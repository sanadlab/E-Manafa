"""Device-free tests of the energy model: time conversion, CPU model, component rules,
battery drain and the Perfetto configs."""
import os
import re
import tempfile
from unittest import TestCase
from unittest.mock import patch

from manafa.parsing.batteryStats.BatteryStatsParser import BatteryStatsParser
from manafa.parsing.perfetto.perfettoParser import PerfettoCPUEvent, PerfettoCPUfreqParser
from manafa.parsing.powerProfile.PowerProfile import PowerProfile
from manafa.tests import synthetic
from manafa.utils import dateUtils
from manafa.utils.BatteryDrainCalculator import BatteryDrainCalculator
from manafa.utils.Utils import get_resources_dir


class WithProfile(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.profile_file = synthetic.write_profile(cls.tmp.name)
        cls.profile = PowerProfile(cls.profile_file)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class TimeConversionTests(TestCase):
    WALL_TIME = "2020-11-13-09-54-20"
    UTC_EPOCH = 1605261260

    def test_reset_time_uses_the_device_timezone(self):
        self.assertEqual(dateUtils.batStatResetTimeToTimeStamp(self.WALL_TIME, "UTC"), self.UTC_EPOCH)
        self.assertEqual(dateUtils.batStatResetTimeToTimeStamp(self.WALL_TIME, "Asia/Dubai"), self.UTC_EPOCH - 4 * 3600)
        self.assertEqual(dateUtils.batStatResetTimeToTimeStamp(self.WALL_TIME, "+0400"), self.UTC_EPOCH - 4 * 3600)
        self.assertEqual(dateUtils.batStatResetTimeToTimeStamp(self.WALL_TIME, "-03:30"), self.UTC_EPOCH + 3.5 * 3600)

    def test_summer_time_is_applied(self):
        winter = dateUtils.batStatResetTimeToTimeStamp("2020-01-15-12-00-00", "Europe/Lisbon")
        summer = dateUtils.batStatResetTimeToTimeStamp("2020-07-15-12-00-00", "Europe/Lisbon")
        self.assertEqual(winter % 86400, 12 * 3600)
        self.assertEqual(summer % 86400, 11 * 3600)

    def test_unknown_timezone_is_an_error(self):
        self.assertFalse(dateUtils.is_known_timezone("CEST"))
        with self.assertRaises(ValueError):
            dateUtils.batStatResetTimeToTimeStamp(self.WALL_TIME, "CEST")

    def test_android_16_format_keeps_milliseconds(self):
        time = dateUtils.convertBatStatTimeToTimeStampNew("11-13 09:54:20.952", 2020, "UTC")
        self.assertAlmostEqual(time, self.UTC_EPOCH + 0.952, places=3)


class CpuModelTests(WithProfile):
    def current_ma(self, freqs, idle=(), state="active"):
        return PerfettoCPUEvent(0, freqs, idle).calculate_CPUs_current(state, self.profile) * 1000

    def test_running_cores_are_added_not_averaged(self):
        # 2 x 20 + 2 x 200 (cores) + 1 + 2 (clusters) + 5 (cpu.active)
        self.assertAlmostEqual(self.current_ma([2000, 2000, 4000, 4000]), 448)

    def test_idle_cores_and_their_clusters_are_not_counted(self):
        self.assertAlmostEqual(self.current_ma([2000, 2000, 4000, 4000], [False, True, True, True]), 26)

    def test_all_cores_idle_gives_cpu_idle(self):
        self.assertAlmostEqual(self.current_ma([2000, 2000, 4000, 4000], [True] * 4), 3)

    def test_idle_and_suspend_states_use_their_own_current(self):
        self.assertAlmostEqual(self.current_ma([2000] * 4, state="idle"), 3)
        self.assertAlmostEqual(self.current_ma([2000] * 4, state="suspend"), 1)

    def test_frequency_between_two_speeds_is_interpolated(self):
        self.assertAlmostEqual(self.current_ma([1500, 0, 0, 0], [False, True, True, True]), 15 + 1 + 5)


SYSTRACE = """# tracer: nop
          <idle>-0     (-----) [001] .... 10.000000: cpu_frequency: state=1000 cpu_id=1
          <idle>-0     (-----) [001] .... 10.500000: cpu_idle: state=1 cpu_id=1
          <idle>-0     (-----) [002] .... 11.000000: cpu_frequency: state=4000 cpu_id=2
          <idle>-0     (-----) [001] .... 11.500000: cpu_idle: state=4294967295 cpu_id=1
""".splitlines()


class OfflineTraceParsingTests(WithProfile):
    def setUp(self):
        self.parser = PerfettoCPUfreqParser(self.profile_file, start_time=100.0)
        self.parser.parse_history(SYSTRACE)

    def test_core_count_comes_from_the_power_profile(self):
        self.assertEqual(len(self.parser.events[0].vals), 4)

    def test_a_core_starts_with_its_own_first_frequency(self):
        # core 2 reports 4000 one second after the first event. cores 0 and 3 never report.
        self.assertEqual(self.parser.events[0].vals, [0, 1000, 4000, 0])

    def test_idle_events_are_parsed(self):
        self.assertEqual([e.time for e in self.parser.events], [110.0, 110.5, 111.0, 111.5])
        self.assertTrue(self.parser.events[1].idle[1])
        self.assertFalse(self.parser.events[3].idle[1])

    def test_closest_pair(self):
        self.assertEqual(self.parser.get_closest_pair(0), (0, 0))
        self.assertEqual(self.parser.get_closest_pair(110.0), (0, 1))
        self.assertEqual(self.parser.get_closest_pair(110.7), (1, 2))
        self.assertEqual(self.parser.get_closest_pair(999), (3, 3))


class ComponentRuleTests(WithProfile):
    def currents(self, *state_lines):
        parser = BatteryStatsParser(self.profile_file)
        for time, states in enumerate(state_lines):
            parser.add_update(float(time), parser.parse_states(states))
        return parser.events[-1].currents

    def test_active_mobile_radio_has_a_current(self):
        self.assertEqual(self.currents("+mobile_radio phone_signal_strength=great")["modem"], 145)

    def test_gps_is_charged_only_when_it_is_on(self):
        self.assertEqual(self.currents("+gps gps_signal_quality=good")["gps"], 5)
        self.assertEqual(self.currents("+gps gps_signal_quality=poor")["gps"], 28)
        self.assertEqual(self.currents("+gps")["gps"], 50)
        self.assertEqual(self.currents("+gps gps_signal_quality=good", "-gps")["gps"], 0)
        self.assertEqual(self.currents("+gps gps_signal_quality=good", "-gps gps_signal_quality=none")["gps"], 0)

    def test_wifi_states_are_exclusive(self):
        self.assertEqual(self.currents("+wifi")["wifi"], 31)                # idle
        self.assertEqual(self.currents("+wifi +wifi_radio")["wifi"], 39)    # receiving, not idle + mean(tx, rx)
        self.assertEqual(self.currents("+wifi", "-wifi")["wifi"], 0)

    def test_screen_with_no_brightness_value_is_dark(self):
        # the history does not print brightness=dark in its first line
        self.assertEqual(self.currents("+screen")["screen"], 100)
        self.assertEqual(self.currents("+screen brightness=bright")["screen"], 300)

    def test_android_16_history(self):
        parser = BatteryStatsParser(self.profile_file, timezone="UTC")
        parser.parse_history([
            "Battery History [Format: 2] (0% used, 7396 used of 4096KB, 6 strings using 135):",
            "  11-13 09:54:20.682 RESET:TIME: 2020-11-13-09-54-20",
            "  11-13 09:54:20.682 100 volt=4000 +running +screen",
            "  11-13 09:54:20.640 100 +wifi",  # a time before the previous line
            "  11-13 09:54:21.000 100 -screen",
        ])
        times = [e.time for e in parser.events]
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(times[0], 1605261260.682, places=3)
        self.assertEqual(parser.events[0].currents["wifi"], 31)

    def test_bluetooth_is_charged_only_when_it_is_on(self):
        self.assertEqual(self.currents("+running")["bluetooth"], 0)
        self.assertEqual(self.currents("+bluetooth")["bluetooth"], 7)


DUMPSYS_BATTERY = """Current Battery Service state:
  AC powered: false
  Charge counter: 2000000
  status: 3
  health: 2
  level: 50
  Capacity level: 5
  scale: 100
  voltage: 4000
  temperature: 250
"""


class BatteryDrainTests(TestCase):
    def drain(self, charge_full):
        def shell(cmd, args=[]):
            if "charge_full" in cmd:
                return (0, charge_full, "") if charge_full else (1, "", "Permission denied")
            return 0, DUMPSYS_BATTERY, ""
        with patch("manafa.utils.BatteryDrainCalculator.execute_shell_command", side_effect=shell):
            return BatteryDrainCalculator().calculate_battery_drain(576.0)

    def test_capacity_is_estimated_from_charge_and_level(self):
        # 2000 mAh remain at 50 %: the full capacity is 4000 mAh = 16 Wh at 4 V. 576 J = 0.16 Wh = 1 %
        info = self.drain(charge_full=None)
        self.assertAlmostEqual(info['full_capacity_mah'], 4000)
        self.assertAlmostEqual(info['battery_drain_percentage'], 1.0)

    def test_kernel_full_capacity_is_preferred(self):
        self.assertAlmostEqual(self.drain(charge_full="3200000\n")['full_capacity_mah'], 3200)


class PerfettoConfigTests(TestCase):
    def test_traces_have_no_fixed_duration(self):
        for name in ("perfetto_config_power_rails.pbtxt", "perfetto_config_memory_only.pbtxt",
                     "perfetto_config_both.pbtxt"):
            with open(os.path.join(get_resources_dir(), name)) as f:
                config = re.sub(r'#.*', '', f.read())
            self.assertNotIn("duration_ms", config, name)


class IntervalTests(WithProfile):
    """A window that has events inside it and exactly one event after it."""

    def setUp(self):
        from manafa.emanafa import EManafa
        self.manafa = EManafa.__new__(EManafa)
        bat = BatteryStatsParser(self.profile_file)
        for time, states in ((0.0, "volt=4000 +running +screen brightness=dark"), (10.0, "-screen -running"),
                             (20.0, "+running"), (40.0, "volt=3000 -running")):
            bat.add_update(time, bat.parse_states(states))
        perf = PerfettoCPUfreqParser(self.profile_file, start_time=0.0)
        perf.events = [PerfettoCPUEvent(0.0, [2000, 2000, 4000, 4000], [False] * 4),         # 448 mA
                       PerfettoCPUEvent(10.0, [1000, 0, 0, 0], [False, True, True, True]),   # 16 mA
                       PerfettoCPUEvent(40.0, [2000, 2000, 4000, 4000], [False] * 4)]
        self.manafa.bat_events, self.manafa.perf_events = bat, perf

    def test_component_states_inside_the_window_are_used(self):
        # screen: 100 mA at 4 V from 5 s to 10 s, then off
        _, per_component = self.manafa.calculate_non_cpu_energy(5.0, 30.0)
        self.assertAlmostEqual(per_component["screen"], 0.1 * 4 * 5)

    def test_cpu_frequencies_inside_the_window_are_used(self):
        # 5 s at 448 mA, 10 s idle (3 mA), 10 s at 16 mA. 4 V
        expected = 4 * (0.448 * 5 + 0.003 * 10 + 0.016 * 10)
        self.assertAlmostEqual(self.manafa.calculate_cpu_energy(5.0, 30.0), expected)

    def test_cpu_state_and_voltage_do_not_come_from_later_events(self):
        # the event at 40 s (idle, 3 V) is after the window
        self.assertEqual(self.manafa.bat_events.get_CPU_samples_in_between(2.0, 4.0), [(2.0, "active", 4.0)])
        self.assertEqual(self.manafa.bat_events.get_CPU_samples_in_between(22.0, 24.0), [(2.0, "active", 4.0)])

    def test_window_with_no_event_inside_uses_the_state_before_it(self):
        self.assertAlmostEqual(self.manafa.calculate_cpu_energy(12.0, 14.0), 4 * 0.003 * 2)
        self.assertAlmostEqual(self.manafa.calculate_cpu_energy(50.0, 52.0), 3 * 0.003 * 2)  # 3 V after 40 s

    def test_timeline_of_a_window_with_no_event_inside_has_the_state_before_it(self):
        # events at 10 s and 20 s. the window 12 s - 14 s has none
        timeline = self.manafa.bat_events.get_events_in_between(12.0, 14.0)
        self.assertEqual(timeline["volt"], [("4000", 12.0, 14.0, 100.0)])
        self.assertNotIn("screen", timeline)  # the screen is off

    def test_timeline_starts_with_the_state_before_the_window(self):
        # screen on until 10 s, then off
        timeline = self.manafa.bat_events.get_events_in_between(5.0, 15.0)
        self.assertEqual(timeline["screen"], [(1, 5.0, 10.0, 50.0)])
        self.assertEqual(timeline["volt"], [("4000", 5.0, 15.0, 100.0)])

    def test_timeline_has_a_new_entry_when_a_state_is_set_to_on_again(self):
        # running: on until 10 s, on again from 20 s to 40 s
        timeline = self.manafa.bat_events.get_events_in_between(0.0, 50.0)
        self.assertEqual(timeline["running"], [(1, 0.0, 10.0, 20.0), (1, 20.0, 40.0, 40.0)])

    def test_legacy_report_names_its_energy_source(self):
        self.assertEqual(self.manafa.gen_final_report(5.0, 30.0)['global']['energy_source'], 'power profile')

    def test_cpu_energy_without_batterystats_uses_the_default_voltage(self):
        self.manafa.bat_events = BatteryStatsParser(self.profile_file)
        self.assertAlmostEqual(self.manafa.calculate_cpu_energy(2.0, 4.0), 3.7 * 0.448 * 2)

    def test_method_gets_no_energy_outside_the_recorded_data(self):
        self.manafa.power_rails = None
        self.assertEqual(self.manafa.get_method_consumption(50.0, 60.0)[0], 0)
        inside = self.manafa.get_method_consumption(30.0, 40.0)[0]
        self.assertAlmostEqual(self.manafa.get_method_consumption(30.0, 60.0)[0], inside)
        self.assertGreater(inside, 0)


class ClockOffsetTests(TestCase):
    def test_offset_between_monotonic_and_epoch_time(self):
        from manafa import am_emanafa
        lines = {"epoch": "--------- beginning of main\n         1795074147.076 10130 10130 I EMANAFA_CLOCK: sync\n",
                 "monotonic": "--------- beginning of main\n542755.115 10130 10130 I EMANAFA_CLOCK: sync\n"}

        def shell(cmd, args=[]):
            return 0, next((out for clock, out in lines.items() if "-v " + clock in cmd), ""), ""
        with patch("manafa.am_emanafa.execute_shell_command", side_effect=shell), \
                patch("manafa.am_emanafa.time.sleep") as sleep:
            self.assertAlmostEqual(am_emanafa.get_monotonic_to_epoch_s(), 1795074147.076 - 542755.115, places=3)
            monotonic, lines["monotonic"] = lines["monotonic"], ""
            self.assertIsNone(am_emanafa.get_monotonic_to_epoch_s())
            # the line is in the log buffer only at the second attempt
            sleep.side_effect = lambda seconds: lines.update(monotonic=monotonic)
            self.assertAlmostEqual(am_emanafa.get_monotonic_to_epoch_s(), 1795074147.076 - 542755.115, places=3)


class SunburstTests(TestCase):
    def test_invocations_with_the_same_call_path_are_one_sector(self):
        import json
        from manafa.sunburst import build_sunburst
        with tempfile.TemporaryDirectory() as tmp:
            # main runs 4 s and calls work two times. the cpu consumes 1 W
            calls = synthetic.write_am_csv(tmp, [("a.Main.main", 0, 4, 0), ("a.Main.work", 0, 1, 1),
                                                 ("a.Main.work", 2, 3, 1), ("lib.Util.help", 2, 3, 2)])
            start = synthetic.MONOTONIC * 1e9
            session = {"app": "a", "method_invocations": {"trace_file": calls},
                       "energy": {"total": 6.0, "method_energy_series": [[start, 0.0], [start + 4e9, 4.0]]}}
            out = build_sunburst(session, os.path.join(tmp, "s.html"), frames=2)
            with open(out) as f:
                page = f.read()
        figure = json.loads(re.search(r'Plotly\.newPlot\(\s*"sunburst",\s*(\[.*?\]),\s*\{', page, re.S).group(1))
        self.assertEqual(figure[0]["labels"], ["session", "Main.main", "Main.work", "Util.help"])
        self.assertEqual([round(v, 6) for v in figure[0]["values"]], [4.0, 4.0, 2.0, 1.0])
        # name, energy in its own code, calls, time (ms), mean energy of a call
        self.assertEqual(figure[0]["customdata"][2], ["a.Main.work", 1.0, 2, 2000.0, 1.0])
        # view of all the code: packages a and lib, with the energy of the methods in their own code
        self.assertEqual(figure[2]["labels"], ["session", "a", "Main", "main", "work", "lib", "Util", "help"])
        self.assertEqual([round(v, 6) for v in figure[2]["values"]], [4.0, 3.0, 3.0, 2.0, 1.0, 1.0, 1.0, 1.0])
        # view of the app only: package, class, method. the call to the library is below the method
        self.assertEqual(figure[3]["labels"], ["a", "a", "Main", "main", "work", "Util.help"])
        self.assertEqual(figure[3]["parents"], ["", "session", "a:0", "a:1", "a:1", "a:3"])
        self.assertEqual([round(v, 6) for v in figure[3]["values"]], [4.0, 4.0, 4.0, 2.0, 2.0, 1.0])

    def test_hot_spots(self):
        from manafa.sunburst import hot_sectors
        ids = ["session", "a", "b", "a1", "a2", "b1"]
        parents = ["", "session", "session", "a", "a", "b"]
        values = [100.0, 95.0, 5.0, 9.0, 1.0, 5.0]
        times = [900.0, 800.0, 100.0, 50.0, 50.0, 5.0]
        # a: more than 10 % of the total. a1: 9 % of the total, but 60 % of its ring.
        # b1: 33 % of its ring, but only 5 ms. b and a2: below the two limits
        self.assertEqual(hot_sectors(ids, parents, values, times, 10, 20, 10),
                         [False, True, False, True, False, False])
        self.assertEqual(hot_sectors(ids, parents, values, times, 4, 20, 10),
                         [False, True, True, True, False, False])


class ResultsFileTests(TestCase):
    """The record of each call in the results file has no data that is in another place."""

    def test_calls_have_only_the_data_that_is_not_repeated(self):
        from manafa.parsing.method_traces.AppConsumptionStats import AppConsumptionStats
        with tempfile.TemporaryDirectory() as tmp:
            stats = AppConsumptionStats(results_dir=tmp)

        def call(begin, components, metrics):
            return {'begin_time': begin, 'end_time': begin + 1, 'depth': 0, 'method_def': '"com.app.A.run: ()V"',
                    'checked': False, 'consumption': sum(components.values()),
                    'per_component_consumption': components, 'metrics': metrics}
        stats.app_traces = {'com.app.A.run_1': {0: call(1.0, {'cpu': 0.5, 'screen': 0.0}, {}),
                                                 1: call(3.0, {'cpu': 0.0, 'screen': 0.2}, {'screen': [(1, 3.0, 4.0, 100.0)]})}}
        calls = stats.get_elaborate_stats()['com.app.A.run_1']
        self.assertEqual(calls[0], {'begin_time': 1.0, 'end_time': 2.0, 'depth': 0, 'consumption': 0.5,
                                    'method_def': '"com.app.A.run: ()V"', 'cpu': 0.5})
        self.assertEqual(calls[1], {'begin_time': 3.0, 'end_time': 4.0, 'depth': 0, 'consumption': 0.2,
                                    'cpu': 0.0, 'screen': 0.2, 'metrics': {'screen': [(1, 3.0, 4.0, 100.0)]}})
        self.assertEqual(stats.get_total_methods(), 2)  # the session data is not changed


class MethodTraceFileTests(TestCase):
    def test_each_run_has_its_own_trace_file_name(self):
        from manafa.services.AmProfilerService import AmProfilerService
        service = AmProfilerService.__new__(AmProfilerService)
        service.package_name, service.boot_time = "com.app", 100.0
        with patch("manafa.services.AmProfilerService.execute_shell_command",
                   side_effect=[(0, "1700000001\n", ""), (0, "1700000002\n", "")]):
            first, second = service.get_results_filename(None), service.get_results_filename(None)
        self.assertEqual(first, "/data/local/tmp/app_com.app_1700000001_100.0.trace")
        self.assertNotEqual(first, second)


class MethodKeyTests(TestCase):
    def test_method_key_is_the_same_in_each_run_and_different_for_each_signature(self):
        from manafa.parsing.method_traces.AMParser import AMParser
        parser = AMParser()
        parser.parse_history(['"com.app.A.run: ()V",1000,10,0', '"com.app.A.run: (I)V",2000,10,0',
                              '"com.app.A.run: ()V",3000,10,0'])
        # crc32 of " ()V\"" and " (I)V\"": fixed values, not hash()
        self.assertEqual(sorted(parser.trace), ['com.app.A.run_6c789991', 'com.app.A.run_afde11be'])
        self.assertEqual(len(parser.trace['com.app.A.run_6c789991']) + len(parser.trace['com.app.A.run_afde11be']), 3)
