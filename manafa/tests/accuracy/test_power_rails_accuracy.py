"""Accuracy tests: E-MANAFA results against the power rails of Perfetto.

The power rails are energy counters that the device hardware measures, so they are
the reference. These tests compare three E-MANAFA results with them:

1. the session total of the energy mode,
2. the energy that is attributed to methods,
3. the estimate of the legacy model (batterystats + power profile + cpu frequency).

Three levels:

- Synthetic (no device). A generated trace where the correct result is known.
  Needs the perfetto python package and its trace_processor binary.
- Recorded session. Set these environment variables to the files of a session that
  was recorded in energy mode (`-pm energy`), then run pytest with `-s` to see the report:
      EMANAFA_TRACE_FILE     manafa/results/perfetto/trace-*.perfetto-trace
      EMANAFA_BTS_FILE       manafa/results/batterystats/bstats-*.log
      EMANAFA_POWER_PROFILE  power profile xml of the device
      EMANAFA_TIMEZONE       device timezone, e.g. Europe/Lisbon (default UTC)
      EMANAFA_MAX_REL_ERROR  optional. fail if the legacy total differs more, e.g. 0.3
- Live device. Set EMANAFA_LIVE_ACCURACY to the number of seconds to profile.
  Needs a connected device that has power rails.
"""
import os
import tempfile
from unittest import TestCase, SkipTest

from manafa.am_emanafa import AMEManafa
from manafa.emanafa import EManafa
from manafa.parsing.batteryStats.BatteryStatsParser import BatteryStatsParser
from manafa.parsing.method_traces.AMParser import AMParser
from manafa.parsing.method_traces.AppConsumptionStats import AppConsumptionStats
from manafa.parsing.perfetto.perfettoParser import PerfettoCPUfreqParser, CPU_IDLE_EXIT
from manafa.parsing.perfettoEnergyCalculator import PowerRailsTrace
from manafa.tests import synthetic


def open_trace(trace_file):
    try:
        from perfetto.trace_processor import TraceProcessor
        return TraceProcessor(trace=trace_file)
    except Exception as e:
        raise SkipTest("perfetto trace_processor is not available: %s" % e)


def perfetto_rail_energy(trace_file):
    """Reference: energy (J) of each power rail, with a direct Perfetto query and no E-MANAFA code."""
    tp = open_trace(trace_file)
    try:
        query = """
        SELECT t.name AS name, (MAX(c.value) - MIN(c.value)) * 1e-6 AS joules
        FROM counter c JOIN counter_track t ON c.track_id = t.id
        WHERE t.name LIKE 'power.%' GROUP BY t.name
        """
        return {row.name: row.joules for row in tp.query(query)}
    finally:
        tp.close()


def legacy_estimate(trace_file, bts_file, power_profile, timezone, start, end):
    """Energy (J) that the legacy model estimates between start and end (epoch seconds).

    Uses the cpu frequency and cpu idle events of the same trace that has the power rails,
    so the two results describe the same interval of the same session.
    Returns:
        total, per_component
    """
    manafa = EManafa.__new__(EManafa)  # no device needed
    manafa.bat_events = BatteryStatsParser(power_profile, timezone=timezone)
    manafa.bat_events.parse_file(bts_file)
    manafa.perf_events = PerfettoCPUfreqParser(power_profile)
    tp = open_trace(trace_file)
    try:
        boot_to_epoch = [row.clock_value - row.ts for row in tp.query(
            "SELECT clock_value, ts FROM clock_snapshot WHERE clock_name = 'REALTIME' ORDER BY snapshot_id LIMIT 1")][0]
        query = """
        SELECT c.ts AS ts, t.name AS name, t.cpu AS cpu, c.value AS value
        FROM counter c JOIN cpu_counter_track t ON c.track_id = t.id
        WHERE t.name IN ('cpufreq', 'cpuidle') ORDER BY c.ts
        """
        rows = [(row.ts, row.name, row.cpu, int(row.value)) for row in tp.query(query)]
    finally:
        tp.close()
    if not rows:
        raise SkipTest("the trace has no cpu frequency events. Record the session with -pm energy")
    for ts, name, cpu, value in rows:
        if name == 'cpufreq':
            manafa.perf_events.first_freqs.setdefault(cpu, value)
    for ts, name, cpu, value in rows:
        time = (ts + boot_to_epoch) * 1e-9
        if name == 'cpufreq':
            manafa.perf_events.add_event(time, cpu, value)
        else:
            manafa.perf_events.add_idle_event(time, cpu, value != CPU_IDLE_EXIT)
    total, per_component, _ = manafa.get_consumption_in_between(start, end)
    return total, per_component


def relative_error(estimate, reference):
    return (estimate - reference) / reference if reference else float('nan')


def compare_legacy_with_power_rails(trace_file, bts_file, power_profile, timezone):
    """Compares the legacy model with the power rails over the interval that the rails cover.

    Returns:
        dict: row name -> (rails joules, legacy joules, relative error of legacy).
        The 'cpu' and 'screen' rows exist only if the trace has rails with 'cpu' and 'disp' in the name.
    """
    rails = PowerRailsTrace.from_trace(trace_file)
    if rails is None:
        raise SkipTest("the trace has no power rails")
    start, end = rails.window()
    total, per_component = legacy_estimate(trace_file, bts_file, power_profile, timezone, start, end)
    rows = {'total': (rails.energy_between(start, end), total)}
    display_rails = [name for name in rails.rails if 'disp' in name.lower()]
    for row, rail_names, component in (('cpu', rails.cpu_rails(), 'cpu'), ('screen', display_rails, 'screen')):
        if rail_names:
            rows[row] = (rails.energy_between(start, end, rail_names), per_component.get(component, 0))
    rows = {row: (reference, estimate, relative_error(estimate, reference))
            for row, (reference, estimate) in rows.items()}
    print("\nLegacy model against power rails, %.1f s (%s)" % (end - start, os.path.basename(trace_file)))
    print("%-8s %14s %14s %10s" % ("", "rails (J)", "legacy (J)", "error"))
    for row, (reference, estimate, error) in rows.items():
        print("%-8s %14.4f %14.4f %+9.1f%%" % (row, reference, estimate, 100 * error))
    print("rails of the device: %s" % ", ".join(sorted(rails.rails)))
    return rows


class PowerRailsSeriesTests(TestCase):
    """Energy of an interval from cumulative counters. No trace file needed."""

    def setUp(self):
        # cpu rail: 2 W for 2 s, then 4 W for 2 s. display rail: 1 W.
        self.rails = PowerRailsTrace({
            'power.rails.cpu.big': ([10.0, 12.0, 14.0], [100.0, 104.0, 112.0]),
            'power.rails.display': ([10.0, 12.0, 14.0], [50.0, 52.0, 54.0]),
        })

    def test_total_is_last_counter_minus_first(self):
        self.assertAlmostEqual(self.rails.energy_between(10, 14), 12 + 4)
        self.assertAlmostEqual(self.rails.summary()['total'], 12 + 4)

    def test_interval_between_samples_is_interpolated(self):
        self.assertAlmostEqual(self.rails.energy_between(11, 13, ['power.rails.cpu.big']), 2 + 4)

    def test_time_outside_the_samples_adds_no_energy(self):
        self.assertEqual(self.rails.energy_between(0, 5), 0)
        self.assertEqual(self.rails.energy_between(20, 30), 0)
        self.assertAlmostEqual(self.rails.energy_between(0, 30), 16)

    def test_consecutive_intervals_add_up_to_the_total(self):
        edges = [10, 10.3, 11.9, 12.0, 13.7, 14]
        parts = sum(self.rails.energy_between(a, b) for a, b in zip(edges, edges[1:]))
        self.assertAlmostEqual(parts, self.rails.energy_between(10, 14))

    def test_methods_get_the_cpu_rails_only(self):
        self.assertEqual(self.rails.method_rails(), ['power.rails.cpu.big'])

    def test_methods_get_all_rails_when_no_cpu_rail_is_known(self):
        rails = PowerRailsTrace({'power.VPH_PWR_S5C_uA': ([0.0, 1.0], [0.0, 1.0])})
        self.assertEqual(rails.method_rails(), ['power.VPH_PWR_S5C_uA'])


class SyntheticTraceAccuracyTests(TestCase):
    """A generated session where the power rails consume exactly what the power profile predicts."""

    @classmethod
    def setUpClass(cls):
        try:
            import perfetto.protos.perfetto.trace.perfetto_trace_pb2  # noqa: F401
        except ImportError as e:
            raise SkipTest("perfetto python package is not available: %s" % e)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.profile = synthetic.write_profile(cls.tmp.name)
        cls.bts_file = synthetic.write_batterystats(cls.tmp.name)
        cls.trace_file = synthetic.write_trace(cls.tmp.name)
        cls.reference = perfetto_rail_energy(cls.trace_file)
        cls.rails = PowerRailsTrace.from_trace(cls.trace_file)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_session_total_is_equal_to_perfetto(self):
        summary = self.rails.summary()
        self.assertEqual(set(summary['by_rail']), set(self.reference))
        for rail, joules in self.reference.items():
            self.assertAlmostEqual(summary['by_rail'][rail], joules, places=6)
        self.assertAlmostEqual(summary['total'], sum(self.reference.values()), places=6)
        self.assertAlmostEqual(summary['total'], synthetic.CPU_JOULES + synthetic.SCREEN_JOULES, places=3)
        self.assertAlmostEqual(summary['measured_duration_seconds'], synthetic.SECONDS, places=3)

    def test_rail_times_are_epoch_times(self):
        start, end = self.rails.window()
        self.assertAlmostEqual(start, synthetic.EPOCH, places=3)
        self.assertAlmostEqual(self.rails.monotonic_to_epoch_s, synthetic.EPOCH - synthetic.MONOTONIC, places=3)

    def test_method_energy_is_the_cpu_rail_energy_of_its_interval(self):
        calls = [("com.app.Main.first", 0.5, 1.5, 0),   # CPU at 26 mA
                 ("com.app.Main.second", 1.0, 3.0, 0),  # 1 s at 26 mA, 1 s at 228 mA
                 ("com.app.Main.late", 100.0, 101.0, 0)]  # after the trace: no energy
        manafa = AMEManafa.__new__(AMEManafa)
        manafa.power_rails = self.rails
        manafa.pft_out_file = self.trace_file
        manafa.trace_out_file = synthetic.write_am_csv(self.tmp.name, calls)
        manafa.am_log_parser = AMParser(boot_time=self.rails.monotonic_to_epoch_s)
        manafa.app_consumptions = AppConsumptionStats(results_dir=self.tmp.name)
        manafa.calculate_function_consumption()
        energy = {inv['method_def'].split(':')[0].split('.')[-1]: inv['consumption']
                  for invocations in manafa.am_log_parser.trace.values() for inv in invocations.values()}
        volts = synthetic.VOLTAGE
        self.assertAlmostEqual(energy['first'], volts * synthetic.CPU_MA_FIRST_HALF / 1000, places=4)
        self.assertAlmostEqual(
            energy['second'], volts * (synthetic.CPU_MA_FIRST_HALF + synthetic.CPU_MA_SECOND_HALF) / 1000, places=4)
        self.assertEqual(energy['late'], 0)
        # the screen rail is not given to methods
        self.assertLess(energy['first'] + energy['second'], self.reference['power.rails.cpu.little'] + 1e-6)

    def test_legacy_model_agrees_with_power_rails(self):
        rows = compare_legacy_with_power_rails(self.trace_file, self.bts_file, self.profile, "UTC")
        self.assertAlmostEqual(rows['cpu'][0], synthetic.CPU_JOULES, places=3)
        self.assertAlmostEqual(rows['screen'][0], synthetic.SCREEN_JOULES, places=3)
        for row in ('total', 'cpu', 'screen'):
            self.assertLess(abs(rows[row][2]), 0.01, "%s: %s" % (row, rows[row],))


class RecordedSessionAccuracyTests(TestCase):
    """Files of a real session, given with environment variables (see the module docstring)."""

    def setUp(self):
        self.trace_file = os.environ.get("EMANAFA_TRACE_FILE")
        if not self.trace_file:
            self.skipTest("EMANAFA_TRACE_FILE is not set")

    def test_session_total_is_equal_to_perfetto(self):
        reference = perfetto_rail_energy(self.trace_file)
        rails = PowerRailsTrace.from_trace(self.trace_file)
        self.assertIsNotNone(rails, "the trace has no power rails")
        summary = rails.summary()
        self.assertAlmostEqual(summary['total'], sum(reference.values()), places=4)

    def test_legacy_model_against_power_rails(self):
        bts_file = os.environ.get("EMANAFA_BTS_FILE")
        profile = os.environ.get("EMANAFA_POWER_PROFILE")
        if not bts_file or not profile:
            self.skipTest("EMANAFA_BTS_FILE and EMANAFA_POWER_PROFILE are not set")
        rows = compare_legacy_with_power_rails(
            self.trace_file, bts_file, profile, os.environ.get("EMANAFA_TIMEZONE", "UTC"))
        self.assertGreater(rows['total'][0], 0)
        max_error = os.environ.get("EMANAFA_MAX_REL_ERROR")
        if max_error:
            self.assertLess(abs(rows['total'][2]), float(max_error))


class LiveDeviceAccuracyTests(TestCase):
    """Profiles the connected device, then compares the legacy model with its power rails."""

    def test_legacy_model_against_power_rails(self):
        seconds = os.environ.get("EMANAFA_LIVE_ACCURACY")
        if not seconds:
            self.skipTest("EMANAFA_LIVE_ACCURACY is not set")
        import time
        manafa = EManafa()
        manafa.profiler_mode = 'energy'
        manafa.init(clean=True)
        manafa.start()
        time.sleep(float(seconds))
        bts_file, trace_file = manafa.stop()
        if manafa.power_rails is None:
            self.skipTest("the device has no power rails")
        reference = perfetto_rail_energy(trace_file)
        self.assertAlmostEqual(manafa.power_rails_energy['total'], sum(reference.values()), places=4)
        rows = compare_legacy_with_power_rails(trace_file, bts_file, manafa.power_profile, manafa.timezone)
        self.assertGreater(rows['total'][0], 0)
        max_error = os.environ.get("EMANAFA_MAX_REL_ERROR")
        if max_error:
            self.assertLess(abs(rows['total'][2]), float(max_error))
