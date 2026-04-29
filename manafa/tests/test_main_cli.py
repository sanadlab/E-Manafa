"""CLI / dispatch tests for manafa.main.

These exercise the pure-Python plumbing — argparse default resolution,
profile-mode inference, and create_manafa's class dispatch — without
requiring a connected device.
"""
import argparse
import sys
from unittest import TestCase
from unittest.mock import patch

from manafa import main as manafa_main
from manafa.am_emanafa import AMEManafa
from manafa.emanafa import EManafa
from manafa.hunter_emanafa import HunterEManafa


def _build_parser():
    """Reconstruct the CLI parser exactly as main.main() does so we can
    drive resolution without invoking the rest of main()."""
    parser = argparse.ArgumentParser()
    parser.add_argument("-ht", "--hunter", action='store_true', default=False)
    parser.add_argument("-p", "--profile", default=None)
    parser.add_argument("-t", "--timezone", default=None)
    parser.add_argument("-pft", "--perfettofile", default=None)
    parser.add_argument("-bts", "--batstatsfile", default=None)
    parser.add_argument("-htf", "--hunterfile", default=None)
    parser.add_argument("-d", "--directory", default=None)
    parser.add_argument("-o", "--output_file", default=None)
    parser.add_argument("-s", "--time_in_secs", default=0, type=int)
    parser.add_argument("-a", "--app_package", default=None)
    parser.add_argument("-cmd", "--command", default=None)
    parser.add_argument("-pm", "--profile-mode",
                       choices=['legacy', 'energy', 'memory', 'both'], default='energy')
    parser.add_argument("-of", "--output-format", choices=['json', 'csv'], default='json')
    parser.add_argument("--force-legacy", action='store_true')
    parser.add_argument("--meminfo-period-ms", type=int, default=50)
    parser.add_argument("--battery-poll-ms", type=int, default=250)
    parser.add_argument("--trace-methods", choices=['none', 'am', 'hunter'], default=None)
    return parser


def _resolve_trace_methods(args):
    """Mirror main.main()'s post-parse defaulting logic."""
    if args.trace_methods is None:
        if args.hunter:
            args.trace_methods = 'hunter'
        elif args.app_package is not None:
            args.trace_methods = 'am'
        else:
            args.trace_methods = 'none'
    return args


class ResolveProfilerModeTests(TestCase):
    def test_force_legacy_wins_over_profile_mode(self):
        ns = argparse.Namespace(force_legacy=True, profile_mode='energy')
        self.assertEqual(manafa_main._resolve_profiler_mode(ns), 'legacy')

    def test_uses_profile_mode_when_no_force_legacy(self):
        ns = argparse.Namespace(force_legacy=False, profile_mode='memory')
        self.assertEqual(manafa_main._resolve_profiler_mode(ns), 'memory')

    def test_returns_none_when_no_mode_specified(self):
        ns = argparse.Namespace(force_legacy=False, profile_mode=None)
        self.assertIsNone(manafa_main._resolve_profiler_mode(ns))


class TraceMethodsDefaultTests(TestCase):
    def _parse(self, argv):
        return _resolve_trace_methods(_build_parser().parse_args(argv))

    def test_no_flags_defaults_to_none(self):
        self.assertEqual(self._parse([]).trace_methods, 'none')

    def test_app_package_implies_am(self):
        self.assertEqual(self._parse(['-a', 'com.foo']).trace_methods, 'am')

    def test_hunter_flag_implies_hunter(self):
        self.assertEqual(self._parse(['-ht']).trace_methods, 'hunter')

    def test_explicit_value_overrides_inference(self):
        # explicit none with -a turns off method tracing
        self.assertEqual(
            self._parse(['-a', 'com.foo', '--trace-methods', 'none']).trace_methods,
            'none')

    def test_explicit_hunter_does_not_require_app(self):
        self.assertEqual(
            self._parse(['--trace-methods', 'hunter']).trace_methods, 'hunter')


class ApplySamplingTests(TestCase):
    def test_propagates_periods_onto_manafa_instance(self):
        ns = argparse.Namespace(meminfo_period_ms=37, battery_poll_ms=125)

        class Stub:
            pass
        stub = Stub()
        manafa_main._apply_sampling(stub, ns)
        self.assertEqual(stub.meminfo_period_ms, 37)
        self.assertEqual(stub.battery_poll_ms, 125)

    def test_skips_missing_attributes(self):
        ns = argparse.Namespace()  # neither period present
        class Stub:
            pass
        stub = Stub()
        manafa_main._apply_sampling(stub, ns)
        self.assertFalse(hasattr(stub, 'meminfo_period_ms'))
        self.assertFalse(hasattr(stub, 'battery_poll_ms'))


def _base_args(**overrides):
    """Default Namespace with everything required by create_manafa."""
    ns = argparse.Namespace(
        hunter=False, hunterfile=None, app_package=None,
        force_legacy=False, profile_mode='energy',
        profile='dummy.xml', timezone='UTC',
        meminfo_period_ms=50, battery_poll_ms=250,
        trace_methods='none',
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


class CreateManafaDispatchTests(TestCase):
    """Verify create_manafa picks the right Manafa class for each trace_methods value.

    We patch the three constructors so the test never instantiates a real
    EManafa (which would invoke adb during __init__)."""

    def test_trace_methods_am_returns_ameemanafa(self):
        with patch.object(AMEManafa, '__init__', return_value=None) as am_init, \
             patch.object(EManafa, '__init__', return_value=None) as em_init, \
             patch.object(HunterEManafa, '__init__', return_value=None) as hu_init:
            m = manafa_main.create_manafa(_base_args(app_package='com.foo', trace_methods='am'))
        self.assertIsInstance(m, AMEManafa)
        am_init.assert_called_once()
        em_init.assert_not_called()
        hu_init.assert_not_called()

    def test_trace_methods_hunter_returns_huntermanafa(self):
        with patch.object(AMEManafa, '__init__', return_value=None) as am_init, \
             patch.object(EManafa, '__init__', return_value=None), \
             patch.object(HunterEManafa, '__init__', return_value=None) as hu_init:
            m = manafa_main.create_manafa(_base_args(trace_methods='hunter'))
        self.assertIsInstance(m, HunterEManafa)
        hu_init.assert_called_once()
        am_init.assert_not_called()

    def test_trace_methods_none_returns_emanafa(self):
        with patch.object(AMEManafa, '__init__', return_value=None) as am_init, \
             patch.object(EManafa, '__init__', return_value=None) as em_init, \
             patch.object(HunterEManafa, '__init__', return_value=None) as hu_init:
            m = manafa_main.create_manafa(_base_args(trace_methods='none'))
        # HunterEManafa and AMEManafa both subclass EManafa, so check exact type
        self.assertIs(type(m), EManafa)
        em_init.assert_called_once()
        am_init.assert_not_called()
        hu_init.assert_not_called()

    def test_none_with_app_package_records_app_attribute(self):
        with patch.object(EManafa, '__init__', return_value=None):
            m = manafa_main.create_manafa(
                _base_args(trace_methods='none', app_package='com.foo'))
        self.assertEqual(m.app, 'com.foo')

    def test_dispatch_propagates_profiler_mode_and_sampling(self):
        with patch.object(AMEManafa, '__init__', return_value=None):
            m = manafa_main.create_manafa(_base_args(
                trace_methods='am', app_package='com.foo',
                force_legacy=True,
                meminfo_period_ms=37, battery_poll_ms=125))
        self.assertEqual(m.profiler_mode, 'legacy')
        self.assertEqual(m.meminfo_period_ms, 37)
        self.assertEqual(m.battery_poll_ms, 125)

    def test_hunterfile_implicitly_enables_hunter(self):
        # parse-from-file workflow: -htf <file> with no --trace-methods
        # should still route to HunterEManafa
        with patch.object(HunterEManafa, '__init__', return_value=None) as hu_init:
            m = manafa_main.create_manafa(_base_args(
                trace_methods='none', hunterfile='/tmp/some.log'))
        self.assertIsInstance(m, HunterEManafa)
        hu_init.assert_called_once()
