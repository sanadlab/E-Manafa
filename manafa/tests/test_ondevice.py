"""Device-free tests of the on-device mode: the files for the device, and the analysis of
recorded sessions."""
import json
import os
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from manafa import main as manafa_main
from manafa import ondevice
from manafa.services.perfettoServiceEnhanced import render_config
from manafa.tests import synthetic
from manafa.utils.Utils import get_resources_dir

BATTERY = """Current Battery Service state:
  AC powered: false
  Charge counter: 2500000
  level: 50
  voltage: 4000
  temperature: 250
"""


def write_session(directory, mode="energy", app=synthetic.APP):
    """Results directory with one recorded session (run id 1), as the recorder writes it."""
    for name in ("perfetto", "batterystats", "am"):
        os.makedirs(os.path.join(directory, name))
    synthetic.write_trace(os.path.join(directory, "perfetto"))
    synthetic.write_batterystats(os.path.join(directory, "batterystats"))
    with open(os.path.join(directory, "session-1.txt"), "w") as f:
        f.write(f"run=1\nmode={mode}\napp={app}\nmethods=none\nstart={synthetic.EPOCH}\n"
                f"stop={synthetic.EPOCH + synthetic.SECONDS}\nboot_time={synthetic.EPOCH - synthetic.BOOT}\n"
                "timezone=UTC\nutc_offset=+0000\nmodel=Test Phone\nclock_epoch=1700000010.5\n"
                "clock_monotonic=4910.5\ncharge_full=5000000\n")
    with open(os.path.join(directory, "battery-1.txt"), "w") as f:
        f.write(BATTERY)
    return directory


class DeviceFilesTests(TestCase):
    def test_device_gets_the_configs_of_the_normal_mode(self):
        files = ondevice.device_files()
        for mode, cfg_file in ondevice.CONFIGS.items():
            self.assertEqual(files[f"config_{mode}.pbtxt"].decode(), render_config(cfg_file))
        with open(os.path.join(get_resources_dir(), "perfetto.config.bin"), "rb") as f:
            self.assertEqual(files["config_legacy.bin"], f.read())
        self.assertIn("manafa.sh", files)

    def test_polling_periods_are_in_the_configs(self):
        files = ondevice.device_files(meminfo_period_ms=77, battery_poll_ms=333)
        self.assertIn("meminfo_period_ms: 77", files["config_both.pbtxt"].decode())
        self.assertIn("battery_poll_ms: 333", files["config_energy.pbtxt"].decode())

    def test_call_stack_config_has_a_place_for_the_app(self):
        config = ondevice.device_files()["config_energy_perf.pbtxt"].decode()
        self.assertIn('name: "linux.perf"', config)
        self.assertIn('target_cmdline: "__APP__"', config)
        self.assertNotIn("linux.perf", ondevice.device_files()["config_energy.pbtxt"].decode())

    def test_recorder_script_has_no_syntax_error(self):
        script = os.path.join(get_resources_dir(), "ondevice", "manafa.sh")
        self.assertEqual(subprocess.run(["sh", "-n", script], capture_output=True).returncode, 0)

    def test_recorder_refuses_a_wrong_command(self):
        script = os.path.join(get_resources_dir(), "ondevice", "manafa.sh")
        result = subprocess.run(["sh", script, "explode"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Usage", result.stdout)


class CommandTests(TestCase):
    def run_command(self, argv):
        with patch.object(ondevice, "recorder", return_value="E-MANAFA session 7 saved") as recorder, \
                patch.object(ondevice, "install") as install, patch.object(ondevice, "export") as export, \
                patch.object(ondevice, "analyze") as analyze, patch.object(ondevice.time, "sleep"):
            ondevice.main(argv)
        return recorder, install, export, analyze

    def test_start_gives_the_mode_the_app_and_the_method_source_to_the_recorder(self):
        self.assertEqual(self.run_command(["start"])[0].call_args.args, ("start energy",))
        self.assertEqual(self.run_command(["start", "-pm", "both"])[0].call_args.args, ("start both",))
        self.assertEqual(self.run_command(["start", "-a", "com.app"])[0].call_args.args, ("start energy com.app am",))
        recorder = self.run_command(["start", "-a", "com.app", "--trace-methods", "perf"])[0]
        self.assertEqual(recorder.call_args.args, ("start energy com.app perf",))

    def test_method_source_needs_an_app(self):
        with self.assertRaises(SystemExit), patch.object(sys, "stderr"):
            ondevice.main(["start", "--trace-methods", "am"])

    def test_run_does_all_the_steps_and_analyses_only_the_new_session(self):
        recorder, install, export, analyze = self.run_command(["run", "out", "-s", "5"])
        self.assertEqual([call.args[0] for call in recorder.call_args_list], ["start energy", "stop"])
        install.assert_called_once()
        export.assert_called_once_with("out")
        self.assertEqual(analyze.call_args.kwargs["runs"], ["7"])

    def test_emanafa_command_gives_ondevice_to_this_module(self):
        with patch.object(sys, "argv", ["emanafa", "ondevice", "status"]), \
                patch.object(ondevice, "recorder") as recorder:
            manafa_main.main()
        recorder.assert_called_once_with("status")


class AnalysisTests(TestCase):
    """A recorded session is analysed with no device. The trace is the generated trace of
    manafa.tests.synthetic, so the correct results are known."""

    @classmethod
    def setUpClass(cls):
        try:
            import perfetto.protos.perfetto.trace.perfetto_trace_pb2  # noqa: F401
        except ImportError as e:
            from unittest import SkipTest
            raise SkipTest("perfetto python package is not available: %s" % e)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = write_session(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def results(self):
        with open(os.path.join(self.directory, "emanafa_energy_1.json")) as f:
            return json.load(f)

    def test_sessions_are_read_from_the_directory(self):
        sessions = ondevice.read_sessions(self.directory)
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0]["run"], sessions[0]["mode"], sessions[0]["app"]), ("1", "energy", synthetic.APP))
        self.assertTrue(ondevice.session_file(sessions[0], "perfetto/trace-{run}-*").endswith(".perfetto-trace"))
        self.assertIsNone(ondevice.session_file(sessions[0], "power_profile-{run}.txt"))

    def test_energy_session_gives_the_results_of_the_normal_mode(self):
        with patch("manafa.utils.BatteryDrainCalculator.execute_shell_command") as adb:
            ondevice.analyze(self.directory)
        adb.assert_not_called()  # the battery data comes from the session
        results = self.results()
        self.assertEqual((results["mode"], results["app"], results["duration_seconds"]),
                         ("energy", synthetic.APP, synthetic.SECONDS))
        energy = results["energy"]
        self.assertAlmostEqual(energy["total"], synthetic.CPU_JOULES + synthetic.SCREEN_JOULES, places=3)
        self.assertAlmostEqual(energy["app_process"]["energy_joules"], synthetic.APP_CPU_JOULES, places=3)
        self.assertEqual(energy["by_process"][0]["process"], "other")
        # 5000 mAh at 4 V = 20 Wh
        self.assertAlmostEqual(results["battery_drain"]["total_battery_energy_wh"], 20.0)
        self.assertEqual(results["battery_drain"]["battery_level_percent"], 50)

    def test_result_is_equal_to_the_result_of_the_trace_parsed_directly(self):
        from manafa.parsing.perfettoEnergyCalculator import PowerRailsTrace
        ondevice.analyze(self.directory)
        trace = ondevice.session_file(ondevice.read_sessions(self.directory)[0], "perfetto/trace-{run}-*")
        direct = PowerRailsTrace.from_trace(trace).summary()
        self.assertEqual(self.results()["energy"]["by_rail"], direct["by_rail"])
        self.assertEqual(self.results()["energy"]["total"], direct["total"])

    def test_only_the_requested_sessions_are_analysed(self):
        with self.assertRaises(Exception):
            ondevice.analyze(self.directory, runs=["2"])
        self.assertFalse(os.path.exists(os.path.join(self.directory, "emanafa_energy_1.json")))

    def test_directory_option_of_emanafa_reads_recorded_sessions(self):
        argv = ["emanafa", "-d", self.directory, "-p", synthetic.write_profile(self.tmp.name), "-t", "UTC"]
        with patch.object(sys, "argv", argv), patch.object(manafa_main, "has_connected_devices", return_value=False):
            manafa_main.main()
        self.assertAlmostEqual(self.results()["energy"]["total"], synthetic.CPU_JOULES + synthetic.SCREEN_JOULES, places=3)

    def test_saved_power_profile_is_converted(self):
        with open(os.path.join(self.directory, "power_profile-1.txt"), "w") as f:
            f.write("  cpu.active=5.0\n  screen.on=100.0\n")
        xml = ondevice.session_power_profile(ondevice.read_sessions(self.directory)[0])
        self.assertTrue(xml.endswith("power_profile-1.xml") and os.path.getsize(xml) > 0)
        with open(os.path.join(self.directory, "power_profile-1.txt"), "w") as f:
            f.write("Unknown option: --power-profile\n")
        self.assertIsNone(ondevice.session_power_profile(ondevice.read_sessions(self.directory)[0]))
