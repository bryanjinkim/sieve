"""Structural checks: the plugin's files fit together the way Claude Code expects."""

import importlib.util
import json
import os
import re
import unittest

from sieve.config import load_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frontmatter(path):
    text = open(path).read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    fields = dict(line.split(":", 1) for line in m.group(1).split("\n") if ":" in line)
    return {k.strip(): v.strip() for k, v in fields.items()}, m.group(2)


class PluginStructureTest(unittest.TestCase):
    def test_manifest(self):
        manifest = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(manifest["name"], "sieve")
        self.assertRegex(manifest["name"], r"^[a-z0-9-]+$")
        self.assertFalse(manifest["name"].startswith(("claude-", "anthropic-")))

    def test_every_hook_runs_an_existing_script_in_exec_form(self):
        hooks = json.load(open(os.path.join(ROOT, "hooks", "hooks.json")))["hooks"]
        self.assertEqual(set(hooks), {"PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart"})
        for event, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    with self.subTest(event=event):
                        self.assertEqual(hook["type"], "command")
                        self.assertEqual(hook["command"], "python3")  # exec form: no shell
                        script, = hook["args"]
                        self.assertTrue(script.startswith("${CLAUDE_PLUGIN_ROOT}/scripts/"))
                        path = script.replace("${CLAUDE_PLUGIN_ROOT}", ROOT)
                        self.assertTrue(os.path.isfile(path), path)
                        self.assertLessEqual(hook["timeout"], 30)

    def test_scout_agent_is_cheap_and_read_only(self):
        fields, body = frontmatter(os.path.join(ROOT, "agents", "scout.md"))
        self.assertEqual(fields["name"], "scout")
        self.assertEqual(fields["model"], "haiku")
        tools = {t.strip() for t in fields["tools"].split(",")}
        self.assertEqual(tools, {"Read", "Grep", "Glob"})  # no Bash, Edit or Write
        self.assertIn("proactively", fields["description"])
        self.assertIn("200 words", body)

    def test_report_skill_points_at_the_report_script(self):
        fields, body = frontmatter(os.path.join(ROOT, "skills", "report", "SKILL.md"))
        self.assertEqual(fields["name"], "report")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/report.py", body)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", "report.py")))


class SessionStartTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "session_start", os.path.join(ROOT, "scripts", "session_start.py"))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_tips_are_short_and_mention_the_scout(self):
        from sieve.tokens import estimate_tokens
        text = self.mod.handle({}, load_config(user_dir="/nonexistent", env={}))
        self.assertIn("sieve:scout", text)
        self.assertLess(estimate_tokens(text), 200)

    def test_tips_can_be_turned_off(self):
        cfg = load_config(user_dir="/nonexistent", env={})
        cfg.data["session"]["tips"] = False
        self.assertIsNone(self.mod.handle({}, cfg))
        self.assertIsNone(self.mod.handle({}, load_config(user_dir="/nonexistent",
                                                           env={"SIEVE_OFF": "1"})))


if __name__ == "__main__":
    unittest.main()
