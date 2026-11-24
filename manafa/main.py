import glob
import shutil
import sys, os, time, json
import argparse

from manafa.am_emanafa import AMEManafa
from manafa.services.perfettoService import convert_to_systrace
from manafa.services.perfettoServiceEnhanced import PerfettoServiceEnhanced
from manafa.utils.Utils import execute_shell_command, mega_find, get_resources_dir
from manafa.emanafa import EManafa
from manafa.hunter_emanafa import HunterEManafa
from manafa.utils.Logger import log, LogSeverity
from manafa.utils.BatteryDrainCalculator import BatteryDrainCalculator

MANAFA_RESOURCES_DIR = get_resources_dir()
MAX_SIZE = sys.maxsize
MANAFA_INSPECTOR_URL = "https://greensoftwarelab.github.io/manafa-inspector/"


def validate_start():
    res, o, e = execute_shell_command("adb shell getprop ro.build.version.release")
    is_above_android_8 = res == 0 and int(o.split(".")[0]) >= 9
    if not is_above_android_8:
        raise Exception("Unable to run E-Manafa on devices with version < Android 9")


def has_connected_devices():
    """checks if there are devices connected via adb"""
    res, o, e = execute_shell_command("adb devices -l | grep -v attached")
    return res == 0 and len(o) > 2


def export_to_json(data, filepath):
    """Export profiling data to JSON file."""
    with open(filepath, 'w') as f:
        # a session with method tracing has many thousands of calls: no indentation, to keep the file small
        json.dump(data, f, indent=None if 'method_invocations' in data else 2)
    log(f"Detailed results exported to: {filepath}", log_sev=LogSeverity.INFO)


def export_to_csv(data, filepath):
    """Export profiling data to CSV file."""
    import csv
    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)

        if 'energy' in data:
            writer.writerow(['ENERGY PROFILING RESULTS'])
            writer.writerow(['Power Rail', 'Energy (Joules)'])
            writer.writerow(['TOTAL', f"{data['energy']['total']:.2f}"])
            writer.writerow([])
            writer.writerow(['Individual Rails:'])
            for rail, energy in sorted(data['energy']['by_rail'].items(), key=lambda x: x[1], reverse=True):
                writer.writerow([rail, f"{energy:.2f}"])
            writer.writerow([])

        if 'memory' in data:
            writer.writerow(['MEMORY PROFILING RESULTS'])
            writer.writerow(['Counter', 'Min (MB)', 'Avg (MB)', 'Max (MB)', 'Samples'])
            for counter in ['MemTotal', 'MemFree', 'MemAvailable', 'Buffers', 'Cached', 'Active', 'Inactive']:
                if counter in data['memory']:
                    stats = data['memory'][counter]
                    writer.writerow([
                        counter,
                        f"{stats['min_mb']:.2f}",
                        f"{stats['avg_mb']:.2f}",
                        f"{stats['max_mb']:.2f}",
                        stats['samples']
                    ])
            writer.writerow([])

        if 'battery_drain' in data:
            writer.writerow(['BATTERY DRAIN ANALYSIS'])
            drain = data['battery_drain']
            writer.writerow(['Metric', 'Value'])
            writer.writerow(['Full Capacity (mAh)', f"{drain['full_capacity_mah']:.2f}"])
            writer.writerow(['Current Voltage (V)', f"{drain['current_voltage_v']:.3f}"])
            if 'temperature_c' in drain:
                writer.writerow(['Temperature (°C)', f"{drain['temperature_c']:.1f}"])
            if 'battery_level_percent' in drain:
                writer.writerow(['Battery Level (%)', drain['battery_level_percent']])
            writer.writerow(['Total Battery Energy (Wh)', f"{drain['total_battery_energy_wh']:.3f}"])
            writer.writerow(['Consumed Energy (J)', f"{drain['consumed_energy_joules']:.2f}"])
            writer.writerow(['Consumed Energy (Wh)', f"{drain['consumed_energy_wh']:.6f}"])
            writer.writerow(['Battery Drain (%)', f"{drain['battery_drain_percentage']:.6f}"])

    log(f"Detailed results exported to: {filepath}", log_sev=LogSeverity.INFO)


def display_new_profiler_results(emanafa, profile_mode, battery_drain_info=None):
    """Display results from new enhanced profiler."""
    print("\n" + "="*70)
    print("PROFILING RESULTS")
    print("="*70)

    total_energy = 0
    if profile_mode in ['energy', 'both']:
        if hasattr(emanafa, 'power_rails_energy') and emanafa.power_rails_energy:
            total_energy = emanafa.power_rails_energy['total']
            print(f"\n ENERGY CONSUMPTION:")
            print(f"  Total: {total_energy:.2f} Joules ({total_energy/3600:.4f} Wh)")

            #show top 5 rails
            rails = emanafa.power_rails_energy.get('by_rail', {})
            if rails:
                sorted_rails = sorted(rails.items(), key=lambda x: x[1], reverse=True)[:5]
                print(f"\n  Top Power Rail Consumers:")
                for rail, energy in sorted_rails:
                    print(f"    {rail}: {energy:.2f} J")

            processes = emanafa.power_rails_energy.get('by_process')
            if processes:
                print(f"\n  Top Processes (energy of the CPU rails):")
                for process in processes[:5]:
                    print(f"    {process['process']}: {process['energy_joules']:.2f} J "
                          f"({process['cpu_seconds']:.1f} s on a CPU)")
            app = emanafa.power_rails_energy.get('app_process')
            if app:
                print(f"\n  App {app['process']}: {app['energy_joules']:.2f} J of the CPU rails")
        else:
            print("\n No energy data available")

    if profile_mode in ['memory', 'both']:
        if hasattr(emanafa, 'memory_stats') and emanafa.memory_stats:
            mem = emanafa.memory_stats
            print(f"\nSYSTEM MEMORY STATISTICS:")

            #show Total RAM
            if 'MemTotal' in mem:
                total_ram = mem['MemTotal']['avg_mb']
                print(f"  Total RAM: {total_ram:.2f} MB ({total_ram/1024:.2f} GB)")

            #calculate and show Memory Used (Total - Available)
            if 'MemTotal' in mem and 'MemAvailable' in mem:
                total = mem['MemTotal']['avg_mb']
                avail = mem['MemAvailable']

                used_min = total - avail['max_mb']  #when available is max, used is min
                used_avg = total - avail['avg_mb']
                used_max = total - avail['min_mb']  #when available is min, used is max

                print(f"\n  Memory Used:")
                print(f"    Min: {used_min:.2f} MB  |  Avg: {used_avg:.2f} MB  |  Max: {used_max:.2f} MB")

            print(f"\n  (Detailed breakdown of all counters saved to JSON file)")
        else:
            print("\n  No memory data available")

    print("="*70)

    #print battery drain information if available and we have energy data
    if battery_drain_info and total_energy > 0:
        calculator = BatteryDrainCalculator()
        print(calculator.format_battery_drain_report(battery_drain_info))


def _resolve_profiler_mode(args):
    """Pick the profiler_mode that matches the CLI flags."""
    if getattr(args, 'force_legacy', False):
        return 'legacy'
    if hasattr(args, 'profile_mode') and args.profile_mode:
        return args.profile_mode
    return None


def _apply_sampling(manafa, args):
    """Propagate Perfetto sampling flags onto the manafa instance."""
    if hasattr(args, 'meminfo_period_ms') and args.meminfo_period_ms is not None:
        manafa.meminfo_period_ms = args.meminfo_period_ms
    if hasattr(args, 'battery_poll_ms') and args.battery_poll_ms is not None:
        manafa.battery_poll_ms = args.battery_poll_ms


def create_manafa(args):
    profiler_mode = _resolve_profiler_mode(args)
    trace_methods = getattr(args, 'trace_methods', 'none') or 'none'

    #parsing-mode shortcut: -htf <file> implies hunter post-processing
    if args.hunterfile is not None and trace_methods == 'none':
        trace_methods = 'hunter'

    if trace_methods == 'hunter':
        log("Using HunterEManafa (logcat-based method tracing)", log_sev=LogSeverity.INFO)
        manafa = HunterEManafa(power_profile=args.profile, timezone=args.timezone,
                               resources_dir=MANAFA_RESOURCES_DIR)
    elif trace_methods == 'am':
        log("Using AMEManafa with app package %s (mode=%s)" % (
            args.app_package, profiler_mode or 'energy'), log_sev=LogSeverity.INFO)
        manafa = AMEManafa(app_package_name=args.app_package, power_profile=args.profile,
                           timezone=args.timezone, resources_dir=MANAFA_RESOURCES_DIR)
    else:
        #plain energy/memory profiling, no method tracing
        manafa = EManafa(power_profile=args.profile, timezone=args.timezone,
                         resources_dir=MANAFA_RESOURCES_DIR)
        if args.app_package is not None:
            manafa.app = args.app_package
            if trace_methods == 'perf':
                manafa.callstack_package = args.app_package

    if profiler_mode:
        manafa.profiler_mode = profiler_mode
    _apply_sampling(manafa, args)
    return manafa


def parse_results(args, manafa):
    if args.directory and glob.glob(os.path.join(args.directory, "session-*.txt")):
        # sessions that the device recorded (emanafa ondevice)
        from manafa import ondevice
        ondevice.analyze(args.directory, args.output_format, args.profile, bool(args.sunburst))
    elif args.directory:
        bstats_files = mega_find(args.directory, pattern="bstats-*", maxdepth=2, type_file='f')
        for b_file in bstats_files:
            b_file_id = os.path.basename(b_file).split("-")[1]
            matching_pft_files = [x for x in mega_find(args.directory, pattern="trace-*") if b_file_id in x]
            if len(matching_pft_files) == 0:
                print(" unmatched batstats file")
                continue

            is_converted = matching_pft_files[0].endswith('.systrace')
            if not is_converted:
                matching_pft_files[0] = convert_to_systrace(matching_pft_files[0])
            matching_ht_files = [x for x in mega_find(args.directory, pattern="method_traces-*") if b_file_id in x]
            if len(matching_ht_files) > 0:
                _, fc = manafa.parse_results(bts_file=b_file, pf_file=matching_pft_files[0], htr_file=matching_ht_files[0])
                if fc is not None:
                    shutil.copyfile(fc, os.path.basename(fc))
            else:
                manafa.parse_results(b_file, matching_pft_files[0])
            save_legacy_report(args, manafa)

    elif is_rails_trace(args):
        parse_rails_trace(args, manafa)
    elif args.hunterfile:
        manafa.parse_results(args.batstatsfile, args.perfettofile, args.hunterfile)
        manafa.calculate_function_consumption()
        save_legacy_report(args, manafa)
    else:
        manafa.parse_results(args.batstatsfile, args.perfettofile)
        save_legacy_report(args, manafa)


def save_legacy_report(args, manafa):
    begin = manafa.perf_events.events[0].time if len(manafa.perf_events.events) > 1 else manafa.bat_events.events[0].time
    out_file = manafa.save_final_report(begin, output_filepath=args.output_file)
    log(f"Output file: {out_file}. You can inspect it with E-MANAFA Inspector in {MANAFA_INSPECTOR_URL}",
        log_sev=LogSeverity.SUCCESS)


def is_rails_trace(args):
    """True if -pft gives a trace of the energy, memory or both mode, to parse with the power rails."""
    return (args.perfettofile is not None and args.perfettofile.endswith('.perfetto-trace')
            and args.profile_mode != 'legacy')


def parse_rails_trace(args, manafa):
    """Parses a recorded trace of the energy, memory or both mode again."""
    from manafa.parsing.perfettoEnergyCalculator import PowerRailsTrace, calculate_memory_stats
    rails = PowerRailsTrace.from_trace(args.perfettofile)
    manafa.power_rails = rails
    manafa.power_rails_energy = rails.summary() if rails is not None else None
    manafa.attribute_cpu_energy(args.perfettofile, args.app_package)
    manafa.memory_stats = calculate_memory_stats(args.perfettofile)
    results = {'mode': args.profile_mode, 'app': args.app_package, 'timestamp': time.time()}
    if manafa.power_rails_energy:
        results['energy'] = manafa.power_rails_energy
    if manafa.memory_stats:
        results['memory'] = manafa.memory_stats
    if 'energy' not in results and 'memory' not in results:
        log(f"No power rails and no memory data in {args.perfettofile}", log_sev=LogSeverity.ERROR)
        return
    results['mode'] = 'both' if 'energy' in results and 'memory' in results else 'energy' if 'energy' in results else 'memory'
    display_new_profiler_results(manafa, results['mode'])
    if not args.output_file:
        args.output_file = f"emanafa_{results['mode']}_{int(time.time())}.{args.output_format}"
    if args.output_format == 'json':
        export_to_json(results, args.output_file)
    else:
        export_to_csv(results, args.output_file)
    print(f"\nDetailed results saved to: {args.output_file}\n")


def print_profiled_stats(el_time, total_consumption, per_comp_consumption, event_timeline, battery_drain_info=None):
    print("--------------------------------------")
    print(f"Total energy consumed: {total_consumption} Joules")
    print(f"Elapsed time: {el_time} secs")
    print("--------------------------------------")
    print("Per-component consumption")
    print(json.dumps(per_comp_consumption, indent=1))

    #print battery drain information if available
    if battery_drain_info:
        calculator = BatteryDrainCalculator()
        print(calculator.format_battery_drain_report(battery_drain_info))


def preserve_trace_file(results, output_file):
    """Keeps the method trace next to the results file and repoints it there.

    manafa/results is wiped when the next session starts, so a results file
    left pointing into it stops being usable as soon as you profile again.
    """
    invocations = results.get('method_invocations')
    if not isinstance(invocations, dict):
        return
    trace_file = invocations.get('trace_file')
    if not trace_file or not os.path.exists(trace_file):
        return
    kept = f"{os.path.splitext(output_file)[0]}_calls{os.path.splitext(trace_file)[1]}"
    try:
        shutil.copyfile(trace_file, kept)
    except OSError as e:
        log(f"Could not keep a copy of {trace_file}: {e}", log_sev=LogSeverity.WARNING)
        return
    invocations['trace_file'] = os.path.abspath(kept)
    log(f"Call trace kept alongside results: {kept}", log_sev=LogSeverity.INFO)


def generate_sunburst(args, results):
    """Writes the call-graph sunburst for a finished session, if it can."""
    out = args.sunburst
    if out == 'auto':
        out = f"emanafa_sunburst_{int(time.time())}.html"
    try:
        from manafa.sunburst import build_sunburst
        build_sunburst(results, out)
        print(f"Call graph sunburst saved to: {out}\n")
    except ImportError:
        log("--sunburst needs plotly. Install it with `pip install plotly`.",
            log_sev=LogSeverity.ERROR)
    except ValueError as e:
        log(f"Cannot build sunburst: {e}", log_sev=LogSeverity.ERROR)


def report_legacy_session(args, manafa, battery_calculator):
    """Prints and saves the results of a session of the legacy model."""
    #legacy output
    if len(manafa.perf_events.events) > 1 or len(manafa.bat_events.events) > 0:
        begin = manafa.perf_events.events[0].time if len(manafa.perf_events.events) > 1 else manafa.bat_events.events[0].time
        end = manafa.perf_events.events[-1].time if len(manafa.perf_events.events) > 1 else manafa.bat_events.events[-1].time
        try:
            total, per_c, timeline = manafa.get_consumption_in_between(begin, end)

            # Calculate battery drain
            battery_drain_info = battery_calculator.calculate_battery_drain(total)

            print_profiled_stats(end-begin, total, per_c, timeline, battery_drain_info)
            out_file = manafa.save_final_report(begin, output_filepath=args.output_file)
            log(f"Output file: {out_file}. You can inspect it with E-MANAFA Inspector in {MANAFA_INSPECTOR_URL}",
                log_sev=LogSeverity.SUCCESS)
        except Exception as e:
            log(f"Unable to compute legacy stats: {e}", log_sev=LogSeverity.WARNING)
            log("Tip: increase profiling time (e.g., -s 60) or allow a short warm-up before stopping.", log_sev=LogSeverity.INFO)
    else:
        log("No profiling events captured. Legacy profiler requires perfetto events.", log_sev=LogSeverity.WARNING)
        log("The trace files were saved but could not be parsed.", log_sev=LogSeverity.INFO)
    if args.sunburst:
        log("--sunburst needs the power rails. No diagram is written with the legacy model.",
            log_sev=LogSeverity.WARNING)


def report_rails_session(args, manafa, battery_calculator):
    """Prints and saves the results of a session of the energy, memory or both mode."""
    #new enhanced profiler output
    #calculate battery drain if we have energy data
    battery_drain_info = None
    if args.profile_mode in ['energy', 'both'] and hasattr(manafa, 'power_rails_energy') and manafa.power_rails_energy:
        total_energy = manafa.power_rails_energy.get('total', 0)
        if total_energy > 0:
            battery_drain_info = battery_calculator.calculate_battery_drain(total_energy)

    display_new_profiler_results(manafa, args.profile_mode, battery_drain_info)

    #export detailed results
    results = {
        'mode': args.profile_mode,
        'app': args.app_package,
        'duration_seconds': args.time_in_secs,
        'timestamp': time.time()
    }

    if args.profile_mode in ['energy', 'both'] and hasattr(manafa, 'power_rails_energy'):
        results['energy'] = manafa.power_rails_energy

    if args.profile_mode in ['memory', 'both'] and hasattr(manafa, 'memory_stats'):
        results['memory'] = manafa.memory_stats

    #include AM-profiler method invocations when an app package was supplied
    app_consumptions = getattr(manafa, 'app_consumptions', None)
    if app_consumptions is not None and getattr(app_consumptions, 'app_traces', None):
        #only emit per-method consumption when batterystats produced samples;
        #otherwise emit an invocation count per method (still useful for the user)
        has_consumption = any(
            'per_component_consumption' in inv
            for invs in app_consumptions.app_traces.values()
            for inv in invs.values()
        )
        methods = (app_consumptions.get_elaborate_stats()
                   if has_consumption
                   else {m: len(invs) for m, invs in app_consumptions.app_traces.items()})
        results['method_invocations'] = {
            'total': app_consumptions.get_total_methods(),
            'distinct': app_consumptions.get_diff_methods(),
            'trace_file': getattr(manafa, 'trace_out_file', None),
            'methods': methods,
        }
        print(f"\n  Method invocations: {results['method_invocations']['total']} "
              f"calls across {results['method_invocations']['distinct']} distinct methods")
        if results['method_invocations']['trace_file']:
            print(f"  AM trace file: {results['method_invocations']['trace_file']}")
    elif args.app_package and args.trace_methods == 'perf':
        if getattr(manafa, 'sampled_functions', None):
            results['sampled_functions'] = manafa.sampled_functions
            print(f"\n  Call-stack samples: {manafa.sampled_functions['samples']} "
                  f"({manafa.sampled_functions['samples_with_stack']} with a stack)")
        else:
            log("No call-stack samples of the app. The app must be profileable or debuggable, "
                "and the device must have the linux.perf data source.", log_sev=LogSeverity.WARNING)
    elif args.app_package and args.trace_methods != 'none':
        log("No method invocations captured. Verify the app declares "
            "`<profileable android:shell=\"true\"/>` or `android:debuggable=\"true\"` "
            "and is launchable.", log_sev=LogSeverity.WARNING)

    #add battery drain info to results
    if battery_drain_info:
        results['battery_drain'] = battery_drain_info

    #auto-generate filename if not specified
    has_payload = any(k in results for k in ('energy', 'memory', 'method_invocations'))
    if not args.output_file and has_payload:
        timestamp = int(time.time())
        args.output_file = f"emanafa_{args.profile_mode}_{timestamp}.{args.output_format}"

    #export
    if args.output_file and has_payload:
        preserve_trace_file(results, args.output_file)
        if args.output_format == 'json':
            export_to_json(results, args.output_file)
        else:
            export_to_csv(results, args.output_file)

        print(f"\nDetailed results saved to: {args.output_file}\n")

    if args.sunburst:
        generate_sunburst(args, results)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'ondevice':
        from manafa import ondevice
        return ondevice.main(sys.argv[2:])
    parser = argparse.ArgumentParser(
        description='E-MANAFA: Energy and Memory profiler for Android applications',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Legacy profiler (old method)
  python3 manafa/main.py -a com.android.chrome -s 30 --force-legacy
  
  # New energy profiling (default with power rails)
  python3 manafa/main.py -a com.android.chrome -s 30 -pm energy
  
  # Memory profiling only
  python3 manafa/main.py -a com.android.chrome -s 30 -pm memory
  
  # Export detailed results
  python3 manafa/main.py -a com.android.chrome -s 30 -pm energy -of json -o results.json

  # On-device mode: the device records with no computer (see: emanafa ondevice -h)
  emanafa ondevice run results/ -a com.android.chrome -s 30
        """
    )
    
    # Existing arguments
    parser.add_argument("-ht", "--hunter", help="parse method_traces logs", action='store_true', default=False)
    parser.add_argument("-p", "--profile", help="power profile file", default=None, type=str)
    parser.add_argument("-t", "--timezone", help="device timezone: IANA name (Europe/Lisbon) or UTC offset (+0100)", default=None, type=str)
    parser.add_argument("-pft", "--perfettofile", help="perfetto file", default=None, type=str)
    parser.add_argument("-bts", "--batstatsfile", help="batterystats file", default=None, type=str)
    parser.add_argument("-htf", "--hunterfile", help="method_traces file", default=None, type=str)
    parser.add_argument("-d", "--directory", help="results file directory", default=None, type=str)
    parser.add_argument("-o", "--output_file", help="output file", default=None, type=str)
    parser.add_argument("-s", "--time_in_secs", help="time to profile", default=0, type=int)
    parser.add_argument("-a", "--app_package", help="package of app to profile", default=None, type=str)
    parser.add_argument("-cmd", "--command", help="command to profile", default=None, type=str)

    #new enhanced profiling arguments
    parser.add_argument("-pm", "--profile-mode",
                       choices=['legacy', 'energy', 'memory', 'both'],
                       default='energy',
                       help='Profiling mode: legacy (old profiler), energy (power rails), memory (system memory), or both (default: energy)')

    parser.add_argument("-of", "--output-format",
                       choices=['json', 'csv'],
                       default='json',
                       help='Output format for detailed results (default: json)')

    parser.add_argument("--force-legacy", action='store_true',
                       help='Force use of legacy profiler even if device supports new features')

    parser.add_argument("--meminfo-period-ms", type=int, default=50,
                       help='Polling period for /proc/meminfo via linux.sys_stats (default: 50)')
    parser.add_argument("--battery-poll-ms", type=int, default=250,
                       help='Polling period for android.power battery + power rails counters '
                            '(default: 250). Power rails are cumulative, so this only affects '
                            'temporal granularity, not total-energy accuracy.')

    parser.add_argument("--sunburst", nargs='?', const='auto', default=None,
                       metavar='FILE',
                       help='Write an animated call-graph sunburst (html) coloured by '
                            'energy. Needs method tracing (-a <package>) and plotly. '
                            'Pass a filename or leave empty to auto-name it.')

    parser.add_argument("--trace-methods", choices=['none', 'am', 'hunter', 'perf'], default=None,
                       help="Capture method invocations. 'am' uses ActivityManager profiling "
                            "(requires -a <package>); 'hunter' parses logcat via the Hunter "
                            "instrumentation; 'perf' samples the call stacks of the app with "
                            "linux.perf (requires -a <package> and power rails; gives native "
                            "functions). Defaults: 'am' if -a is given, 'hunter' if -ht "
                            "is given, otherwise 'none'.")
    args = parser.parse_args()

    #resolve --trace-methods default based on the other flags
    if args.trace_methods is None:
        if args.hunter:
            args.trace_methods = 'hunter'
        elif args.app_package is not None:
            args.trace_methods = 'am'
        else:
            args.trace_methods = 'none'

    if args.trace_methods in ('am', 'perf') and args.app_package is None:
        parser.error(f"--trace-methods={args.trace_methods} requires -a/--app_package")
    
    #warnings for new modes
    if args.profile_mode == 'both' and not args.force_legacy:
        log("WARNING: Profiling both energy and memory simultaneously may introduce overhead.", 
            log_sev=LogSeverity.WARNING)
        log("Consider running separate sessions for most accurate results.", log_sev=LogSeverity.WARNING)
    
    if args.force_legacy:
        args.profile_mode = 'legacy'
        log("Using legacy profiler", log_sev=LogSeverity.INFO)
    
    has_device_conn = has_connected_devices()
    invalid_file_args = ((args.perfettofile is None or args.batstatsfile is None) and args.directory is None
                         and not is_rails_trace(args))
    
    if not has_device_conn and invalid_file_args:
        log("Fatal error. No connected devices or result files submitted for analysis", LogSeverity.FATAL)
        exit(-1)
    
    if has_device_conn:
        validate_start()
    manafa = create_manafa(args)
    
    if has_device_conn and invalid_file_args:
        #live profiling mode
        print(f"\n{'='*70}")
        print(f"E-MANAFA Profiling")
        print(f"{'='*70}")
        print(f"Mode: {args.profile_mode.upper()}")
        if args.app_package:
            print(f"App: {args.app_package}")
        print(f"Duration: {args.time_in_secs} seconds" if args.time_in_secs > 0 else "Duration: Manual stop")
        print(f"{'='*70}\n")
        
        manafa.init(clean=True)
        manafa.start()
        log("profiling...")

        if args.command is not None:
            log("executing command to profile: %s" % args.command)
            os.system(args.command)
            log("executed command")
        elif args.time_in_secs == 0:
            input("press any key to stop monitoring")
        else:
            log(f"Profiling for ~{args.time_in_secs} seconds", LogSeverity.INFO)
            time.sleep(args.time_in_secs)
            log("stopping profiler...")
        
        manafa.stop()

        #display results based on profiler type. energy mode falls back to the legacy
        #profiler on devices without power rails, so check the service that ran
        battery_calculator = BatteryDrainCalculator()
        if not isinstance(manafa.perfetto, PerfettoServiceEnhanced):
            report_legacy_session(args, manafa, battery_calculator)
        else:
            report_rails_session(args, manafa, battery_calculator)
    else:
        #file parsing mode (existing functionality)
        parse_results(args, manafa)


if __name__ == '__main__':
    main()