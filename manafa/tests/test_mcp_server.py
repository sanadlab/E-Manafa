"""Device-free tests of the MCP server tools. The tools are plain functions, so most tests
do not need the `mcp` package."""
import asyncio
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from unittest import TestCase, SkipTest
from unittest.mock import patch

from manafa import mcp_server
from manafa.tests import synthetic
from manafa.tests.test_ondevice import write_session

TOOL_NAMES = ['list_sessions', 'get_session_summary', 'get_power_rails', 'get_process_energy', 'get_app_threads',
              'get_memory', 'get_hot_methods', 'get_sampled_functions', 'compare_sessions', 'query_trace',
              'build_sunburst', 'device_status', 'record_session']


class WithSessions(TestCase):
    """Results directory with session 1 (a generated trace) and session 2 (a results file
    with a method trace and call-stack samples)."""

    @classmethod
    def setUpClass(cls):
        try:
            import perfetto.protos.perfetto.trace.perfetto_trace_pb2  # noqa: F401
        except ImportError as e:
            raise SkipTest("perfetto python package is not available: %s" % e)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = write_session(cls.tmp.name)
        with open(os.path.join(cls.directory, "session-2.txt"), "w") as f:
            f.write("run=2\nmode=both\napp=com.app\nmethods=am\nstart=100\nstop=110\nmodel=Test Phone\n")
        results = {
            'mode': 'both', 'app': 'com.app', 'duration_seconds': 10,
            'energy': {'total': 20.0, 'measured_duration_seconds': 10.0, 'method_rails': ['power.rails.cpu.big'],
                       'by_rail': {'power.rails.cpu.big': 12.0, 'power.rails.display': 8.0},
                       'method_energy_scope': 'app process',
                       # 1 W of the app, on the clock of the method trace
                       'method_energy_series': [[round((synthetic.MONOTONIC + t) * 1e9), float(t)] for t in range(11)]},
            'memory': {'MemTotal': {'min_mb': 8000, 'avg_mb': 8000, 'max_mb': 8000, 'samples': 3}},
            'method_invocations': {'total': 3, 'distinct': 2, 'trace_file': synthetic.write_am_csv(cls.directory, [
                ("com.app.A.run", 0.0, 4.0, 0), ("com.app.B.load", 1.0, 2.0, 1), ("android.os.Parcel.read", 2.0, 2.5, 1),
                ("com.app.B.load", 3.0, 3.5, 1)]), 'methods': {
                'com.app.A.run_aaaaaaaa': {'0': {'begin_time': 1.0, 'end_time': 2.0, 'consumption': 3.0},
                                           '1': {'begin_time': 4.0, 'end_time': 4.5, 'consumption': 1.0}},
                'com.app.B.load_bbbbbbbb': {'0': {'begin_time': 1.0, 'end_time': 1.5, 'consumption': 0.5}}}},
            'sampled_functions': {'samples': 9, 'samples_with_stack': 9, 'functions': [
                {'function': 'memcpy', 'self_samples': 6, 'total_samples': 6, 'self_joules': 0.6, 'total_joules': 0.6},
                {'function': 'read', 'self_samples': 3, 'total_samples': 3, 'self_joules': 0.3, 'total_joules': 0.3}]},
            'battery_drain': {'battery_drain_percentage': 0.03},
        }
        with open(os.path.join(cls.directory, "emanafa_both_2.json"), "w") as f:
            json.dump(results, f)
        cls.patch = patch.object(mcp_server, "RESULTS_DIR", cls.directory)
        cls.patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.tmp.cleanup()


class SessionToolTests(WithSessions):
    def test_sessions_are_listed(self):
        sessions = mcp_server.list_sessions()
        self.assertEqual([s['session'] for s in sessions], ['1', '2'])
        self.assertEqual((sessions[1]['mode'], sessions[1]['app'], sessions[1]['duration_seconds']), ('both', 'com.app', 10))
        self.assertEqual(sessions[0]['device'], 'Test Phone')

    def test_summary_of_a_session_that_is_not_yet_analysed(self):
        summary = mcp_server.get_session_summary("1")
        self.assertAlmostEqual(summary['energy_joules'], synthetic.CPU_JOULES + synthetic.SCREEN_JOULES, places=3)
        self.assertAlmostEqual(summary['mean_power_watts'], summary['energy_joules'] / synthetic.SECONDS, places=3)
        self.assertAlmostEqual(summary['app_cpu_energy_joules'], synthetic.APP_CPU_JOULES, places=3)
        self.assertEqual(summary['source'], 'power rails')
        self.assertEqual(summary['has'], ['power_rails', 'process_energy'])
        self.assertTrue(os.path.exists(os.path.join(self.directory, "emanafa_energy_1.json")))

    def test_empty_session_is_the_most_recent_one(self):
        summary = mcp_server.get_session_summary()
        self.assertEqual(summary['session'], '2')
        self.assertEqual(summary['battery_drain_percent'], 0.03)
        self.assertEqual(summary['has'], ['power_rails', 'memory', 'methods', 'sampled_functions'])

    def test_unknown_session_gives_the_known_sessions(self):
        with self.assertRaisesRegex(ValueError, "Sessions: 1, 2"):
            mcp_server.get_session_summary("99")

    def test_power_rails_have_energy_and_mean_power(self):
        rails = mcp_server.get_power_rails("2")
        self.assertEqual(rails['rails'][0], {'rail': 'power.rails.cpu.big', 'joules': 12.0, 'mean_milliwatts': 1200.0})
        self.assertEqual(rails['total_joules'], 20.0)

    def test_process_energy(self):
        processes = mcp_server.get_process_energy("1", top=1)
        self.assertEqual(len(processes['processes']), 1)
        self.assertEqual(processes['processes'][0]['process'], 'other')
        self.assertAlmostEqual(processes['cpu_rails_joules'], synthetic.CPU_JOULES, places=3)
        with self.assertRaisesRegex(ValueError, "scheduler events"):
            mcp_server.get_process_energy("2")

    def test_app_threads(self):
        app = mcp_server.get_app_threads("1")
        self.assertEqual(app['process'], synthetic.APP)
        self.assertEqual(app['threads'][0]['thread'], 'worker')

    def test_memory(self):
        self.assertEqual(mcp_server.get_memory("2")['MemTotal']['avg_mb'], 8000)
        with self.assertRaisesRegex(ValueError, "no memory data"):
            mcp_server.get_memory("1")

    def test_hot_methods_are_ranked_by_the_energy_of_their_own_code(self):
        # A.run: 4 s, of which 2 s in its callees. B.load: two calls of 1 s and 0.5 s
        hot = mcp_server.get_hot_methods("2")
        self.assertEqual((hot['scope'], hot['joules_include_callees'], hot['distinct_methods']), ('app process', False, 2))
        self.assertEqual([m['method'] for m in hot['methods']], ['com.app.A.run', 'com.app.B.load'])
        run, load = hot['methods']
        self.assertAlmostEqual(run['joules'], 2.0)
        self.assertAlmostEqual(load['joules'], 1.5)
        self.assertEqual((load['calls'], load['mean_joules_per_call']), (2, load['joules'] / 2))
        self.assertAlmostEqual(run['seconds'], 4.0)

    def test_hot_methods_of_all_the_code_and_the_top_limit(self):
        hot = mcp_server.get_hot_methods("2", top=1, app_only=False)
        self.assertEqual(hot['distinct_methods'], 3)
        self.assertEqual(len(hot['methods']), 1)
        with self.assertRaisesRegex(ValueError, "no method trace"):
            mcp_server.get_hot_methods("1")

    def test_sampled_functions(self):
        sampled = mcp_server.get_sampled_functions("2", top=1)
        self.assertEqual([f['function'] for f in sampled['functions']], ['memcpy'])
        self.assertEqual(sampled['samples'], 9)
        with self.assertRaisesRegex(ValueError, "no call-stack samples"):
            mcp_server.get_sampled_functions("1")

    def test_compare_gives_the_difference_of_mean_power(self):
        same = mcp_server.compare_sessions("2", "2")
        self.assertEqual(same['difference_milliwatts'], 0)
        both = mcp_server.compare_sessions("1", "2")
        self.assertAlmostEqual(both['difference_milliwatts'], both['b']['mean_milliwatts'] - both['a']['mean_milliwatts'])
        self.assertEqual(both['rails'][0]['rail'], 'power.rails.cpu.big')  # 1200 mW in b, not in a

    def test_query_runs_sql_on_the_trace(self):
        rows = mcp_server.query_trace("1", "SELECT COUNT(DISTINCT name) AS rails FROM counter_track WHERE name LIKE 'power.%'")
        self.assertEqual(rows, [{'rails': 2}])
        rows = mcp_server.query_trace("1", "SELECT ts FROM counter")
        self.assertLessEqual(len(rows), mcp_server.MAX_ROWS)
        with self.assertRaisesRegex(ValueError, "no Perfetto trace"):
            mcp_server.query_trace("2", "SELECT 1")

    def test_sunburst_needs_a_method_trace(self):
        with self.assertRaises(ValueError):
            mcp_server.build_sunburst("1")

    def test_sunburst_is_written_in_the_results_directory(self):
        try:
            import plotly  # noqa: F401
        except ImportError as e:
            self.skipTest("plotly is not available: %s" % e)
        path = mcp_server.build_sunburst("2")
        self.assertEqual(path, os.path.realpath(os.path.join(self.directory, "emanafa_sunburst_2.html")))
        self.assertGreater(os.path.getsize(path), 1000)

    def test_tool_output_does_not_go_to_stdout(self):
        # stdout is the channel of the MCP protocol
        os.remove(os.path.join(self.directory, "emanafa_energy_1.json")) if os.path.exists(
            os.path.join(self.directory, "emanafa_energy_1.json")) else None
        out = io.StringIO()
        with redirect_stdout(out):
            mcp_server.get_session_summary("1")  # analyses the session, which prints a report
        self.assertEqual(out.getvalue(), "")


class DeviceToolTests(WithSessions):
    def test_status_with_no_device(self):
        with patch("manafa.utils.Utils.execute_shell_command", return_value=(0, "List of devices attached\n\n", "")):
            self.assertEqual(mcp_server.device_status(), {'devices': [], 'recorder': None})

    def test_status_with_a_device(self):
        outputs = [(0, "List of devices attached\nABC123\tdevice\n\n", ""), (0, "no session is running\n2 saved session(s)\n", "")]
        with patch("manafa.utils.Utils.execute_shell_command", side_effect=outputs):
            status = mcp_server.device_status()
        self.assertEqual(status['devices'], ['ABC123'])
        self.assertIn("no session is running", status['recorder'])

    def test_record_does_the_steps_of_the_on_device_mode(self):
        with patch.object(mcp_server.ondevice, "install") as install, \
                patch.object(mcp_server.ondevice, "recorder") as recorder, \
                patch.object(mcp_server.ondevice, "export") as export, patch("time.sleep") as sleep:
            summary = mcp_server.record_session(seconds=5, mode="both", app="com.app")
        install.assert_called_once()
        self.assertEqual([call.args[0] for call in recorder.call_args_list], ["start both com.app am", "stop"])
        sleep.assert_called_once_with(5)
        export.assert_called_once_with(self.directory)
        self.assertEqual(summary['session'], '2')  # the most recent session

    def test_record_refuses_wrong_arguments(self):
        with patch.object(mcp_server.ondevice, "install") as install:
            for arguments in ({'mode': 'turbo'}, {'seconds': 0}, {'seconds': 601}, {'trace_methods': 'am'},
                              {'app': 'com.app; rm -rf /'}, {'app': 'com.app', 'trace_methods': 'hunter'}):
                with self.assertRaises(ValueError, msg=arguments):
                    mcp_server.record_session(**arguments)
        install.assert_not_called()


class ServerTests(TestCase):
    def test_all_the_tools_are_in_the_server(self):
        self.assertEqual([function.__name__ for function in mcp_server.TOOLS], TOOL_NAMES)
        try:
            server = mcp_server.build_server()
        except ImportError as e:
            self.skipTest("mcp python package is not available: %s" % e)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(sorted(tool.name for tool in tools), sorted(TOOL_NAMES))
        self.assertTrue(all(tool.description for tool in tools))


class ServerCallTests(WithSessions):
    def test_client_call_gets_the_result_of_the_tool(self):
        try:
            server = mcp_server.build_server()
        except ImportError as e:
            self.skipTest("mcp python package is not available: %s" % e)
        result = asyncio.run(server.call_tool("get_power_rails", {"session": "2"}))
        text = json.dumps(result, default=lambda o: getattr(o, "model_dump", lambda: str(o))())
        self.assertIn("power.rails.cpu.big", text)
        self.assertIn("1200.0", text)

    def test_client_gets_the_message_of_a_wrong_request(self):
        try:
            server = mcp_server.build_server()
        except ImportError as e:
            self.skipTest("mcp python package is not available: %s" % e)
        with self.assertRaisesRegex(Exception, "Sessions: 1, 2"):
            asyncio.run(server.call_tool("get_power_rails", {"session": "99"}))
