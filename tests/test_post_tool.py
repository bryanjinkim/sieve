import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from sieve import ledger
from sieve.config import load_config
from sieve.tokens import estimate_tokens
from tests import logs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "post_tool.py")

spec = importlib.util.spec_from_file_location("post_tool", SCRIPT)
post_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(post_tool)


def bash_event(stdout="", stderr="", command="npm test", **response_extra):
    return {
        "session_id": "sess-1",
        "tool_use_id": "toolu_01",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "run"},
        "tool_response": {"stdout": stdout, "stderr": stderr, "interrupted": False,
                          "isImage": False, **response_extra},
    }


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name
        self.cfg = load_config(user_dir=os.path.join(self.tmp.name, "nouser"), env={})
        self.big, self.must_keep = logs.pytest_failure()

    def tearDown(self):
        self.tmp.cleanup()

    def handle(self, event, cfg=None):
        return post_tool.handle(event, cfg or self.cfg, self.data)

    def test_small_output_is_left_alone(self):
        self.assertIsNone(self.handle(bash_event(stdout="ok\n")))

    def test_big_stdout_is_compressed_within_budget(self):
        reply = self.handle(bash_event(stdout=self.big))
        out = reply["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        stdout = out["updatedToolOutput"]["stdout"]
        self.assertLessEqual(estimate_tokens(stdout), self.cfg.budget("bash_output"))
        for line in self.must_keep:
            self.assertIn(line.rstrip(), stdout)
        self.assertTrue(stdout.startswith("[sieve: output compressed"))

    def test_response_shape_is_preserved(self):
        reply = self.handle(bash_event(stdout=self.big, returnCodeInterpretation="ok"))
        updated = reply["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(set(updated), {"stdout", "stderr", "interrupted", "isImage",
                                        "returnCodeInterpretation"})
        self.assertEqual(updated["stderr"], "")
        self.assertFalse(updated["interrupted"])

    def test_full_output_is_saved_and_ledger_written(self):
        reply = self.handle(bash_event(stdout=self.big))
        stdout = reply["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        path = stdout.split("full output at ", 1)[1].split(",", 1)[0]
        with open(path) as f:
            self.assertIn(self.big, f.read())
        entries = list(ledger.read(self.data))
        self.assertEqual(len(entries), 1)
        self.assertGreater(entries[0]["before"], entries[0]["after"])

    def test_big_stderr_with_small_stdout(self):
        reply = self.handle(bash_event(stdout="building...\n", stderr=self.big))
        updated = reply["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(updated["stdout"], "building...\n")
        self.assertTrue(updated["stderr"].startswith("[sieve:"))

    def test_both_streams_big_fit_budget_together(self):
        cargo, _ = logs.cargo_error()
        reply = self.handle(bash_event(stdout=self.big, stderr=cargo))
        updated = reply["hookSpecificOutput"]["updatedToolOutput"]
        total = estimate_tokens(updated["stdout"]) + estimate_tokens(updated["stderr"])
        self.assertLessEqual(total, self.cfg.budget("bash_output"))
        self.assertIn("error[E0308]: mismatched types", updated["stderr"])

    def test_skips_other_tools_images_passthrough_and_disabled(self):
        other = bash_event(stdout=self.big)
        other["tool_name"] = "Read"
        self.assertIsNone(self.handle(other))
        self.assertIsNone(self.handle(bash_event(stdout=self.big, isImage=True)))
        self.assertIsNone(self.handle(bash_event(stdout=self.big, command="npm test # sieve:off")))
        off = load_config(user_dir=os.path.join(self.tmp.name, "nouser"), env={"SIEVE_OFF": "1"})
        self.assertIsNone(self.handle(bash_event(stdout=self.big), cfg=off))

    def test_odd_input_is_ignored(self):
        self.assertIsNone(self.handle({"tool_name": "Bash"}))
        self.assertIsNone(self.handle({"tool_name": "Bash", "tool_response": "text"}))
        self.assertIsNone(self.handle(bash_event(stdout=None)))


class ScriptTest(unittest.TestCase):
    """Run the real script the way Claude Code does: JSON in, JSON out."""

    def run_script(self, stdin):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "CLAUDE_PLUGIN_DATA": tmp, "HOME": tmp}
            env.pop("SIEVE_OFF", None)
            start = time.perf_counter()
            p = subprocess.run([sys.executable, SCRIPT], input=stdin, capture_output=True,
                               text=True, env=env, timeout=30)
            return p, (time.perf_counter() - start) * 1000

    def test_end_to_end(self):
        big, _ = logs.pytest_failure()
        p, ms = self.run_script(json.dumps(bash_event(stdout=big)))
        self.assertEqual(p.returncode, 0)
        reply = json.loads(p.stdout)
        self.assertIn("updatedToolOutput", reply["hookSpecificOutput"])
        self.assertLess(ms, 2000, f"{ms:.0f} ms")

    def test_garbage_input_fails_open(self):
        p, _ = self.run_script("this is not json")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")

    def test_small_output_prints_nothing(self):
        p, _ = self.run_script(json.dumps(bash_event(stdout="ok")))
        self.assertEqual((p.returncode, p.stdout), (0, ""))


class PruneTest(unittest.TestCase):
    def test_keeps_newest_and_drops_old(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "outputs", "s")
            os.makedirs(folder)
            for i in range(10):  # written directly: save_full_output prunes by itself
                path = os.path.join(folder, f"t{i}.log")
                with open(path, "w") as f:
                    f.write("x")
                os.utime(path, (1000 + i, 1000 + i))
            removed = ledger.prune(os.path.join(root, "outputs"), max_files=4,
                                   max_age=10**12, now=2000)
            self.assertEqual(removed, 6)
            left = sorted(os.listdir(os.path.join(root, "outputs", "s")))
            self.assertEqual(left, ["t6.log", "t7.log", "t8.log", "t9.log"])


if __name__ == "__main__":
    unittest.main()
