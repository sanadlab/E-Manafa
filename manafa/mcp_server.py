"""MCP server of E-MANAFA: an LLM client can record sessions and read their results.

    pip install "manafa[mcp]"
    python -m manafa.mcp_server [results directory]

The results directory (default: emanafa_ondevice, or the EMANAFA_RESULTS_DIR variable) has the
sessions that `emanafa ondevice export` copied from a device. The tool `record_session` adds
new sessions to it.

Each tool is a function of this module, so it can also be used with no MCP client.
"""
import contextlib
import functools
import glob
import json
import os
import sys

from manafa import ondevice

RESULTS_DIR = os.environ.get("EMANAFA_RESULTS_DIR", ondevice.DEFAULT_RESULTS_DIR)
MAX_ROWS = 100
TOOLS = []


def tool(function):
    """Marks a function as an MCP tool. E-MANAFA prints its log on stdout, which is the
    channel of the MCP protocol, so the log goes to stderr while the tool runs."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(sys.stderr):
            return function(*args, **kwargs)
    TOOLS.append(wrapper)
    return wrapper


def find_session(session=""):
    """Session with the run id `session`. The most recent session if `session` is empty."""
    sessions = ondevice.read_sessions(RESULTS_DIR)
    if not sessions:
        raise ValueError(f"no recorded session in {RESULTS_DIR}. Use record_session, or `emanafa ondevice export`")
    if not session:
        return sessions[-1]
    for candidate in sessions:
        if candidate['run'] == str(session):
            return candidate
    raise ValueError(f"no session {session}. Sessions: {', '.join(s['run'] for s in sessions)}")


def results_file(session):
    return os.path.join(RESULTS_DIR, f"emanafa_{session['mode']}_{session['run']}.json")


def load_results(session=""):
    """Results of a session. Analyses the session first if it has no results file."""
    session = find_session(session)
    path = results_file(session)
    if not os.path.exists(path):
        ondevice.analyze_session(session, path)
    if not os.path.exists(path):
        raise ValueError(f"session {session['run']} gave no results")
    with open(path) as f:
        results = json.load(f)
    if 'global' in results:  # legacy model
        legacy = results['global']
        results = {'mode': 'legacy', 'app': session.get('app') or None,
                   'energy': {'source': 'power profile', 'total': legacy['total_energy:'], 'measured_duration_seconds': legacy['elapsed_time'],
                              'by_component': legacy['per_component_consumption']},
                   'method_invocations': {'methods': results.get('invoked_methods', {})}}
    results['session'] = session['run']
    return results


def energy_of(results):
    if not results.get('energy'):
        raise ValueError(f"session {results['session']} has no energy data (mode {results.get('mode')})")
    return results['energy']


@tool
def list_sessions() -> list:
    """Lists the recorded sessions: run id, mode, app, duration and the device model."""
    return [{'session': s['run'], 'mode': s['mode'], 'app': s.get('app') or None,
             'trace_methods': s.get('methods', 'none'),
             'duration_seconds': int(s.get('stop', 0)) - int(s.get('start', 0)),
             'device': s.get('model'), 'analysed': os.path.exists(results_file(s))}
            for s in ondevice.read_sessions(RESULTS_DIR)]


@tool
def get_session_summary(session: str = "") -> dict:
    """Main results of a session: total energy (J), mean power (W), battery drain, and what
    other data the session has. Empty session = the most recent one."""
    results = load_results(session)
    summary = {key: results.get(key) for key in ('session', 'mode', 'app', 'duration_seconds')}
    energy = results.get('energy')
    if energy:
        duration = energy.get('measured_duration_seconds') or 0
        summary.update({'energy_joules': energy['total'], 'measured_duration_seconds': duration,
                        'mean_power_watts': energy['total'] / duration if duration else None,
                        'source': energy.get('source', 'power rails' if 'by_rail' in energy else 'power profile'),
                        'cpu_joules_by_source': energy.get('cpu_joules_by_source')})
        if energy.get('app_process'):
            summary['app_cpu_energy_joules'] = energy['app_process']['energy_joules']
    if results.get('battery_drain'):
        summary['battery_drain_percent'] = results['battery_drain']['battery_drain_percentage']
    summary['has'] = [name for name, present in (
        ('power_rails', bool(energy and energy.get('by_rail'))),
        ('process_energy', bool(energy and energy.get('by_process'))),
        ('memory', bool(results.get('memory'))),
        ('methods', bool(results.get('method_invocations', {}).get('methods'))),
        ('sampled_functions', bool(results.get('sampled_functions')))) if present]
    return summary


@tool
def get_power_rails(session: str = "") -> dict:
    """Energy (J) and mean power (mW) of each hardware power rail of a session, the highest
    first. With the legacy model: the estimate of each component."""
    energy = energy_of(load_results(session))
    duration = energy.get('measured_duration_seconds') or 0
    rails = energy.get('by_rail') or energy.get('by_component')
    return {'total_joules': energy['total'], 'measured_duration_seconds': duration,
            'rails': [{'rail': name, 'joules': joules, 'mean_milliwatts': joules / duration * 1000 if duration else None}
                      for name, joules in sorted(rails.items(), key=lambda item: item[1], reverse=True)]}


@tool
def get_process_energy(session: str = "", top: int = 15) -> dict:
    """Energy of the CPU rails for each process of a session (J), from the scheduler events.
    Needs a session of the energy mode."""
    energy = energy_of(load_results(session))
    if not energy.get('by_process'):
        raise ValueError("the session has no scheduler events. Record it in energy mode")
    return {'cpu_rails_joules': sum(energy['by_rail'][rail] for rail in energy['method_rails']),
            'unattributed_joules': energy.get('unattributed_cpu_joules'),
            'processes': energy['by_process'][:top]}


@tool
def get_app_threads(session: str = "") -> dict:
    """Energy of the CPU rails for each thread of the profiled app (J). Needs a session of the
    energy mode that was recorded with an app."""
    energy = energy_of(load_results(session))
    if not energy.get('app_process'):
        raise ValueError("the session has no app, or no scheduler events of the app")
    return energy['app_process']


@tool
def get_memory(session: str = "") -> dict:
    """System memory counters of a session (minimum, mean, maximum, in MB). Needs the memory
    or both mode."""
    results = load_results(session)
    if not results.get('memory'):
        raise ValueError(f"session {results['session']} has no memory data (mode {results.get('mode')})")
    return results['memory']


@tool
def get_hot_methods(session: str = "", top: int = 20, app_only: bool = True) -> dict:
    """Methods with the most energy in a session, the highest first. Needs method tracing
    (trace_methods am).

    With power rails: `joules` is the energy of the code of the method itself, not of the
    methods that it calls, and `scope` says if it is the energy of the app or of all the
    processes. With the legacy model: `joules` is the estimate for the time of the calls,
    and includes the callees.

    Args:
        app_only: only the methods of the package of the app.
    """
    results = load_results(session)
    energy = results.get('energy') or {}
    if 'by_rail' in energy:
        from manafa.sunburst import method_own_energy
        with open(results_file(find_session(results['session']))) as f:
            ranked = method_own_energy(json.load(f))
        includes_callees = False
    else:
        ranked = []
        for name, calls in (results.get('method_invocations', {}).get('methods') or {}).items():
            ranked.append({'method': name.rsplit('_', 1)[0], 'calls': len(calls),
                           'joules': sum(call.get('consumption', 0.0) for call in calls.values()),
                           'seconds': sum(call.get('end_time', 0.0) - call.get('begin_time', 0.0) for call in calls.values())})
        if not ranked:
            raise ValueError(f"session {results['session']} has no method trace")
        ranked.sort(key=lambda entry: entry['joules'], reverse=True)
        includes_callees = True
    if app_only and results.get('app'):
        ranked = [entry for entry in ranked if entry['method'].startswith(results['app'])]
    for entry in ranked:
        entry['mean_joules_per_call'] = entry['joules'] / entry['calls']
    return {'scope': energy.get('method_energy_scope', 'all processes'), 'joules_include_callees': includes_callees,
            'distinct_methods': len(ranked), 'methods': ranked[:top]}


@tool
def get_sampled_functions(session: str = "", top: int = 20) -> dict:
    """Functions in the call-stack samples of the app (trace_methods perf), with the energy of
    their samples. Mostly native functions."""
    results = load_results(session)
    sampled = results.get('sampled_functions')
    if not sampled:
        raise ValueError(f"session {results['session']} has no call-stack samples")
    return dict(sampled, functions=sampled['functions'][:top])


@tool
def compare_sessions(session_a: str, session_b: str) -> dict:
    """Compares the energy of two sessions: totals, mean power, and the rails with the largest
    difference of mean power (b - a)."""
    a, b = energy_of(load_results(session_a)), energy_of(load_results(session_b))

    def power(energy, joules):
        duration = energy.get('measured_duration_seconds') or 0
        return joules / duration * 1000 if duration else 0.0
    rails_a, rails_b = (e.get('by_rail') or e.get('by_component') or {} for e in (a, b))
    rails = [{'rail': name, 'a_milliwatts': power(a, rails_a.get(name, 0.0)), 'b_milliwatts': power(b, rails_b.get(name, 0.0))}
             for name in sorted(set(rails_a) | set(rails_b))]
    for rail in rails:
        rail['difference_milliwatts'] = rail['b_milliwatts'] - rail['a_milliwatts']
    rails.sort(key=lambda rail: abs(rail['difference_milliwatts']), reverse=True)
    return {'a': {'session': str(session_a), 'joules': a['total'], 'mean_milliwatts': power(a, a['total'])},
            'b': {'session': str(session_b), 'joules': b['total'], 'mean_milliwatts': power(b, b['total'])},
            'difference_milliwatts': power(b, b['total']) - power(a, a['total']),
            'rails': rails[:10]}


@tool
def query_trace(session: str, sql: str) -> list:
    """Runs a PerfettoSQL query on the Perfetto trace of a session, for questions that the
    other tools do not answer. Returns at most 100 rows."""
    from perfetto.trace_processor import TraceProcessor
    trace = ondevice.session_file(find_session(session), "perfetto/trace-{run}-*")
    if trace is None:
        raise ValueError(f"session {session} has no Perfetto trace")
    tp = TraceProcessor(trace=trace)
    try:
        rows = []
        for row in tp.query(sql):
            rows.append(dict(row.__dict__))
            if len(rows) >= MAX_ROWS:
                break
        return rows
    finally:
        tp.close()


@tool
def build_sunburst(session: str = "") -> str:
    """Writes the sunburst diagram (html) of the method energy of a session. Returns the path
    of the file. Needs a session of the energy mode with method tracing (trace_methods am)."""
    from manafa.sunburst import build_sunburst as build
    found = find_session(session)
    load_results(found['run'])
    with open(results_file(found)) as f:
        results = json.load(f)
    out = os.path.join(RESULTS_DIR, f"emanafa_sunburst_{found['run']}.html")
    build(results, out)
    return os.path.realpath(out)


@tool
def device_status() -> dict:
    """Connected Android devices, and the state of the E-MANAFA recorder on the device."""
    from manafa.utils.Utils import execute_shell_command
    _, out, _ = execute_shell_command("adb devices")
    devices = [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith("device")]
    status = None
    if devices:
        res, out, _ = execute_shell_command(f"adb shell sh {ondevice.DEVICE_DIR}/{ondevice.SCRIPT} status")
        status = out.strip() if res == 0 else "recorder not installed"
    return {'devices': devices, 'recorder': status}


@tool
def record_session(seconds: int = 20, mode: str = "energy", app: str = "", trace_methods: str = "") -> dict:
    """Records a new session on the connected device and returns its summary.

    Args:
        seconds: time to record (1 to 600).
        mode: energy (power rails), memory, both, or legacy (power profile estimate).
        app: package of an app to profile. Empty: all the device.
        trace_methods: am (method trace; the app starts again), perf (call-stack samples) or
            none. Default: am if app is given.
    """
    import time
    if mode not in ('energy', 'memory', 'both', 'legacy'):
        raise ValueError("mode must be energy, memory, both or legacy")
    trace_methods = trace_methods or ('am' if app else 'none')
    if trace_methods not in ('none', 'am', 'perf') or (trace_methods != 'none' and not app):
        raise ValueError("trace_methods must be none, am or perf, and am and perf need an app")
    if not 1 <= seconds <= 600:
        raise ValueError("seconds must be from 1 to 600")
    if any(not (c.isalnum() or c in "._") for c in app):
        raise ValueError("app must be a package name")
    ondevice.install()
    ondevice.recorder(f"start {mode} {app} {trace_methods if app else ''}".strip())
    try:
        time.sleep(seconds)
    finally:
        ondevice.recorder("stop")
    ondevice.export(RESULTS_DIR)
    return get_session_summary()


def build_server():
    """MCP server with all the tools. Needs the `mcp` package."""
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1
    server = MCPServer("e-manafa")
    try:
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        from mcp.server.fastmcp.exceptions import ToolError

    def with_messages(function):
        # the client gets the message of a ToolError, but not of other exceptions
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except ValueError as e:
                raise ToolError(str(e)) from e
        return wrapper
    for function in TOOLS:
        server.tool()(with_messages(function))
    return server


def main():
    global RESULTS_DIR
    if len(sys.argv) > 1:
        RESULTS_DIR = sys.argv[1]
    build_server().run()


if __name__ == "__main__":
    main()
