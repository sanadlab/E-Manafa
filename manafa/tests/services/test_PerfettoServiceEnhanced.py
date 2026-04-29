"""Tests for PerfettoServiceEnhanced config rendering."""
import os
import re
from unittest import TestCase

from manafa.services.perfettoServiceEnhanced import (
    PerfettoServiceEnhanced,
    DEFAULT_MEMINFO_PERIOD_MS,
    DEFAULT_BATTERY_POLL_MS,
)
from manafa.utils.Utils import get_resources_dir


def _read(path):
    with open(path, 'r') as f:
        return f.read()


class ConfigSelectionTests(TestCase):
    def test_picks_memory_only_config_when_only_memory_enabled(self):
        s = PerfettoServiceEnhanced(enable_energy=False, enable_memory=True)
        self.assertEqual(s.cfg_file, "perfetto_config_memory_only.pbtxt")

    def test_picks_power_rails_config_when_only_energy_enabled(self):
        s = PerfettoServiceEnhanced(enable_energy=True, enable_memory=False)
        self.assertEqual(s.cfg_file, "perfetto_config_power_rails.pbtxt")

    def test_picks_combined_config_when_both_enabled(self):
        s = PerfettoServiceEnhanced(enable_energy=True, enable_memory=True)
        self.assertEqual(s.cfg_file, "perfetto_config_both.pbtxt")

    def test_rejects_when_neither_enabled(self):
        with self.assertRaises(ValueError):
            PerfettoServiceEnhanced(enable_energy=False, enable_memory=False)

    def test_default_periods_are_50_and_250(self):
        s = PerfettoServiceEnhanced(enable_energy=True, enable_memory=True)
        self.assertEqual(s.meminfo_period_ms, 50)
        self.assertEqual(s.battery_poll_ms, 250)
        self.assertEqual(DEFAULT_MEMINFO_PERIOD_MS, 50)
        self.assertEqual(DEFAULT_BATTERY_POLL_MS, 250)


class RenderConfigTests(TestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        for p in self._created:
            try:
                os.remove(p)
            except OSError:
                pass

    def _render(self, service, cfg_name):
        path = os.path.join(get_resources_dir(), cfg_name)
        rendered = service._render_config(path)
        self._created.append(rendered)
        return rendered

    def test_substitutes_meminfo_period_in_memory_only_config(self):
        s = PerfettoServiceEnhanced(enable_energy=False, enable_memory=True,
                                     meminfo_period_ms=37)
        rendered = self._render(s, "perfetto_config_memory_only.pbtxt")
        contents = _read(rendered)
        match = re.search(r'meminfo_period_ms:\s*(\d+)', contents)
        self.assertIsNotNone(match, "meminfo_period_ms missing from rendered config")
        self.assertEqual(match.group(1), "37")

    def test_substitutes_battery_poll_in_power_rails_config(self):
        s = PerfettoServiceEnhanced(enable_energy=True, enable_memory=False,
                                     battery_poll_ms=125)
        rendered = self._render(s, "perfetto_config_power_rails.pbtxt")
        contents = _read(rendered)
        match = re.search(r'battery_poll_ms:\s*(\d+)', contents)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "125")

    def test_combined_config_substitutes_both_periods(self):
        s = PerfettoServiceEnhanced(enable_energy=True, enable_memory=True,
                                     meminfo_period_ms=80, battery_poll_ms=400)
        rendered = self._render(s, "perfetto_config_both.pbtxt")
        contents = _read(rendered)
        self.assertIn("meminfo_period_ms: 80", contents)
        self.assertIn("battery_poll_ms: 400", contents)

    def test_render_writes_a_temp_file_distinct_from_source(self):
        s = PerfettoServiceEnhanced(enable_energy=False, enable_memory=True,
                                     meminfo_period_ms=50)
        source = os.path.join(get_resources_dir(), "perfetto_config_memory_only.pbtxt")
        rendered = self._render(s, "perfetto_config_memory_only.pbtxt")
        self.assertNotEqual(source, rendered)
        self.assertTrue(os.path.exists(rendered))
        # source should still be untouched
        self.assertIn("meminfo_period_ms: 250", _read(source))
