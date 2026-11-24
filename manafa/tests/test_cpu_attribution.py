"""Device-free tests of the attribution of the CPU rail energy to threads and processes."""
import tempfile
from unittest import TestCase, SkipTest

from manafa.emanafa import EManafa
from manafa.parsing.cpuAttribution import CpuAttribution, cluster_rails
from manafa.parsing.perfettoEnergyCalculator import PowerRailsTrace
from manafa.tests import synthetic

LITTLE, BIG = 'power.rails.cpu.little', 'power.rails.cpu.big'


class ClusterRailsTests(TestCase):
    def test_rails_are_matched_with_clusters_by_capacity(self):
        cpus = {0: (0, 100), 1: (0, 100), 2: (1, 500), 3: (2, 1024)}
        rails = [BIG, LITTLE, 'power.rails.cpu.mid', 'power.rails.cpu.mid.mem']
        groups = cluster_rails(rails, cpus)
        self.assertEqual(groups, [({0, 1}, [LITTLE]), ({2}, ['power.rails.cpu.mid', 'power.rails.cpu.mid.mem']),
                                  ({3}, [BIG])])

    def test_two_clusters_are_little_and_big(self):
        self.assertEqual(cluster_rails([BIG, LITTLE], {0: (0, 100), 1: (1, 1024)}), [({0}, [LITTLE]), ({1}, [BIG])])

    def test_unknown_rail_names_give_one_group(self):
        cpus = {0: (0, 100), 1: (1, 1024)}
        self.assertEqual(cluster_rails(['power.S4M_VDD_CPUCL0'], cpus), [({0, 1}, ['power.S4M_VDD_CPUCL0'])])
        # no cluster data in the trace
        self.assertEqual(cluster_rails([BIG, LITTLE], {0: (0, None), 1: (0, None)}), [({0, 1}, [BIG, LITTLE])])


class AttributionTests(TestCase):
    """Two clusters. Rail samples at 0, 1 and 2 s."""

    def setUp(self):
        self.rails = PowerRailsTrace({LITTLE: ([0.0, 1.0, 2.0], [0.0, 2.0, 6.0]),
                                      BIG: ([0.0, 1.0, 2.0], [5.0, 15.0, 16.0])})
        self.threads = {1: {'tid': 10, 'name': 'main', 'pid': 10, 'process': 'com.app'},
                        2: {'tid': 11, 'name': 'worker', 'pid': 10, 'process': 'com.app'},
                        3: {'tid': 20, 'name': 'svc', 'pid': 20, 'process': 'com.app:service'},
                        4: {'tid': 30, 'name': 'other', 'pid': 30, 'process': 'other'},
                        5: {'tid': 40, 'name': 'kworker', 'pid': None, 'process': 'kworker'}}
        slices = [(0.0, 1.0, 0, 1),    # little, interval 0: main alone -> 2 J
                  (0.0, 0.5, 7, 1),    # big, interval 0: main alone -> 10 J
                  (1.0, 1.5, 0, 2),    # little, interval 1 (4 J): worker 0.5 s ...
                  (1.0, 2.0, 1, 4),    # ... and other 1 s
                  (0.5, 3.0, 9, 5)]    # cpu 9 is in no cluster
        groups = [({0, 1}, [LITTLE]), ({7}, [BIG])]
        self.attribution = CpuAttribution.from_slices(self.rails, groups, slices, self.threads)

    def test_thread_gets_its_part_of_the_cluster_energy_of_each_interval(self):
        self.assertAlmostEqual(self.attribution.thread_joules(1), 12.0)
        self.assertAlmostEqual(self.attribution.thread_joules(2), 4.0 / 3)
        self.assertAlmostEqual(self.attribution.thread_joules(4), 8.0 / 3)
        self.assertEqual(self.attribution.thread_joules(5), 0)

    def test_energy_of_an_interval_with_no_thread_is_not_attributed(self):
        # big rail, interval 1: 1 J and no thread
        self.assertAlmostEqual(self.attribution.unattributed, 1.0)
        attributed = sum(self.attribution.thread_joules(utid) for utid in self.threads)
        self.assertAlmostEqual(attributed + self.attribution.unattributed, 6.0 + 11.0)

    def test_processes_are_ranked_by_energy(self):
        processes = self.attribution.by_process()
        self.assertEqual([p['process'] for p in processes], ['com.app', 'other'])
        self.assertAlmostEqual(processes[0]['energy_joules'], 12.0 + 4.0 / 3)
        self.assertAlmostEqual(processes[0]['cpu_seconds'], 2.0)
        self.assertEqual(len(self.attribution.by_process(top=1)), 1)

    def test_app_has_its_main_process_and_its_named_processes(self):
        self.assertEqual(sorted(self.attribution.utids_of_app('com.app')), [1, 2, 3])
        self.assertEqual(self.attribution.utids_of_app('com.ap'), [])
        threads = self.attribution.by_thread([1, 2, 3])
        self.assertEqual([t['thread'] for t in threads], ['main', 'worker'])  # svc did not run

    def test_series_is_the_cumulative_energy(self):
        times, joules = self.attribution.series([1, 2])
        self.assertEqual(times, [0.0, 1.0, 2.0])
        self.assertAlmostEqual(joules[1], 12.0)
        self.assertAlmostEqual(joules[2], 12.0 + 4.0 / 3)

    def test_slice_over_two_intervals_is_divided(self):
        attribution = CpuAttribution.from_slices(self.rails, [({0}, [LITTLE])], [(0.5, 1.5, 0, 1)], self.threads)
        self.assertEqual(attribution.thread_energy[1], {0: 2.0, 1: 4.0})
        self.assertAlmostEqual(attribution.thread_cpu_s[1], 1.0)

    def test_method_gets_the_energy_of_the_app_and_not_of_all_the_cpu_rails(self):
        manafa = EManafa.__new__(EManafa)
        manafa.power_rails = self.rails
        manafa.app_energy = None
        cpu, per_component, _ = manafa.get_method_consumption(1.0, 2.0)
        self.assertAlmostEqual(cpu, 5.0)  # all the cpu rails: 4 J + 1 J
        manafa.app_energy = self.attribution.series(self.attribution.utids_of_app('com.app'))
        cpu, per_component, _ = manafa.get_method_consumption(1.0, 2.0)
        self.assertAlmostEqual(cpu, 4.0 / 3)
        self.assertAlmostEqual(per_component['all_rails'], 5.0)
        self.assertAlmostEqual(manafa.get_method_consumption(1.0, 1.5)[0], 2.0 / 3)  # interpolated


class TraceAttributionTests(TestCase):
    """The scheduler events and the power rails of a generated Perfetto trace."""

    @classmethod
    def setUpClass(cls):
        try:
            import perfetto.protos.perfetto.trace.perfetto_trace_pb2  # noqa: F401
        except ImportError as e:
            raise SkipTest("perfetto python package is not available: %s" % e)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.trace_file = synthetic.write_trace(cls.tmp.name)
        cls.rails = PowerRailsTrace.from_trace(cls.trace_file)
        cls.attribution = CpuAttribution.from_trace(cls.trace_file, cls.rails)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_process_energy_of_the_trace(self):
        processes = {p['process']: p for p in self.attribution.by_process()}
        self.assertAlmostEqual(processes[synthetic.APP]['energy_joules'], synthetic.APP_CPU_JOULES, places=3)
        self.assertAlmostEqual(processes['other']['energy_joules'], synthetic.OTHER_CPU_JOULES, places=3)
        self.assertAlmostEqual(processes[synthetic.APP]['cpu_seconds'], 3.0, places=3)
        self.assertAlmostEqual(self.attribution.unattributed, 0.0, places=3)

    def test_all_the_cpu_energy_is_attributed(self):
        attributed = sum(p['energy_joules'] for p in self.attribution.by_process())
        self.assertAlmostEqual(attributed, synthetic.CPU_JOULES, places=3)

    def test_session_results_have_the_app_and_its_threads(self):
        manafa = EManafa.__new__(EManafa)
        manafa.power_rails = self.rails
        manafa.power_rails_energy = self.rails.summary()
        manafa.attribute_cpu_energy(self.trace_file, synthetic.APP)
        energy = manafa.power_rails_energy
        self.assertEqual(energy['method_energy_scope'], 'app process')
        self.assertAlmostEqual(energy['app_process']['energy_joules'], synthetic.APP_CPU_JOULES, places=3)
        self.assertEqual([t['thread'] for t in energy['app_process']['threads']], ['worker', 't%d' % synthetic.APP_PID])  # thread names of the scheduler events
        self.assertAlmostEqual(energy['method_energy_series'][-1][1], synthetic.APP_CPU_JOULES, places=3)
        # the series is on the clock of the method trace
        self.assertEqual(energy['method_energy_series'][0][0], round(synthetic.MONOTONIC * 1e9))
        # 1 s in the first half: only the app runs
        self.assertAlmostEqual(manafa.get_method_consumption(synthetic.EPOCH + 0.5, synthetic.EPOCH + 1.5)[0],
                               synthetic.CPU_WATTS_FIRST_HALF, places=4)

    def test_no_app_in_the_trace_keeps_the_energy_of_all_processes(self):
        manafa = EManafa.__new__(EManafa)
        manafa.power_rails = self.rails
        manafa.power_rails_energy = self.rails.summary()
        manafa.attribute_cpu_energy(self.trace_file, 'com.not.there')
        self.assertEqual(manafa.power_rails_energy['method_energy_scope'], 'all processes')
        self.assertIsNone(manafa.app_energy)
        self.assertEqual(len(manafa.power_rails_energy['by_process']), 2)
