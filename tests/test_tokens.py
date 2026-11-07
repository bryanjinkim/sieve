import os
import tempfile
import unittest

from sieve.tokens import chars_for_tokens, estimate_file_tokens, estimate_tokens

PROSE = (
    "Most tokens in a coding session are not the prompts you type. They are "
    "tool results: long build logs, test runs and whole files that the model "
    "reads. Sieve sits between the tools and the model and keeps what matters."
)

CODE = '''
def compress(text: str, budget: int = 2000) -> CompressionResult:
    lines = text.splitlines()
    if estimate_tokens(text) <= budget:
        return CompressionResult(text=text, before=len(lines), after=len(lines))
    scored = [(score_line(l), i) for i, l in enumerate(lines)]
'''


class EstimateTokensTest(unittest.TestCase):
    def test_empty_text_costs_nothing(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_any_text_costs_at_least_one(self):
        self.assertEqual(estimate_tokens("a"), 1)

    def test_prose_is_about_four_chars_per_token(self):
        ratio = len(PROSE) / estimate_tokens(PROSE)
        self.assertTrue(3.5 <= ratio <= 5.5, ratio)

    def test_code_is_denser_than_prose(self):
        prose_ratio = len(PROSE) / estimate_tokens(PROSE)
        code_ratio = len(CODE) / estimate_tokens(CODE)
        self.assertLess(code_ratio, prose_ratio)

    def test_more_text_never_costs_less(self):
        self.assertGreaterEqual(estimate_tokens(PROSE + PROSE), estimate_tokens(PROSE))

    def test_repeated_punctuation_is_cheap(self):
        # 80 dashes is one punctuation run, far cheaper than 80 separate tokens
        self.assertLessEqual(estimate_tokens("-" * 80), 40)

    def test_non_ascii_costs_about_one_per_character(self):
        self.assertEqual(estimate_tokens("日本語のテキスト"), 8)

    def test_long_numbers_split_into_chunks(self):
        self.assertEqual(estimate_tokens("123456789"), 3)


class FileAndInverseTest(unittest.TestCase):
    def test_file_estimate_uses_size(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as f:
            f.write(CODE * 100)
            path = f.name
        try:
            from_size = estimate_file_tokens(path)
            from_text = estimate_tokens(CODE * 100)
            # size-only estimate should land within 35% of the full estimate
            self.assertLess(abs(from_size - from_text) / from_text, 0.35)
        finally:
            os.unlink(path)

    def test_missing_file_is_zero(self):
        self.assertEqual(estimate_file_tokens("/no/such/file.txt"), 0)

    def test_chars_for_tokens_stays_under_budget_for_prose(self):
        budget = 50
        cut = PROSE[: chars_for_tokens(budget)]
        self.assertLessEqual(estimate_tokens(cut), budget * 1.1)


if __name__ == "__main__":
    unittest.main()
