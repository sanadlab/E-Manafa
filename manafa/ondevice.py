"""On-device mode: the device records a session with no computer, and E-MANAFA analyses it later.

    emanafa ondevice install            put the recorder and the Perfetto configs on the device
    emanafa ondevice start [-pm MODE] [-a APP] [--trace-methods am|perf]
    emanafa ondevice stop
    emanafa ondevice export [DIR]       copy the recorded sessions to the computer
    emanafa ondevice analyze [DIR]      results of each recorded session, as in the normal mode
    emanafa ondevice run -s SECONDS     all the steps in sequence
    emanafa ondevice status | clean

After `install`, `start` and `stop` can also be given in a shell on the device, with the
USB cable disconnected:  sh /data/local/tmp/emanafa/manafa.sh start energy

The recorder (resources/ondevice/manafa.sh) uses the same Perfetto configs and does the same
steps as the E-MANAFA services, so the two modes give the same kind of results.
"""
import argparse
import glob
import os
import shutil
import tempfile
import time

from manafa.utils.Logger import log, LogSeverity
from manafa.utils.Utils import execute_shell_command, get_resources_dir

DEVICE_DIR = "/data/local/tmp/emanafa"
SCRIPT = "manafa.sh"
CONFIGS = {'energy': 'perfetto_config_power_rails.pbtxt', 'memory': 'perfetto_config_memory_only.pbtxt',
           'both': 'perfetto_config_both.pbtxt'}
LEGACY_CONFIG = 'perfetto.config.bin'
DEFAULT_RESULTS_DIR = "emanafa_ondevice"


def device_files(meminfo_period_ms=None, battery_poll_ms=None, energy_consumers=True):
    """Files that `install` puts on the device.

    Args:
        energy_consumers: False for a device whose Perfetto does not know the option that
            collects the energy consumers (before Android 12).

    Returns:
        dict: file name on the device -> content (bytes).
    """
    from manafa.services.perfettoServiceEnhanced import render_config, DEFAULT_MEMINFO_PERIOD_MS, \
        DEFAULT_BATTERY_POLL_MS
    periods = (meminfo_period_ms or DEFAULT_MEMINFO_PERIOD_MS, battery_poll_ms or DEFAULT_BATTERY_POLL_MS)
    resources = get_resources_dir()
    files = {}
    with open(os.path.join(resources, 'ondevice', SCRIPT), 'rb') as f:
        files[SCRIPT] = f.read()
    with open(os.path.join(resources, LEGACY_CONFIG), 'rb') as f:
        files['config_legacy.bin'] = f.read()
    for mode, cfg_file in CONFIGS.items():
        files[f'config_{mode}.pbtxt'] = render_config(cfg_file, *periods, energy_consumers=energy_consumers).encode()
    # the recorder replaces __APP__ with the package of the app
    files['config_energy_perf.pbtxt'] = render_config(CONFIGS['energy'], *periods, callstack_package='__APP__',
                                                      energy_consumers=energy_consumers).encode()
    return files


def adb(command):
    res, out, err = execute_shell_command(f"adb {command}")
    if res != 0:
        raise Exception(f"adb {command} failed: {err.strip() or out.strip()}")
    return out


def install(meminfo_period_ms=None, battery_poll_ms=None):
    """Puts the recorder and the Perfetto configs on the connected device."""
    from manafa.services.perfettoServiceEnhanced import device_supports_power_rails, \
        device_has_energy_consumers_option
    files = device_files(meminfo_period_ms, battery_poll_ms, device_has_energy_consumers_option())
    files['power_rails'] = b"1" if device_supports_power_rails() else b"0"
    tmp = tempfile.mkdtemp(prefix='emanafa_ondevice_')
    try:
        adb(f"shell mkdir -p {DEVICE_DIR}")
        for name, content in files.items():
            with open(os.path.join(tmp, name), 'wb') as f:
                f.write(content)
            adb(f"push {os.path.join(tmp, name)} {DEVICE_DIR}/{name}")
    finally:
        shutil.rmtree(tmp)
    log(f"Recorder installed in {DEVICE_DIR}. On the device: sh {DEVICE_DIR}/{SCRIPT} start", log_sev=LogSeverity.SUCCESS)


def recorder(command):
    """Runs a command of the recorder on the connected device. Returns its output."""
    res, out, err = execute_shell_command(f"adb shell sh {DEVICE_DIR}/{SCRIPT} {command}")
    print((out + err).strip())
    if res != 0:
        raise Exception(f"the recorder command '{command}' failed")
    return out


def export(directory=DEFAULT_RESULTS_DIR):
    """Copies the recorded sessions from the device to directory."""
    os.makedirs(directory, exist_ok=True)
    adb(f"pull {DEVICE_DIR}/results/. {directory}")
    sessions = read_sessions(directory)
    log(f"{len(sessions)} session(s) in {directory}", log_sev=LogSeverity.INFO)
    return directory


def read_sessions(directory):
    """Sessions of a results directory, the oldest first.

    Returns:
        list of dict: the key=value lines of each session-<run>.txt, plus 'directory'.
    """
    sessions = []
    for path in sorted(glob.glob(os.path.join(directory, "session-*.txt"))):
        session = {'directory': directory}
        with open(path) as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep:
                    session[key] = value
        if 'run' in session:
            sessions.append(session)
    return sessions


def session_file(session, pattern):
    """Path of the file of a session that matches pattern ({run} is replaced), or None."""
    matches = sorted(glob.glob(os.path.join(session['directory'], pattern.format(run=session['run']))))
    return matches[0] if matches else None


def session_power_profile(session):
    """Power profile xml of a session, made from the saved `dumpsys batterystats --power-profile`."""
    from manafa.parsing.perfetto.perfettoParser import parse_dumpsys_output, generate_power_profile_xml
    text_file = session_file(session, "power_profile-{run}.txt")
    if text_file is None or os.path.getsize(text_file) == 0:
        return None
    with open(text_file) as f:
        text = f.read()
    if 'unknown option' in text.lower():
        return None
    xml_file = text_file[:-len(".txt")] + ".xml"
    generate_power_profile_xml(parse_dumpsys_output(text), xml_file)
    return xml_file


def session_battery(session):
    """BatteryDrainCalculator with the battery properties that the session saved. It does not use adb."""
    from manafa.utils.BatteryDrainCalculator import BatteryDrainCalculator
    calculator = BatteryDrainCalculator()
    calculator.get_battery_properties = lambda: None
    battery_file = session_file(session, "battery-{run}.txt")
    if battery_file is not None:
        with open(battery_file) as f:
            calculator.properties = calculator.parse_battery_properties(f.read(), session.get('charge_full', ''))
    return calculator


def analyze_session(session, output_file=None, output_format='json', power_profile=None, sunburst=None):
    """Computes, prints and saves the results of one recorded session.

    Returns:
        the E-MANAFA object with the parsed session.
    """
    from manafa import main as manafa_main
    from manafa.am_emanafa import AMEManafa
    from manafa.emanafa import EManafa, DEFAULT_PROFILE
    from manafa.services.AmProfilerService import convert_to_csv
    from manafa.services.perfettoService import convert_to_systrace
    from manafa.utils.dateUtils import is_known_timezone

    mode, app, methods = session['mode'], session.get('app') or None, session.get('methods', 'none')
    bts_file = session_file(session, "batterystats/bstats-{run}-*.log")
    trace = session_file(session, "perfetto/trace-{run}-*")
    if bts_file is None or trace is None:
        raise Exception(f"session {session['run']} has no batterystats file or no Perfetto trace")
    profile = power_profile or session_power_profile(session) or DEFAULT_PROFILE
    timezone = session.get('timezone')
    if not is_known_timezone(timezone or ''):
        timezone = session.get('utc_offset') if is_known_timezone(session.get('utc_offset', '')) else 'UTC'

    if methods == 'am':
        manafa = AMEManafa(app_package_name=app, power_profile=profile, timezone=timezone)
        if session.get('clock_epoch') and session.get('clock_monotonic'):
            manafa.recorded_clock_offset = float(session['clock_epoch']) - float(session['clock_monotonic'])
    else:
        manafa = EManafa(power_profile=profile, timezone=timezone)
        manafa.app = app
    manafa.profiler_mode = mode
    if mode == 'legacy' and not trace.endswith('.systrace'):
        trace = convert_to_systrace(trace)
    am_traces = [path for path in glob.glob(os.path.join(session['directory'], "am", f"app_*_{session.get('start')}_*.trace"))
                 if not path.endswith("_exec.trace")]
    if methods == 'am' and am_traces:
        manafa.parse_results(bts_file, trace, convert_to_csv(am_traces[0], os.path.dirname(am_traces[0])))
    else:
        manafa.parse_results(bts_file, trace)

    duration = int(session.get('stop', 0)) - int(session.get('start', 0))
    args = argparse.Namespace(profile_mode=mode, app_package=app, time_in_secs=duration, trace_methods=methods,
                              output_file=output_file, output_format=output_format, sunburst=sunburst)
    print(f"\nSession {session['run']}: mode {mode}, {duration} s" + (f", app {app}" if app else ""))
    if manafa.uses_power_rails(trace):
        manafa_main.report_rails_session(args, manafa, session_battery(session))
    else:
        manafa_main.report_legacy_session(args, manafa, session_battery(session))
    return manafa


def analyze(directory=DEFAULT_RESULTS_DIR, output_format='json', power_profile=None, sunburst=False, runs=None):
    """Analyses the sessions of a results directory. Each session gets a results file in directory.

    Args:
        runs: run ids of the sessions to analyse. Default: all the sessions.
    """
    sessions = [session for session in read_sessions(directory) if runs is None or session['run'] in runs]
    if not sessions:
        raise Exception(f"no recorded session (session-*.txt) in {directory}")
    for session in sessions:
        out = os.path.join(directory, f"emanafa_{session['mode']}_{session['run']}.{output_format}")
        html = os.path.join(directory, f"emanafa_sunburst_{session['run']}.html") if sunburst else None
        try:
            analyze_session(session, out, output_format, power_profile, html)
        except Exception as e:
            log(f"Session {session['run']} could not be analysed: {e}", log_sev=LogSeverity.ERROR)
    return sessions


def main(argv):
    parser = argparse.ArgumentParser(prog="emanafa ondevice", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=['install', 'start', 'stop', 'status', 'clean', 'export', 'analyze', 'run'])
    parser.add_argument("directory", nargs='?', default=DEFAULT_RESULTS_DIR,
                        help=f"results directory on the computer, for export, analyze and run (default: {DEFAULT_RESULTS_DIR})")
    parser.add_argument("-pm", "--profile-mode", choices=['legacy', 'energy', 'memory', 'both'], default='energy')
    parser.add_argument("-a", "--app_package", default=None, help="package of the app to profile")
    parser.add_argument("--trace-methods", choices=['none', 'am', 'perf'], default=None,
                        help="default: am if -a is given, otherwise none")
    parser.add_argument("-s", "--time_in_secs", type=int, default=0, help="run: time to profile")
    parser.add_argument("-cmd", "--command-to-profile", dest="workload", default=None, help="run: command to profile")
    parser.add_argument("-p", "--profile", default=None, help="analyze: power profile file for the legacy model")
    parser.add_argument("-of", "--output-format", choices=['json', 'csv'], default='json')
    parser.add_argument("--sunburst", action='store_true', help="analyze: write the sunburst diagram of each session")
    parser.add_argument("--meminfo-period-ms", type=int, default=None)
    parser.add_argument("--battery-poll-ms", type=int, default=None)
    args = parser.parse_args(argv)
    if args.trace_methods is None:
        args.trace_methods = 'am' if args.app_package else 'none'
    if args.trace_methods != 'none' and args.app_package is None:
        parser.error(f"--trace-methods={args.trace_methods} requires -a/--app_package")
    start = f"start {args.profile_mode} {args.app_package or ''} {args.trace_methods if args.app_package else ''}".strip()

    if args.command == 'install':
        install(args.meminfo_period_ms, args.battery_poll_ms)
    elif args.command == 'start':
        recorder(start)
    elif args.command in ('stop', 'status', 'clean'):
        recorder(args.command)
    elif args.command == 'export':
        export(args.directory)
    elif args.command == 'analyze':
        analyze(args.directory, args.output_format, args.profile, args.sunburst)
    elif args.command == 'run':
        if args.time_in_secs <= 0 and args.workload is None:
            parser.error("run needs -s SECONDS or -cmd COMMAND")
        install(args.meminfo_period_ms, args.battery_poll_ms)
        recorder(start)
        try:
            if args.workload is not None:
                os.system(args.workload)
            else:
                time.sleep(args.time_in_secs)
        finally:
            out = recorder("stop")
        export(args.directory)
        # the device can have older sessions. analyse only the new one
        analyze(args.directory, args.output_format, args.profile, args.sunburst,
                runs=[word for word in out.split() if word.isdigit()])
