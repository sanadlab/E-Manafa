[![Build Status](https://travis-ci.com/RRua/e-manafa.svg?branch=main)](https://travis-ci.com/RRua/e-manafa)
[![made-with-python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)
[![PyPI version](https://badge.fury.io/py/manafa.svg)](https://badge.fury.io/py/manafa)
[![PyPI license](https://img.shields.io/pypi/l/ansicolortags.svg)](https://pypi.python.org/pypi/manafa)
[![PyPI status](https://img.shields.io/pypi/status/ansicolortags.svg)](https://pypi.python.org/pypi/manafa)
[![DOI](https://zenodo.org/badge/459943164.svg)](https://zenodo.org/badge/latestdoi/459943164)


# E-MANAFA: Energy Monitor and ANAlyzer For Android

E-MANAFA is a plug-and-play, model-based tool for fine-grained estimation of energy and memory consumption on Android devices. It records system-level and per-component data from a running device and produces both summary reports and detailed traces that can be replayed offline.

It pulls data from four sources, depending on the device and the chosen mode:

- **`power_profile.xml`** — vendor-supplied current draw per component state. When unavailable, E-MANAFA can derive it dynamically from `batterystats`.
- **`batterystats`** — Android framework log of every power-related event since the last full charge.
- **Perfetto** — system-wide tracing for Linux/Android. Used both for legacy CPU-frequency tracing and for the newer **`android.power` power-rail counters** (Pixel 6+ ODPM and equivalent hardware) and **`linux.sys_stats` meminfo counters**.
- **ActivityManager / logcat** — optional method-level traces, either via `am profile` (sampled) or via the Hunter logcat instrumentation.

> Manufacturers do not always publish accurate per-component current draws. Validate the values in your `power_profile.xml` against an external apparatus (e.g. Monsoon) before trusting absolute numbers. Google's guidelines: <https://source.android.com/devices/tech/power/component>.

## Documentation

<https://greensoftwarelab.github.io/E-MANAFA/modules.html>

## Setup

Required:
- Android device running **Android 9 or above** (rooted not required for the new modes; sampled `am profile` only requires `<profileable android:shell="true"/>` or `android:debuggable="true"` on the target app).
- *nix-based host (macOS, Linux).
- Python **3.6+**.
- Android platform-tools — <https://developer.android.com/studio/releases/platform-tools>.

For **power-rail energy** mode you also need a device whose Perfetto build advertises the `android.power` data source (Pixel 6 / Tensor SoCs and most flagships from 2022 onward). E-MANAFA detects this automatically and falls back to the legacy profiler otherwise.

## Installation

### Via pip

```
pip install manafa
```

### From sources

```
git clone https://github.com/greensoftwarelab/E-MANAFA.git
cd E-MANAFA
python -m venv env && source env/bin/activate
pip install -r requirements.txt
```

In either case, point the shell at your Android SDK (typically in `~/.bashrc` / `~/.zshrc`):

```
export ANDROID_HOME=$HOME/<android-install-folder>/
export PATH=$ANDROID_HOME/platform-tools:$PATH
```

## Profiling modes

E-MANAFA exposes four mutually exclusive **profiling modes** via `-pm` / `--profile-mode`:

| Mode      | Energy source                                | Memory                | Notes                                                            |
|-----------|----------------------------------------------|-----------------------|------------------------------------------------------------------|
| `legacy`  | `batterystats` + Perfetto CPU-freq           | —                     | Original E-MANAFA pipeline. Always available.                    |
| `energy`  | Perfetto power-rail counters (`android.power`) | —                   | Default. Auto-falls-back to `legacy` when rails aren't supported.|
| `memory`  | —                                            | `linux.sys_stats` meminfo counters | System-wide RAM stats from `/proc/meminfo` polled by Perfetto.   |
| `both`    | power rails                                  | meminfo               | Single trace, both pipelines. Adds some overhead.                |

`--force-legacy` bypasses auto-detection entirely and pins the run to the legacy pipeline regardless of `-pm`.

## Method tracing

Independent of the profiling mode, you can capture method-level invocations of the target app via `--trace-methods`:

| Value     | Mechanism                                    | Requires `-a`? |
|-----------|----------------------------------------------|----------------|
| `none`    | No method tracing.                           | no             |
| `am`      | `am profile start --sampling …` (sampled). Works on apps declaring `<profileable android:shell="true"/>` (Android 10+) **or** `android:debuggable="true"`. | yes |
| `hunter`  | LogCat-based tracing via the Hunter instrumentation. | no       |

**Defaults:** `am` if `-a <package>` is given, `hunter` if `-ht` is given, otherwise `none`. Pass the flag explicitly to override (e.g. `-a com.foo --trace-methods none` profiles energy on a package without method tracing).

When method tracing is on, the JSON/CSV output gains a `method_invocations` block with the total invocation count, distinct method count, the path to the AM/Hunter trace file, and per-method data (with energy attribution when batterystats samples are available, invocation counts only when not).

## Command-line reference

```
$ python3 manafa/main.py --help
```

### Live profiling

| Flag | Description |
|------|-------------|
| `-a, --app_package <pkg>`        | App to profile. Enables `am` method tracing by default. |
| `-s, --time_in_secs <n>`         | Profile for *n* seconds. Omit to stop on keypress. |
| `-cmd, --command "<shell cmd>"`  | Run the given command on the host while profiling, then stop. |
| `-pm, --profile-mode {legacy,energy,memory,both}` | Profiling mode. Default `energy`. |
| `--force-legacy`                 | Force the legacy profiler regardless of device capability. |
| `--trace-methods {none,am,hunter}` | Method tracing strategy. See table above. |
| `-ht, --hunter`                  | Shortcut for `--trace-methods hunter`. |
| `--meminfo-period-ms <n>`        | `/proc/meminfo` polling period in ms. Default **50**. |
| `--battery-poll-ms <n>`          | `android.power` battery + power-rail polling period in ms. Default **250**. Power rails are cumulative, so this only affects per-window granularity, not total-energy accuracy. |
| `-p, --profile <file>`           | Override `power_profile.xml`. Falls back to dynamic inference. |
| `-t, --timezone <tz>`            | Override device timezone (auto-detected otherwise). |
| `-o, --output_file <path>`       | Output file path (auto-named under `emanafa_<mode>_<ts>.<fmt>` if omitted). |
| `-of, --output-format {json,csv}` | Detailed-results format. Default `json`. |

### Offline parsing

| Flag | Description |
|------|-------------|
| `-bts, --batstatsfile <file>` | Pre-recorded batterystats log. |
| `-pft, --perfettofile <file>` | Pre-recorded Perfetto trace. |
| `-htf, --hunterfile <file>`   | Pre-recorded Hunter logcat dump (implies `--trace-methods hunter`). |
| `-d, --directory <dir>`       | Batch-parse a directory of `bstats-*` / `trace-*` / `hunter-*` files. |

## Examples

```bash
# Default (energy via power rails) for an app, with sampled method tracing
python3 manafa/main.py -a com.android.chrome -s 30

# Memory-only profile, polling /proc/meminfo every 50 ms (default)
python3 manafa/main.py -a com.android.chrome -s 30 -pm memory

# Energy + memory in one trace, finer power-rail polling for sub-second attribution
python3 manafa/main.py -a com.android.chrome -s 30 -pm both --battery-poll-ms 50

# Energy on a specific app, no method tracing
python3 manafa/main.py -a com.example -s 30 --trace-methods none

# Hunter-based method tracing without a specific package (system-wide)
python3 manafa/main.py -s 30 --trace-methods hunter

# Force the legacy pipeline (e.g. for a device without power rails)
python3 manafa/main.py -a com.example -s 30 --force-legacy

# Profile a host-side command and save CSV
python3 manafa/main.py -a com.example -cmd "./run-test.sh" -of csv -o results.csv

# Replay an existing trace pair offline
python3 manafa/main.py -bts bstats-1762864000.log -pft trace-1762864000.perfetto-trace
```

## Output

Every live run prints a summary table covering total energy (Joules + Wh), the top power-rail consumers, system memory used (min/avg/max), and an estimated battery drain (% of effective capacity, accounting for battery health and temperature). The same data is written to JSON or CSV via `-of`/`-o`:

```jsonc
{
  "mode": "energy",
  "app": "com.android.chrome",
  "duration_seconds": 30,
  "timestamp": 1762864000.0,
  "energy": { "total": 14.12, "by_rail": { "power.VPH_PWR_S5C_uA": 4.81, "...": 0.0 } },
  "memory":  { "MemAvailable": { "min_mb": 3201.4, "avg_mb": 3320.1, "max_mb": 3450.0, "samples": 600 } },
  "method_invocations": {
    "total": 1842, "distinct": 217,
    "trace_file": "/Users/.../app_com.android.chrome_42_1762864000.csv",
    "methods": { "com.android.chrome.MainActivity_<hash>": 14, "...": 1 }
  },
  "battery_drain": { "consumed_energy_joules": 14.12, "battery_drain_percentage": 0.083 }
}
```

## Library API

```python
from manafa.emanafa import EManafa

em = EManafa()
em.profiler_mode = "energy"        # legacy / energy / memory / both
em.meminfo_period_ms = 50          # optional override
em.init(clean=True)
em.start()

# ... do_work_to_profile()

em.stop()                          # parses results internally
begin = em.perf_events.events[0].time
end   = em.perf_events.events[-1].time
total, per_component, timeline = em.get_consumption_in_between(begin, end)
out  = em.save_final_report(begin)
print(f"TOTAL: {total} Joules ({total / 3600:.4f} Wh)")
```

For per-app method tracing use `manafa.am_emanafa.AMEManafa(app_package_name=...)`; for logcat-based tracing use `manafa.hunter_emanafa.HunterEManafa()`. Both subclass `EManafa` and accept the same `profiler_mode` / sampling-period overrides.

## Testing

```
python3 -m pytest manafa/tests
```

Device-free unit tests live under `manafa/tests/test_main_cli.py` and `manafa/tests/services/test_*_unit.py`. The remaining tests are integration tests that require a connected device.

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
