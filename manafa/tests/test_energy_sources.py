"""Device-free tests of the order of the energy sources: power rails, then the energy
consumers of the PowerStats HAL, then the power profile."""
import json
import os
import tempfile
from unittest import TestCase, SkipTest
from unittest.mock import patch

from manafa import ondevice
from manafa.emanafa import EManafa, SOURCE_DISAGREEMENT_FACTOR
from manafa.parsing.batteryStats.BatteryStatsParser import BatteryStatsParser
from manafa.parsing.cpuAttribution import CpuAttribution, cluster_rails
from manafa.parsing.perfettoEnergyCalculator import PowerRailsTrace, RAILS, CONSUMERS, PROFILE
from manafa.services import perfettoServiceEnhanced as service
from manafa.tests import synthetic
from manafa.tests.test_ondevice import write_session

CPU_CONSUMER = 'consumer.cpu_cluster.0.CPUCL0'
DISPLAY_CONSUMER = 'consumer.display.0.Display'


class WithTraces(TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import perfetto.protos.perfetto.trace.perfetto_trace_pb2  # noqa: F401
        except ImportError as e:
            raise SkipTest("perfetto python package is not available: %s" % e)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.profile = synthetic.write_profile(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def trace(self, name, **options):
        directory = os.path.join(self.tmp.name, name)
        os.makedirs(directory, exist_ok=True)
        return synthetic.write_trace(directory, **options)

    def session(self, trace_file):
        """E-MANAFA object with the parsed trace, as parse_results makes it. No device."""
        manafa = EManafa.__new__(EManafa)
        manafa.power_profile, manafa.timezone = self.profile, "UTC"
        manafa.bat_events = BatteryStatsParser(self.profile, timezone="UTC")
        manafa.bat_events.parse_file(synthetic.write_batterystats(self.tmp.name))
        manafa.power_rails = PowerRailsTrace.from_trace(trace_file)
        manafa.power_rails_energy = manafa.power_rails.summary()
        return manafa


class SourceOrderTests(WithTraces):
    def test_rails_are_used_when_the_trace_has_rails_and_consumers(self):
        rails = PowerRailsTrace.from_trace(self.trace("both", consumers=('cpu', 'display')))
        self.assertEqual(rails.source, RAILS)
        self.assertEqual(sorted(rails.rails), ['power.rails.cpu.little', 'power.rails.display'])
        self.assertEqual(sorted(rails.consumers), [CPU_CONSUMER, DISPLAY_CONSUMER])
        start, end = rails.window()
        self.assertAlmostEqual(rails.consumers_cpu_energy(start, end), synthetic.CPU_JOULES, places=3)
        self.assertEqual(rails.summary()['source'], RAILS)

    def test_consumers_are_used_when_the_trace_has_no_rails(self):
        rails = PowerRailsTrace.from_trace(self.trace("consumers", rails=False, consumers=('cpu', 'display')))
        self.assertEqual(rails.source, CONSUMERS)
        summary = rails.summary()
        self.assertEqual(summary['source'], CONSUMERS)
        self.assertEqual(sorted(summary['by_rail']), [CPU_CONSUMER, DISPLAY_CONSUMER])
        self.assertAlmostEqual(summary['by_rail'][CPU_CONSUMER], synthetic.CPU_JOULES, places=3)
        self.assertAlmostEqual(summary['total'], synthetic.CPU_JOULES + synthetic.SCREEN_JOULES, places=3)
        self.assertEqual(summary['method_rails'], [CPU_CONSUMER])
        self.assertAlmostEqual(rails.window()[0], synthetic.EPOCH, places=3)

    def test_trace_with_no_rails_and_no_cpu_consumer_has_no_measured_energy(self):
        # the power profile is then the only source
        self.assertIsNone(PowerRailsTrace.from_trace(self.trace("display_only", rails=False, consumers=('display',))))
        self.assertIsNone(PowerRailsTrace.from_trace(self.trace("nothing", rails=False)))

    def test_trace_with_rails_only_has_no_consumers(self):
        rails = PowerRailsTrace.from_trace(self.trace("rails"))
        self.assertEqual((rails.source, rails.consumers), (RAILS, {}))
        self.assertIsNone(rails.consumers_cpu_energy(*rails.window()))

    def test_process_and_method_energy_work_with_consumers(self):
        trace_file = self.trace("consumers", rails=False, consumers=('cpu', 'display'))
        manafa = self.session(trace_file)
        manafa.attribute_cpu_energy(trace_file, synthetic.APP)
        energy = manafa.power_rails_energy
        self.assertAlmostEqual(energy['app_process']['energy_joules'], synthetic.APP_CPU_JOULES, places=3)
        self.assertAlmostEqual(manafa.get_method_consumption(synthetic.EPOCH + 0.5, synthetic.EPOCH + 1.5)[0],
                               synthetic.CPU_WATTS_FIRST_HALF, places=4)

    def test_consumers_of_the_clusters_are_matched_by_their_ordinal(self):
        cpus = {0: (0, 100), 1: (0, 100), 2: (1, 500), 3: (2, 1024)}
        consumers = ['consumer.cpu_cluster.2.CPUCL2', 'consumer.cpu_cluster.0.CPUCL0', 'consumer.cpu_cluster.1.CPUCL1']
        self.assertEqual(cluster_rails(consumers, cpus), [({0, 1}, ['consumer.cpu_cluster.0.CPUCL0']),
                                                          ({2}, ['consumer.cpu_cluster.1.CPUCL1']),
                                                          ({3}, ['consumer.cpu_cluster.2.CPUCL2'])])
        # one consumer for three clusters: no match
        self.assertEqual(cluster_rails(consumers[:1], cpus), [({0, 1, 2, 3}, consumers[:1])])


class CrossCheckTests(WithTraces):
    def warnings(self, log):
        return [call.args[0] for call in log.call_args_list if "disagree" in call.args[0]]

    def test_sources_that_agree_give_no_warning(self):
        trace_file = self.trace("both", consumers=('cpu', 'display'))
        manafa = self.session(trace_file)
        with patch("manafa.emanafa.log") as log:
            manafa.cross_check_energy_sources(trace_file)
        sources = manafa.power_rails_energy['cpu_joules_by_source']
        self.assertEqual(list(sources), [RAILS, CONSUMERS, PROFILE])
        for source in sources:
            self.assertAlmostEqual(sources[source], synthetic.CPU_JOULES, places=2, msg=source)
        self.assertEqual(self.warnings(log), [])

    def test_consumers_that_disagree_with_the_rails_give_a_warning(self):
        trace_file = self.trace("scaled", consumers=('cpu',), consumer_scale=SOURCE_DISAGREEMENT_FACTOR + 1)
        manafa = self.session(trace_file)
        with patch("manafa.emanafa.log") as log:
            manafa.cross_check_energy_sources(trace_file)
        sources = manafa.power_rails_energy['cpu_joules_by_source']
        self.assertAlmostEqual(sources[CONSUMERS] / sources[RAILS], SOURCE_DISAGREEMENT_FACTOR + 1, places=2)
        warnings = self.warnings(log)
        self.assertEqual(len(warnings), 1)
        self.assertIn("The results use the power rails", warnings[0])
        self.assertEqual(manafa.power_rails_energy['source'], RAILS)  # the rails stay the source

    def test_consumers_with_no_rails_are_compared_with_the_power_profile(self):
        trace_file = self.trace("consumers_scaled", rails=False, consumers=('cpu',), consumer_scale=4)
        manafa = self.session(trace_file)
        with patch("manafa.emanafa.log") as log:
            manafa.cross_check_energy_sources(trace_file)
        sources = manafa.power_rails_energy['cpu_joules_by_source']
        self.assertEqual(list(sources), [CONSUMERS, PROFILE])
        self.assertAlmostEqual(sources[CONSUMERS] / sources[PROFILE], 4, places=1)
        self.assertIn("The results use the energy consumers", self.warnings(log)[0])


class DeviceTests(WithTraces):
    def test_old_android_gets_a_config_with_no_consumers_option(self):
        for cfg_file in ("perfetto_config_power_rails.pbtxt", "perfetto_config_both.pbtxt"):
            self.assertIn(service.CONSUMERS_OPTION, service.render_config(cfg_file))
            old = service.render_config(cfg_file, energy_consumers=False)
            self.assertNotIn("collect_energy_estimation_breakdown", old)
            self.assertIn("collect_power_rails: true", old)
        self.assertNotIn("energy_estimation", ondevice.device_files(energy_consumers=False)["config_energy.pbtxt"].decode())

    def test_consumers_option_needs_android_12(self):
        for sdk, expected in (("30\n", False), ("31\n", True), ("36\n", True), ("", False)):
            with patch.object(service, "execute_shell_command", return_value=(0, sdk, "")):
                self.assertEqual(service.device_has_energy_consumers_option(), expected, sdk)

    def probe(self, trace_file, sdk="36"):
        """Result of the device probe, when the device records trace_file."""
        import shutil
        commands = []

        def shell(cmd, args=[]):
            commands.append(cmd)
            if cmd.startswith("adb pull"):
                shutil.copyfile(trace_file, cmd.split()[-1])
            return 0, sdk if "getprop" in cmd else "", ""
        with patch.object(service, "execute_shell_command", side_effect=shell):
            return service.device_energy_source(), commands

    def test_probe_finds_the_best_source_of_the_device(self):
        source, commands = self.probe(self.trace("both", consumers=('cpu',)))
        self.assertEqual(source, RAILS)
        self.assertTrue(any(service.CONSUMERS_OPTION in command for command in commands))
        self.assertEqual(self.probe(self.trace("consumers", rails=False, consumers=('cpu', 'display')))[0], CONSUMERS)
        self.assertIsNone(self.probe(self.trace("nothing", rails=False))[0])

    def test_probe_of_an_old_android_does_not_ask_for_consumers(self):
        source, commands = self.probe(self.trace("rails"), sdk="29")
        self.assertEqual(source, RAILS)
        self.assertFalse(any("energy_estimation" in command for command in commands))

    def test_device_with_consumers_only_uses_the_energy_mode(self):
        with patch.object(service, "device_energy_source", return_value=CONSUMERS):
            self.assertTrue(service.device_supports_power_rails())
        with patch.object(service, "device_energy_source", return_value=None):
            self.assertFalse(service.device_supports_power_rails())


class RecordedSessionTests(WithTraces):
    def test_session_of_a_device_with_consumers_only(self):
        directory = os.path.join(self.tmp.name, "session")
        os.makedirs(directory)
        write_session(directory)
        synthetic.write_trace(os.path.join(directory, "perfetto"), rails=False, consumers=('cpu', 'display'))
        ondevice.analyze(directory)
        with open(os.path.join(directory, "emanafa_energy_1.json")) as f:
            energy = json.load(f)["energy"]
        self.assertEqual(energy["source"], CONSUMERS)
        self.assertAlmostEqual(energy["total"], synthetic.CPU_JOULES + synthetic.SCREEN_JOULES, places=3)
        self.assertAlmostEqual(energy["app_process"]["energy_joules"], synthetic.APP_CPU_JOULES, places=3)
        self.assertEqual(list(energy["cpu_joules_by_source"]), [CONSUMERS, PROFILE])
