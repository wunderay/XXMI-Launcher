import importlib.util
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
