import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

from sieve import ledger
from sieve.config import load_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "pre_read.py")
spec = importlib.util.spec_from_file_location("pre_read", SCRIPT)
pre_read = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre_read)


def big_python(classes=300):
    out = ["import os", ""]
    for i in range(classes):
        out += [f"class Handler{i}:", f"    def handle_{i}(self, request, response, context):",
                f"        value = compute_something_expensive(request.body, {i}, context.flags)",
                "        return response.write(value)", ""]
    return "\n".join(out)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        os.makedirs(self.data)
        self.cfg = load_config(user_dir=os.path.join(self.tmp.name, "nouser"), env={})
        self.big = self.write("handlers.py", big_python())
        self.small = self.write("small.py", "def f():\n    return 1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content, mode="w"):
        path = os.path.join(self.tmp.name, name)
        with open(path, mode) as f:
            f.write(content)
        return path

    def read(self, path, **extra):
        event = {"session_id": "s1", "tool_name": "Read",
                 "tool_input": {"file_path": path, **extra}}
        return pre_read.handle(event, self.cfg, self.data)

    def test_small_file_is_allowed(self):
        self.assertIsNone(self.read(self.small))

    def test_range_reads_are_always_allowed(self):
        self.assertIsNone(self.read(self.big, offset=100, limit=40))
        self.assertIsNone(self.read(self.big, limit=40))

    def test_big_file_is_denied_with_outline(self):
        reply = self.read(self.big)["hookSpecificOutput"]
        self.assertEqual(reply["hookEventName"], "PreToolUse")
        self.assertEqual(reply["permissionDecision"], "deny")
        reason = reply["permissionDecisionReason"]
        self.assertIn("offset/limit", reason)
        self.assertIn("class Handler0:", reason)
        self.assertIn("3: class Handler0:", reason.replace("   ", " ").replace("  ", " "))
        self.assertLess(len(reason), 6000)

    def test_second_identical_request_is_allowed(self):
        self.assertIsNotNone(self.read(self.big))
        self.assertIsNone(self.read(self.big))      # escape hatch
        self.assertIsNotNone(self.read(self.big))   # and the guard re-arms

    def test_denial_is_recorded_in_ledger(self):
        self.read(self.big)
        entry, = ledger.read(self.data)
        self.assertEqual(entry["tool"], "Read")
        self.assertGreater(entry["before"], entry["after"])
        self.assertTrue(entry["estimated"])

    def test_skips_images_binaries_missing_and_other_tools(self):
        png = self.write("big.png", b"\x89PNG" + b"\x00" * 200_000, mode="wb")
        blob = self.write("data.dat", b"\x00\x01" * 200_000, mode="wb")
        self.assertIsNone(self.read(png))
        self.assertIsNone(self.read(blob))
        self.assertIsNone(self.read(os.path.join(self.tmp.name, "missing.py")))
        event = {"tool_name": "Write", "tool_input": {"file_path": self.big}}
        self.assertIsNone(pre_read.handle(event, self.cfg, self.data))

    def test_disabled(self):
        off = load_config(user_dir=os.path.join(self.tmp.name, "nouser"), env={"SIEVE_OFF": "1"})
        event = {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": self.big}}
        self.assertIsNone(pre_read.handle(event, off, self.data))

    def test_script_fails_open_on_garbage(self):
        p = subprocess.run([sys.executable, SCRIPT], input="{{nope", capture_output=True, text=True,
                           env={**os.environ, "CLAUDE_PLUGIN_DATA": self.data}, timeout=30)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_script_end_to_end(self):
        event = {"session_id": "s2", "tool_name": "Read", "tool_input": {"file_path": self.big}}
        env = {**os.environ, "CLAUDE_PLUGIN_DATA": self.data, "HOME": self.tmp.name}
        env.pop("SIEVE_OFF", None)
        p = subprocess.run([sys.executable, SCRIPT], input=json.dumps(event), capture_output=True,
                           text=True, env=env, timeout=30)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
