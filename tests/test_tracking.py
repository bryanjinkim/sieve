import importlib.util
import json
import os
import tempfile
import unittest

from sieve import ledger
from sieve.config import load_config
from sieve.transcript import last_context, latest_session_path, parse, project_log_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prompt_check = load_script("prompt_check")
report = load_script("report")


def assistant(mid, ts, ctx_read, out=100, tool=None, sidechain=False):
    content = [{"type": "text", "text": "hi"}]
    if tool:
        content.append({"type": "tool_use", "id": tool[0], "name": tool[1], "input": tool[2]})
    return {"type": "assistant", "isSidechain": sidechain, "timestamp": ts,
            "message": {"id": mid, "model": "claude-test", "content": content,
                        "usage": {"input_tokens": 10, "cache_read_input_tokens": ctx_read,
                                  "cache_creation_input_tokens": 90, "output_tokens": out}}}


def tool_result(tool_use_id, text, ts):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        os.makedirs(self.data)
        self.log = os.path.join(self.tmp.name, "sess-1.jsonl")
        rows = [
            assistant("m1", "2026-11-06T10:00:00Z", 1000,
                      tool=("t1", "Bash", {"command": "npm test\nmore"})),
            # the same response split over a second line: must not be double counted
            assistant("m1", "2026-11-06T10:00:00Z", 1000),
            tool_result("t1", "PASS\n" * 2000, "2026-11-06T10:00:05Z"),
            "not json at all",
            assistant("m2", "2026-11-06T10:01:00Z", 5000,
                      tool=("t2", "Read", {"file_path": "/src/app.py"})),
            tool_result("t2", [{"type": "text", "text": "def main(): pass"}], "2026-11-06T10:01:02Z"),
            assistant("m3", "2026-11-06T10:02:00Z", 9000),
            assistant("side", "2026-11-06T10:03:00Z", 999999, sidechain=True),
        ]
        with open(self.log, "w") as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        self.cfg = load_config(user_dir=os.path.join(self.tmp.name, "nouser"), env={})

    def tearDown(self):
        self.tmp.cleanup()


class TranscriptTest(Fixture):
    def test_calls_are_deduplicated_by_message_id(self):
        s = parse(self.log)
        self.assertEqual([c.context for c in s.calls], [1100, 5100, 9100, 1000099])

    def test_tool_results_are_matched_to_their_tool(self):
        s = parse(self.log)
        self.assertEqual([(r.tool, r.label) for r in s.results],
                         [("Bash", "npm test"), ("Read", "/src/app.py")])
        self.assertGreater(s.results[0].tokens, s.results[1].tokens)

    def test_totals(self):
        s = parse(self.log)
        self.assertEqual(s.output, 400)
        self.assertEqual(s.calls_after(s.calls[0].ts), 2)  # sidechain call not counted
        self.assertEqual(s.context_now, 9100)

    def test_last_context_reads_the_tail_and_skips_sidechains(self):
        self.assertEqual(last_context(self.log), 9100)
        self.assertEqual(last_context(self.log, tail_bytes=600), 9100)
        self.assertEqual(last_context(os.path.join(self.tmp.name, "missing.jsonl")), 0)

    def test_project_log_dir_encoding(self):
        self.assertEqual(os.path.basename(project_log_dir("/Users/me/My Project.v2")),
                         "-Users-me-My-Project-v2")

    def test_latest_session_prefers_current_project(self):
        root = os.path.join(self.tmp.name, "projects")
        mine = os.path.join(project_log_dir("/work/app", root), "a.jsonl")
        other = os.path.join(root, "-other", "b.jsonl")
        for path in (mine, other):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
        os.utime(mine, (1, 1))  # older, but in the current project
        self.assertEqual(latest_session_path("/work/app", root), mine)
        self.assertEqual(latest_session_path("/elsewhere", root), other)


class PromptCheckTest(Fixture):
    def check(self, budget):
        cfg = load_config(user_dir=os.path.join(self.tmp.name, "nouser"), env={})
        cfg.data["budgets"]["session_context"] = budget
        event = {"session_id": "sess-1", "transcript_path": self.log}
        return prompt_check.handle(event, cfg, self.data)

    def test_quiet_under_threshold(self):
        self.assertIsNone(self.check(100_000))  # 9,100 is 9%

    def test_warns_once_per_band(self):
        first = self.check(11_000)  # 83%
        self.assertIn("83%", first["systemMessage"])
        self.assertEqual(first["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIsNone(self.check(11_000))  # same band: stay quiet
        self.assertIn("91%", self.check(10_000)["systemMessage"])  # next band
        self.assertIn("130%", self.check(7_000)["systemMessage"])

    def test_ignores_missing_transcript(self):
        self.assertIsNone(prompt_check.handle({"session_id": "x"}, self.cfg, self.data))


class ReportTest(Fixture):
    def test_report_without_savings(self):
        text = report.build_report([parse(self.log)], self.data, 150_000, "this session", 0)
        self.assertIn("Conversation size now | **9,100** tokens", text)
        self.assertIn("| Bash | 1 |", text)
        self.assertIn("Nothing compressed or guarded yet", text)

    def test_report_with_savings_and_compounding(self):
        s = parse(self.log)
        ledger.record(self.data, {"session": "sess-1", "tool": "Bash", "before": 5000, "after": 500})
        # pin the timestamp so exactly two calls (m2, m3) come after it
        entries = list(ledger.read(self.data))
        entries[0]["ts"] = s.calls[0].ts + 1
        with open(os.path.join(self.data, ledger.LEDGER_FILE), "w") as f:
            f.write(json.dumps(entries[0]) + "\n")
        text = report.build_report([s], self.data, 150_000, "this session", 0)
        self.assertIn("4,500 (90%)", text)
        # saved 4,500 tokens that would have been re-read on 2 later calls (m2, m3)
        self.assertIn("**9,000 fewer input tokens processed**", text)

    def test_other_sessions_savings_are_excluded(self):
        ledger.record(self.data, {"session": "someone-else", "tool": "Bash", "before": 900, "after": 1})
        text = report.build_report([parse(self.log)], self.data, 150_000, "this session", 0)
        self.assertIn("Nothing compressed or guarded yet", text)

    def test_fmt(self):
        self.assertEqual([report.fmt(n) for n in (950, 9_500, 12_400, 2_300_000)],
                         ["950", "9,500", "12k", "2.3M"])


if __name__ == "__main__":
    unittest.main()
