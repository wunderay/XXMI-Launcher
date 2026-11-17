import importlib.util
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'perf' / 'compare.py'
spec = importlib.util.spec_from_file_location('perf_compare', SOURCE)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class RegressionGateTests(unittest.TestCase):
    metric = compare.Metric('t', 's', rel=0.25, floor=0.05)

    def test_growth_must_pass_both_the_tolerance_and_the_noise_floor(self):
        self.assertFalse(compare.regressed(self.metric, 1.0, 1.25))   # at the tolerance
        self.assertTrue(compare.regressed(self.metric, 1.0, 1.26))    # beyond both
        self.assertFalse(compare.regressed(self.metric, 0.1, 0.149))  # +49% but under the floor
        self.assertTrue(compare.regressed(self.metric, 0.1, 0.16))
        self.assertFalse(compare.regressed(self.metric, 1.0, 0.5))    # improvements never fail

    def test_exact_metrics_fail_on_any_growth(self):
        exact = compare.Metric('n', '', rel=0.0, floor=0)
        self.assertFalse(compare.regressed(exact, 0, 0))
        self.assertTrue(compare.regressed(exact, 0, 1))

    def test_report_fails_only_regressed_metrics(self):
        base = {key: 100.0 for key in compare.METRICS}
        head = dict(base, prelaunch_repeat_ms=200.0, gui_open_first_s=50.0)
        summary, failures = compare.report(base, head)
        self.assertEqual(failures, [compare.METRICS['prelaunch_repeat_ms'].name])
        self.assertIn('❌ regression', summary)
        self.assertEqual(compare.report(base, dict(base))[1], [])


class InterleavingTests(unittest.TestCase):
    def test_rounds_alternate_order_after_a_warm_up_and_keep_each_best(self):
        calls, values = [], {'base': iter([9, 5, 7, 3, 4]), 'head': iter([9, 6, 2, 8, 5])}

        def fake_measure(src, mods, game_mods, runs):
            calls.append((src, runs))
            return {'m': next(values[src])}

        base, head = compare.measure_interleaved('base', 'head', None, None, 7, 4, fake_measure)
        self.assertEqual([src for src, _ in calls], ['base', 'head', 'base', 'head', 'head', 'base',
                                                     'base', 'head', 'head', 'base'])
        self.assertEqual([runs for _, runs in calls[:2]], [1, 1])  # warm-up is cheap and unmeasured
        self.assertEqual(base, {'m': 3})   # 9 (warm-up) is ignored
        self.assertEqual(head, {'m': 2})


class ExitStatusTests(unittest.TestCase):
    def run_main(self, base, head):
        with mock.patch.object(compare.shutil, 'which', return_value='/usr/bin/tool'), \
                mock.patch.object(compare.subprocess, 'run'), \
                mock.patch.object(compare, 'measure_interleaved', return_value=(base, head)), \
                mock.patch.object(sys, 'argv', ['compare.py', '--base', 'b', '--head', 'h']), \
                mock.patch.dict(compare.os.environ, {}, clear=False) as env:
            env.pop('GITHUB_STEP_SUMMARY', None)
            with mock.patch('builtins.print'):
                return compare.main()

    def test_a_regression_fails_the_process(self):
        base = {key: 100.0 for key in compare.METRICS}
        self.assertEqual(self.run_main(base, dict(base)), 0)
        self.assertEqual(self.run_main(base, dict(base, gui_open_repeat_s=200.0)), 1)

    def test_a_failing_benchmark_raises_instead_of_passing(self):
        failed = SimpleNamespace(returncode=1, stdout='{"x": 1}', stderr='boom')
        with mock.patch.object(compare.subprocess, 'run', return_value=failed):
            with self.assertRaises(RuntimeError):
                compare.run_json(['bench'])


class MeasureCommandTests(unittest.TestCase):
    gui = {'gui_open_s': 1, 'cpu_s': 1, 'max_rss_mb': 1, 'rss_mb': 1, 'image_resize_calls': 1}

    def measure(self, src):
        calls = []

        def fake_run_json(command, env=None, xvfb=False):
            calls.append(([str(part) for part in command], env))
            name = Path(str(command[1])).name
            if name == 'bench_prelaunch.py':
                timing = {'min_ms': 1, 'fs_syscalls': 1}
                return {'repeat': timing, 'first': timing}
            if name == 'simulate_game_load.py':
                return {'median_load_s': 1}
            if name == 'bench_gui_startup.py':
                return {'first_start': self.gui, 'later_starts': self.gui}
            return {'stable': {'body_bytes': 1, 'counted_requests': 0}}

        with mock.patch.object(compare, 'run_json', fake_run_json):
            compare.measure(src, Path('/mods'), Path('/game'), 3)
        return {Path(command[1]).name: (command, env) for command, env in calls}

    def test_each_version_starts_with_its_own_themes_and_locale(self):
        src = Path('/trees/base/src/xxmi_launcher')
        gui_command, _ = self.measure(src)['bench_gui_startup.py']
        self.assertEqual(gui_command[gui_command.index('--assets') + 1], str(Path('/trees/base')))
        self.assertEqual(gui_command[gui_command.index('--src') + 1], str(src))

    def test_each_benchmark_imports_the_version_it_measures(self):
        src = Path('/trees/head/src/xxmi_launcher')
        calls = self.measure(src)
        for name in ('bench_prelaunch.py', 'simulate_game_load.py', 'bench_update_check.py'):
            with self.subTest(benchmark=name):
                self.assertEqual(calls[name][1]['PYTHONPATH'], str(src))

    def test_prefetch_is_simulated_only_for_versions_that_have_it(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'src' / 'xxmi_launcher'
            (src / 'core').mkdir(parents=True)
            self.assertNotIn('--prefetch', self.measure(src)['simulate_game_load.py'][0])
            (src / 'core' / 'mod_prefetch.py').write_text('', encoding='utf-8')
            self.assertIn('--prefetch', self.measure(src)['simulate_game_load.py'][0])


if __name__ == '__main__':
    unittest.main()
