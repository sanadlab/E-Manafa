[![Build Status](https://travis-ci.com/RRua/e-manafa.svg?branch=main)](https://travis-ci.com/RRua/e-manafa)
[![made-with-python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)
[![PyPI version](https://badge.fury.io/py/manafa.svg)](https://badge.fury.io/py/manafa)
[![PyPI license](https://img.shields.io/pypi/l/ansicolortags.svg)](https://pypi.python.org/pypi/manafa)
[![PyPI status](https://img.shields.io/pypi/status/ansicolortags.svg)](https://pypi.python.org/pypi/manafa)
[![DOI](https://zenodo.org/badge/459943164.svg)](https://zenodo.org/badge/latestdoi/459943164)


# E-MANAFA: Energy Monitor and ANAlyzer For Android

E-MANAFA measures the energy and the memory that an Android device consumes while an app runs. It needs only an `adb` connection to the device (USB or Wi-Fi): no root access and no external power meter. It gives:

- the energy of the session, for each hardware component (CPU, display, radios, and others);
- the system memory during the session;
- the energy of each process, and of each thread of the app;
- the energy of each method of the app, and a call-graph diagram (sunburst) of this energy.

The device can also record a session with no computer connected (see [On-device mode](#on-device-mode)), and an LLM client can use E-MANAFA through an [MCP server](#mcp-server).

## How it works

```
        Host (Python + adb)                         Android device
 +-----------------------------+   start    +-------------------------------+
 | 1. init                     | ---------> | Perfetto trace:               |
 |    detect power rails,      |            |   power rails (energy)        |
 |    read the power profile   |            |   /proc/meminfo (memory)      |
 | 2. start                    |            |   CPU frequency and idle      |
 | 3. your workload runs       |            | batterystats history          |
 |    (time, key or command)   |            | method trace of the app       |
 | 4. stop                     | <--------- |   (am profile or Hunter)      |
 +--------------+--------------+ pull files +-------------------------------+
                |
                v
 +-----------------------------+      +-----------------------------------+
 | 5. parse                    |      | 6. results                        |
 |    energy for each rail,    | ---> |    summary in the terminal        |
 |    memory statistics,       |      |    JSON or CSV file               |
 |    energy for each method   |      |    sunburst diagram (HTML)        |
 +-----------------------------+      +-----------------------------------+
```

E-MANAFA has two sources of energy data. It selects one automatically.

| Source | How | Accuracy |
|---|---|---|
| **Power rails** (first choice) | The device has power sensors on its supply rails (Pixel 6 and later, and other recent devices). Perfetto records their energy counters. | Measured by hardware. |
| **Energy consumers** (second choice) | The PowerStats HAL of the device reports the energy of some components (Android 12 and later), for example each CPU cluster. Perfetto records these counters. E-MANAFA uses them if the device has no rails but has consumers for its CPU clusters. | The values of the vendor. The total has only the components that have a consumer. |
| **Legacy model** (last choice) | `batterystats` gives the state of each component. Perfetto gives the CPU frequencies. `power_profile.xml` gives the current of each state. E-MANAFA multiplies them. | An estimate. It is only as good as the power profile of the manufacturer. See [Accuracy](#accuracy-and-limits). |

E-MANAFA selects the first source in this order that the device has. Each result names the source that it used (`energy.source`: `power rails`, `energy consumers` or `power profile`). In the `energy` mode, E-MANAFA also compares the CPU energy of the session between all the sources that the device has (`energy.cpu_joules_by_source`), and gives a warning if two of them disagree by more than a factor of 2.

The power profile of the legacy model comes from the device (`dumpsys batterystats --power-profile`), or from the file that you give with `-p`.

Full documentation: <https://sanadlab.github.io/E-MANAFA/>

## Setup

You need:

- a device with Android 9 or later, with USB debugging or wireless debugging on;
- macOS or Linux, with Python 3.8 or later;
- the [Android platform-tools](https://developer.android.com/studio/releases/platform-tools) (`adb`) in your `PATH`.

Install from PyPI:

```
pip install manafa
```

Or install from the sources:

```
git clone https://github.com/sanadlab/E-Manafa.git
cd E-MANAFA
python -m venv env && source env/bin/activate
pip install -r requirements.txt
```

The sunburst diagram also needs plotly: `pip install "manafa[sunburst]"` or `pip install plotly`.

The examples below use `emanafa`, the command that `pip install manafa` gives. From the sources, use `python3 manafa/main.py`.

## Quick start

```bash
# measure the energy of the device for 30 seconds
emanafa -s 30

# measure an app for 30 seconds, with the energy of each method
emanafa -a com.example.app -s 30
```

E-MANAFA starts the app again when you give `-a`. It prints a summary and writes a JSON file with the details.

## What to measure

Select the measurement with `-pm` (`--profile-mode`):

| Mode | Energy | Memory | Use it when |
|---|---|---|---|
| `energy` (default) | power rails | no | you want energy with the smallest overhead |
| `memory` | no | `/proc/meminfo` | you want memory only |
| `both` | power rails | `/proc/meminfo` | you want the two results from the same run |
| `legacy` | legacy model | no | the device has no power rails |

### Energy only

```bash
emanafa -a com.example.app -s 30 -pm energy
```

### Memory only

```bash
emanafa -a com.example.app -s 30 -pm memory
```

Memory is polled each 50 ms. Change this with `--meminfo-period-ms`.

### Energy and memory, in separate runs (recommended)

Memory polling uses CPU time, and this adds energy. For the most accurate energy result, measure the two in separate runs of the same workload:

```bash
emanafa -a com.example.app -cmd "./run-test.sh" -pm energy -o energy.json
emanafa -a com.example.app -cmd "./run-test.sh" -pm memory -o memory.json
```

### Energy and memory, in one run

```bash
emanafa -a com.example.app -s 30 -pm both
```

This is quicker, and the two results are from exactly the same execution. The energy result includes the cost of memory polling.

### A device with no power rails

E-MANAFA changes to the legacy model automatically. To use the legacy model on a device that has power rails:

```bash
emanafa -a com.example.app -s 30 --force-legacy
```

### How long to measure

| Option | The session stops |
|---|---|
| `-s 30` | after 30 seconds |
| `-cmd "./run-test.sh"` | when the command on the host is complete |
| none | when you press a key |

## Energy of each method

With `-a <package>`, E-MANAFA records the methods of the app with `am profile` (sampling). The app must be `debuggable`, or must have `<profileable android:shell="true"/>` in its manifest.

| `--trace-methods` | How | Needs |
|---|---|---|
| `am` (default with `-a`) | sampled method trace of `am profile` | `-a`, a debuggable or profileable app |
| `hunter` (or `-ht`) | log lines of the Hunter instrumentation | an instrumented app |
| `perf` | call-stack samples of `linux.perf`. Gives mostly native functions, with less overhead than `am` | `-a`, power rails, a debuggable or profileable app |
| `none` | no method trace | nothing |

```bash
# energy of an app, without the method trace
emanafa -a com.example.app -s 30 --trace-methods none
```

How the energy of a method is calculated:

- With power rails, a method gets the energy of the **CPU rails** that the **processes of the app** consumed while the method was on the call stack. Other rails (display, radios) are not attributed, because a method does not control them directly.
- With the legacy model, a method gets the estimate of the model for the same time interval, for all the device.
- The interval contains the work of all threads of the app, not only of the method. A caller includes the energy of its callees.

### Energy of each process

In the `energy` mode, the trace also has the scheduler events. E-MANAFA divides the energy of each CPU rail between the threads that ran on the CPUs of that rail, in each interval between two rail samples. The results give the energy of each process (`energy.by_process`), and of each thread of the app (`energy.app_process`). The session report prints the processes with the most energy.

## Sunburst diagram

The sunburst shows where the app consumes energy in its call graph.

```bash
pip install plotly
emanafa -a com.example.app -s 30 --sunburst app.html
```

Open `app.html` in a browser.

The diagram needs the power rails. With the legacy model, E-MANAFA writes no diagram.

The page has three views. Select one with the buttons above the diagram:

- **Call graph.** The graph starts at the methods of the app and contains all that they call. Each ring is one call level. A sector is one call path (a method with its callers). The size and the colour of a sector show its energy.
- **Packages: all code.** Three rings: the inner ring is the package, the next ring is the class, and the outer ring is the method. It contains the app, the libraries and the Android framework. A sector shows the energy that the methods consumed in their own code.
- **Packages: app only.** The first three rings are the packages, the classes and the methods of the app only. Below each method are the calls that it makes to other code (libraries, framework), so a method sector contains the energy of its own code and of the code that it calls. A call to a different method of the app is not below the caller: that method has its own sector. The app is the package that you give with `-a`. `--package` of `manafa.sunburst` selects a different package prefix.

**Hot spots.** A sector is orange, not blue, if it has more than 10 % of the energy of the diagram or more than 20 % of the energy of its ring, and if it also ran for more than 10 ms. Change the limits with `--hot-total`, `--hot-ring` and `--hot-min-ms` of `manafa.sunburst`.

**Values.** A value is the total of all the calls of the method (or of the call path). The hover text also gives the number of calls, the mean energy of one call and the execution time.

In all the views:

- Click a sector to zoom in. Click the centre to go back.
- The level buttons select how many call levels are shown.
- The search box finds a method or a package and gives it a thick outline.
- The bar chart shows the methods that consume the most energy in their own code. Click a bar to find the method in the call graph.
- The slider and the Play button show how the energy grows during the session.

Methods that only wait (for example `Object.wait` or an idle `Looper`) get no energy. Call paths with less than 0.05 % of the energy are not drawn.

You can also make the diagram later, from a result file. The method trace (`*_calls.csv`) must be in its initial location.

```bash
python3 -m manafa.sunburst results.json -o app.html
python3 -m manafa.sunburst results.json --focus com.example.ui,androidx  # start the graph at these packages
python3 -m manafa.sunburst results.json --focus all --max-depth 20       # all the process, more levels
python3 -m manafa.sunburst results.json --package com.example.ui         # "app only" view for this package
python3 -m manafa.sunburst results.json --hot-total 5 --hot-ring 30 --hot-min-ms 50  # other hot spot limits
python3 -m manafa.sunburst results.json --include-blocked              # also give energy to methods that wait
```

## Output

Each run prints a summary: total energy (J and Wh), the rails that consume the most, memory statistics, and the estimated battery drain (percentage of the full capacity that the device reports).

`-o <file>` sets the result file, and `-of csv` writes CSV. The default is a JSON file with the name `emanafa_<mode>_<time>.json`.

```jsonc
{
  "mode": "energy",
  "app": "com.example.app",
  "duration_seconds": 30,
  "energy": {
    "total": 14.12,                                // joules, all rails
    "by_rail": { "power.rails.cpu.big": 4.81, "...": 0.0 },
    "measured_duration_seconds": 29.75,            // time that the rail samples cover
    "method_rails": ["power.rails.cpu.big", "..."] // rails that are attributed to methods
  },
  "memory": { "MemAvailable": { "min_mb": 3201.4, "avg_mb": 3320.1, "max_mb": 3450.0, "samples": 600 } },
  "method_invocations": {
    "total": 1842, "distinct": 217,
    "trace_file": ".../results_calls.csv",
    "methods": { "<method>_<id>": { "0": { "begin_time": 0, "end_time": 0, "consumption": 0.01 } } }
  },
  "battery_drain": { "consumed_energy_joules": 14.12, "battery_drain_percentage": 0.083 }
}
```

In the `energy` mode, `energy` also has `by_process` (energy of the CPU rails for each process), `app_process` (the app and its threads, with `-a`) and `method_energy_scope`. With `--trace-methods perf`, the file has `sampled_functions`.

With method tracing, the JSON file has one record for each method call, so it can be some tens of MB. To keep it small, the signature of a method (`method_def`) is only in its first call, and a call has no fields for components with no energy.

The raw files (Perfetto trace, batterystats log, method trace) and the power profile of the device are in `manafa/results/`. Each new run deletes the files of the previous run.

## Parse recorded files again

```bash
# session of the energy, memory or both mode (power rails): only the trace is necessary
emanafa -pft trace-<id>.perfetto-trace -o again.json

# session of the legacy model
emanafa -bts bstats-<id>.log -pft trace-<id>.systrace -o again.json
emanafa -d <directory with bstats-*, trace-* and method_traces-* files>
```

This mode needs no device. With no device, give the power profile of the legacy model with `-p`.
The recorded files are in `manafa/results` until the next measurement starts.

## On-device mode

In the normal mode, the computer controls the session through adb. In the on-device mode, a small recorder script on the device records the session, so the USB cable can be disconnected during the measurement (a connected cable charges the device). The computer analyses the recorded files later, with the same code as the normal mode.

```bash
emanafa ondevice install                      # put the recorder and the Perfetto configs on the device
emanafa ondevice start -pm energy -a com.example.app
# ... disconnect the cable, use the app, connect the cable ...
emanafa ondevice stop
emanafa ondevice export results/              # copy the recorded sessions to the computer
emanafa ondevice analyze results/ --sunburst  # results of each session
```

`emanafa ondevice run results/ -s 30 -a com.example.app` does all the steps in sequence.

After `install`, you can also start and stop a session in a shell on the device:

```bash
sh /data/local/tmp/emanafa/manafa.sh start energy com.example.app am
sh /data/local/tmp/emanafa/manafa.sh stop
```

The recorder has the same modes (`energy`, `memory`, `both`, `legacy`) and the same method sources (`am`, `perf`) as the normal mode. It uses the same Perfetto configs and does the same steps in the same sequence. The device can keep many sessions before an export. `emanafa -d results/` also analyses an exported directory.

## MCP server

An LLM client (for example Claude Code or Claude Desktop) can record sessions and read their results through the MCP server.

```bash
pip install "manafa[mcp]"
python -m manafa.mcp_server results/          # results directory with exported sessions
```

Configuration of the client:

```json
{ "mcpServers": { "e-manafa": { "command": "python", "args": ["-m", "manafa.mcp_server", "/path/to/results"] } } }
```

| Tool | Result |
|---|---|
| `list_sessions`, `get_session_summary` | Sessions, total energy, mean power, battery drain |
| `get_power_rails`, `get_process_energy`, `get_app_threads` | Energy of each rail, each process, each thread of the app |
| `get_memory` | System memory counters |
| `get_hot_methods`, `get_sampled_functions` | Methods and native functions with the most energy |
| `compare_sessions` | Difference of mean power between two sessions |
| `query_trace` | PerfettoSQL query on the trace of a session |
| `build_sunburst` | Sunburst diagram of a session |
| `device_status`, `record_session` | State of the device, and a new session |

## Command-line reference

| Option | Description |
|---|---|
| `-a, --app_package <pkg>` | App to measure. Starts the method trace (`am`). |
| `-s, --time_in_secs <n>` | Measure for *n* seconds. |
| `-cmd, --command "<cmd>"` | Run this command on the host, and stop when it is complete. |
| `-pm, --profile-mode` | `energy` (default), `memory`, `both` or `legacy`. |
| `--force-legacy` | Use the legacy model. |
| `--trace-methods` | `am`, `hunter`, `perf` or `none`. |
| `ondevice <command>` | On-device mode. See [On-device mode](#on-device-mode). |
| `-ht, --hunter` | Same as `--trace-methods hunter`. |
| `--sunburst [FILE]` | Write the sunburst diagram. Needs `-a` and `plotly`. |
| `--meminfo-period-ms <n>` | Period of memory polling. Default 50. |
| `--battery-poll-ms <n>` | Period of power rail polling. Default 250. A smaller value gives a better time resolution for method energy. It does not change the total. |
| `-p, --profile <file>` | `power_profile.xml` for the legacy model. |
| `-t, --timezone <tz>` | Timezone of the device, if detection fails. IANA name (`Europe/Lisbon`) or UTC offset (`+0100`). |
| `-o, --output_file <file>` | Result file. |
| `-of, --output-format` | `json` (default) or `csv`. |
| `-bts`, `-pft`, `-htf`, `-d` | Recorded files to parse again. |

## Library API

```python
from manafa.emanafa import EManafa

em = EManafa()
em.profiler_mode = "both"          # energy / memory / both / legacy
em.init(clean=True)
em.start()

# ... run the workload ...

em.stop()                          # pulls the files and parses them
print(em.power_rails_energy["total"], "J")        # energy and both modes
print(em.power_rails_energy["by_rail"])
print(em.memory_stats)                            # memory and both modes
```

With the legacy model, use the time interval of the session:

```python
begin, end = em.perf_events.events[0].time, em.perf_events.events[-1].time
total, per_component, timeline = em.get_consumption_in_between(begin, end)
```

For method energy, use `manafa.am_emanafa.AMEManafa(app_package_name=...)` or `manafa.hunter_emanafa.HunterEManafa()`. They have the same interface.

## Accuracy and limits

- **Power rails are measurements.** Use them when the device has them. The total is the sum of all the rails that the device reports.
- **The legacy model is an estimate.** On a Pixel 9 Pro XL (Android 16), with the power profile of the device, the legacy model gave these errors against the power rails: CPU -46 % to -75 % with a CPU load (the profile gives about 1 W for a core that consumes 4.7 W), and screen +230 % at low brightness. Use legacy results to compare runs on the same device, not as absolute values.
- **Method energy is energy of the device during the method.** It is not only the energy of the method. Do the measurement with no other apps in use.
- **A USB cable charges the device.** E-MANAFA tells Android to behave as unplugged during the session, so that batterystats records data. The cable still supplies power, and the charging circuit and the heat can change the result. For the most accurate measurements, connect `adb` through Wi-Fi (`adb pair` and `adb connect`, or `adb tcpip 5555`) and disconnect the cable. E-MANAFA uses `adb` only before and after the session, but the Wi-Fi radio stays on during the session.
- A session that stops abnormally can leave the unplugged state on. `adb shell dumpsys battery reset` removes it.
- **The method trace has an overhead.** Sampling makes the app slower and adds CPU energy. Compare runs that use the same options.

## Testing

```bash
# tests that need no device
python3 -m pytest manafa/tests/test_*.py manafa/tests/accuracy manafa/tests/parsers manafa/tests/hunter

# all tests (some need a connected device)
python3 -m pytest manafa/tests
```

### Accuracy against power rails

`manafa/tests/accuracy` compares E-MANAFA results with the power rails. It checks the session total, the energy of methods, and the estimate of the legacy model.

```bash
# synthetic session with a known result, no device needed
python3 -m pytest manafa/tests/accuracy -s

# measure the connected device for 30 s, then compare the legacy model with the rails
EMANAFA_LIVE_ACCURACY=30 python3 -m pytest manafa/tests/accuracy -s

# a session that was recorded in energy mode
EMANAFA_TRACE_FILE=manafa/results/perfetto/trace-<id>.perfetto-trace \
EMANAFA_BTS_FILE=manafa/results/batterystats/bstats-<id>.log \
EMANAFA_POWER_PROFILE=<power_profile.xml> EMANAFA_TIMEZONE=Europe/Lisbon \
python3 -m pytest manafa/tests/accuracy -s
```

The report gives the rails energy, the legacy estimate and the relative error for the total, the CPU and the screen. Set `EMANAFA_MAX_REL_ERROR` (for example `0.3`) to make the test fail above an error limit.

## Associated publications

```
@inproceedings{
          10.1145/3551349.3561342,
          author = {Rua, Rui and Saraiva, Jo\~{a}o},
          title = {E-MANAFA: Energy Monitoring and ANAlysis Tool For Android},
          year = {2023},
          isbn = {9781450394758},
          publisher = {Association for Computing Machinery},
          address = {New York, NY, USA},
          url = {https://doi.org/10.1145/3551349.3561342},
          doi = {10.1145/3551349.3561342},
          articleno = {202},
          numpages = {4},
          location = {Rochester, MI, USA},
          series = {ASE22}
}
```
