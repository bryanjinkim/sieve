import random
import re
import time
import unittest

from sieve.compress import (
    clean,
    clip_long_lines,
    compress,
    dedupe,
    fit_to_budget,
    score_line,
    shape,
)
from sieve.tokens import estimate_tokens
from tests import logs

OMITTED = re.compile(r"^\[\.\.\. (\d+) lines omitted \.\.\.\]$")


# ------------------------------------------------------------ individual stages


class CleanTest(unittest.TestCase):
    def test_strips_color_codes(self):
        self.assertEqual(clean("\x1b[31mred\x1b[0m text"), ["red text"])

    def test_progress_bar_keeps_final_state(self):
        self.assertEqual(clean("10%\r50%\r100%"), ["100%"])

    def test_squeezes_blank_runs_and_trims_ends(self):
        self.assertEqual(clean("\n\na\n\n\n\nb\n\n"), ["a", "", "b"])

    def test_strips_trailing_spaces(self):
        self.assertEqual(clean("a   \nb\t"), ["a", "b"])


class DedupeTest(unittest.TestCase):
    def test_shape_ignores_numbers_hex_and_spacing(self):
        self.assertEqual(shape("PASS test_7 [  9%]"), shape("PASS test_812 [ 10%]"))
        self.assertEqual(shape("commit 3f9a2b1c done"), shape("commit 9e8d7c6b done"))

    def test_collapses_run_keeping_first_last_and_count(self):
        lines = [f"PASS test_{i}" for i in range(10)]
        self.assertEqual(
            dedupe(lines),
            ["PASS test_0", "    [... 8 more similar lines ...]", "PASS test_9"],
        )

    def test_short_runs_are_left_alone(self):
        lines = ["PASS test_1", "PASS test_2", "done"]
        self.assertEqual(dedupe(lines), lines)

    def test_a_different_line_breaks_the_run(self):
        lines = [f"PASS t{i}" for i in range(5)] + ["FAIL t5"] + [f"PASS t{i}" for i in range(6, 11)]
        out = dedupe(lines)
        self.assertIn("FAIL t5", out)
        self.assertEqual(sum("more similar" in l for l in out), 2)


class ClipTest(unittest.TestCase):
    def test_long_line_is_clipped_with_marker(self):
        out = clip_long_lines(["x" * 5000], max_chars=400)[0]
        self.assertLess(len(out), 500)
        self.assertIn("chars clipped", out)

    def test_short_lines_untouched(self):
        self.assertEqual(clip_long_lines(["short"]), ["short"])


class ScoreTest(unittest.TestCase):
    def test_scores_by_importance(self):
        self.assertEqual(score_line("ValueError: bad input"), 10)
        self.assertEqual(score_line("java.lang.NullPointerException"), 10)
        self.assertEqual(score_line("error: could not compile"), 10)
        self.assertEqual(score_line("E   AssertionError"), 10)
        self.assertEqual(score_line("== 3 failed, 10 passed in 2s =="), 10)  # 'failed' wins
        self.assertEqual(score_line("== 10 passed in 2s =="), 8)
        self.assertEqual(score_line('  File "a.py", line 3, in f'), 6)
        self.assertEqual(score_line("npm WARN deprecated foo"), 5)
        self.assertEqual(score_line("Compiling foo v1.0"), 0)


class FitTest(unittest.TestCase):
    def setUp(self):
        self.lines = [f"noise line {i} with some filler words here" for i in range(1000)]
        self.lines[500] = "error: the one line that matters"

    def test_keeps_head_tail_and_important_line_with_context(self):
        out = fit_to_budget(self.lines, budget=800, head=5, tail=5, context=2)
        self.assertEqual(out[:5], self.lines[:5])
        self.assertEqual(out[-5:], self.lines[-5:])
        i = out.index("error: the one line that matters")
        self.assertEqual(out[i - 2:i + 3], self.lines[498:503])

    def test_every_original_line_is_kept_or_counted(self):
        out = fit_to_budget(self.lines, budget=800)
        kept = [l for l in out if not OMITTED.match(l)]
        omitted = sum(int(m.group(1)) for l in out for m in [OMITTED.match(l)] if m)
        self.assertEqual(len(kept) + omitted, len(self.lines))

    def test_kept_lines_stay_in_original_order(self):
        out = [l for l in fit_to_budget(self.lines, budget=800) if not OMITTED.match(l)]
        positions = [self.lines.index(l) for l in out]
        self.assertEqual(positions, sorted(positions))


# ------------------------------------------------------- whole-pipeline guarantees


class PipelineGuaranteesTest(unittest.TestCase):
    BUDGETS = (400, 800, 1500, 3000)

    def test_small_output_is_returned_untouched(self):
        r = compress("hello\nworld", budget=100)
        self.assertEqual(r.text, "hello\nworld")
        self.assertFalse(r.compressed)

    def test_never_exceeds_budget(self):
        for name, make in logs.ALL.items():
            text, _ = make()
            for budget in self.BUDGETS:
                with self.subTest(log=name, budget=budget):
                    r = compress(text, budget=budget)
                    self.assertLessEqual(estimate_tokens(r.text), budget)

    def test_never_grows_the_output(self):
        for name, make in logs.ALL.items():
            text, _ = make()
            with self.subTest(log=name):
                self.assertLessEqual(compress(text, budget=1500).tokens_after,
                                     estimate_tokens(text))

    def test_keeps_errors_and_summaries(self):
        for name, make in logs.ALL.items():
            text, must_keep = make()
            out = compress(text, budget=1500).text.split("\n")
            for line in must_keep:
                with self.subTest(log=name, line=line):
                    self.assertIn(line.rstrip(), out)

    def test_saves_a_lot_on_noisy_logs(self):
        for name, make in logs.ALL.items():
            text, _ = make()
            r = compress(text, budget=1500)
            with self.subTest(log=name, saved=r.tokens_saved / r.tokens_before):
                self.assertGreater(r.tokens_saved / r.tokens_before, 0.8)

    def test_is_deterministic(self):
        text, _ = logs.cargo_error()
        self.assertEqual(compress(text, budget=900).text, compress(text, budget=900).text)

    def test_reported_stats_match_the_text(self):
        text, _ = logs.python_traceback()
        r = compress(text, budget=1000)
        self.assertEqual(r.lines_after, len(r.text.split("\n")))
        self.assertEqual(r.tokens_before, estimate_tokens(text))


class EdgeCaseTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(compress("").text, "")

    def test_one_giant_line_without_newlines(self):
        r = compress("x" * 200_000, budget=500)
        self.assertLessEqual(estimate_tokens(r.text), 500)

    def test_only_blank_lines(self):
        r = compress("\n" * 50_000, budget=100)
        self.assertLessEqual(estimate_tokens(r.text), 100)

    def test_non_ascii(self):
        text = "\n".join(f"処理中 ファイル {i} を読み込み中 ✓" for i in range(3000))
        r = compress(text, budget=500)
        self.assertLessEqual(estimate_tokens(r.text), 500)


class RandomizedTest(unittest.TestCase):
    """Throw 40 random logs at the pipeline and check the guarantees hold."""

    def test_random_logs(self):
        vocab = "load parse build emit link fetch cache hit miss retry ok done step".split()
        for seed in range(40):
            rng = random.Random(seed)
            lines = [" ".join(rng.choices(vocab, k=rng.randint(2, 12))) + f" #{rng.randint(0, 10**6)}"
                     for _ in range(rng.randint(200, 3000))]
            err = f"error: random failure {seed}"
            lines.insert(rng.randrange(len(lines)), err)
            budget = rng.choice((600, 1200, 2500))
            r = compress("\n".join(lines), budget=budget)
            with self.subTest(seed=seed, budget=budget):
                self.assertLessEqual(estimate_tokens(r.text), budget)
                self.assertIn(err, r.text)


class SpeedTest(unittest.TestCase):
    def test_large_varied_log_is_fast(self):
        text, _ = logs.cargo_error(units=2000)
        start = time.perf_counter()
        compress(text, budget=1500)
        ms = (time.perf_counter() - start) * 1000
        # Target is under 100 ms on a laptop; the bound is looser so slower
        # machines don't fail the suite.
        self.assertLess(ms, 250, f"{ms:.0f} ms")


if __name__ == "__main__":
    unittest.main()
