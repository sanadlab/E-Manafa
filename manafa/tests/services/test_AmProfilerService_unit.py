"""Unit tests for AmProfilerService that don't require a connected device.

These complement the integration tests in test_AMProfilerService.py by mocking
execute_shell_command so we can assert on the exact `adb` invocations.
"""
from unittest import TestCase
from unittest.mock import patch

from manafa.services.AmProfilerService import AmProfilerService, PROFILING_SAMPLE_RATE


class ResolveMainActivityTests(TestCase):
    def test_picks_pkg_component_line(self):
        svc = AmProfilerService("com.example.app")
        resolve_output = (
            "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
            "  com.example.app/com.example.app.MainActivity\n"
        )
        with patch("manafa.services.AmProfilerService.execute_shell_command",
                   return_value=(0, resolve_output, "")):
            self.assertEqual(svc._resolve_main_activity(),
                             "com.example.app/com.example.app.MainActivity")

    def test_returns_none_when_unresolved(self):
        svc = AmProfilerService("com.example.app")
        with patch("manafa.services.AmProfilerService.execute_shell_command",
                   return_value=(0, "No activity found.\n", "")):
            self.assertIsNone(svc._resolve_main_activity())

    def test_ignores_header_line_that_mentions_package(self):
        # `cmd package resolve-activity --brief` sometimes prints the package in
        # the preferred-activity header; we should still pick the pkg/component line
        svc = AmProfilerService("com.example.app")
        out = (
            "  Preferred: com.example.app  (some metadata)\n"
            "  com.example.app/com.example.app.LauncherActivity\n"
        )
        with patch("manafa.services.AmProfilerService.execute_shell_command",
                   return_value=(0, out, "")):
            self.assertEqual(svc._resolve_main_activity(),
                             "com.example.app/com.example.app.LauncherActivity")


class StartCommandTests(TestCase):
    """The startup `am start -P` call must pass --sampling so the framework
    accepts the request on apps using <profileable android:shell="true"/>."""

    def _captured_commands(self, mock):
        return [call.args[0] for call in mock.call_args_list]

    def test_startup_call_includes_sampling_flag(self):
        svc = AmProfilerService("com.example.app")
        resolve = (0, "com.example.app/com.example.app.MainActivity\n", "")
        ok = (0, "ok", "")

        with patch("manafa.services.AmProfilerService.execute_shell_command",
                   side_effect=[ok, ok, resolve, ok, ok, ok]) as mock_exec, \
             patch("manafa.services.AmProfilerService.time.sleep"):
            self.assertTrue(svc.start(run_id="42"))

        cmds = self._captured_commands(mock_exec)
        startup = next(c for c in cmds if " am start " in c)
        self.assertIn("--sampling", startup)
        self.assertIn(f"--sampling {PROFILING_SAMPLE_RATE}", startup)
        self.assertIn("--start-profiler", startup)
        self.assertIn("com.example.app/com.example.app.MainActivity", startup)

    def test_exec_phase_uses_am_profile_start_with_sampling(self):
        svc = AmProfilerService("com.example.app")
        resolve = (0, "com.example.app/com.example.app.MainActivity\n", "")
        ok = (0, "ok", "")

        with patch("manafa.services.AmProfilerService.execute_shell_command",
                   side_effect=[ok, ok, resolve, ok, ok, ok]) as mock_exec, \
             patch("manafa.services.AmProfilerService.time.sleep"):
            svc.start(run_id="42")

        cmds = self._captured_commands(mock_exec)
        exec_cmd = next(c for c in cmds if "am profile start" in c)
        self.assertIn(f"--sampling {PROFILING_SAMPLE_RATE}", exec_cmd)
        self.assertIn("com.example.app", exec_cmd)

    def test_aborts_when_activity_unresolvable(self):
        svc = AmProfilerService("com.example.app")
        empty_resolve = (0, "No activity found.\n", "")
        ok = (0, "ok", "")

        with patch("manafa.services.AmProfilerService.execute_shell_command",
                   side_effect=[ok, ok, empty_resolve]) as mock_exec, \
             patch("manafa.services.AmProfilerService.time.sleep"):
            self.assertFalse(svc.start(run_id="42"))

        # we should NOT have issued an `am start` call
        cmds = self._captured_commands(mock_exec)
        self.assertFalse(any(" am start " in c for c in cmds),
                         f"unexpected am start in commands: {cmds}")
