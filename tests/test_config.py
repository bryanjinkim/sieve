import json
import os
import tempfile
import unittest

from sieve.config import MIN_BUDGET, load_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = os.path.join(self.tmp.name, "home", ".claude")
        self.project = os.path.join(self.tmp.name, "project")
        os.makedirs(self.user)
        os.makedirs(os.path.join(self.project, ".claude"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, where, content):
        path = os.path.join(self.user if where == "user" else os.path.join(self.project, ".claude"),
                            "sieve.json")
        with open(path, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def load(self, env=None):
        return load_config(project_dir=self.project, user_dir=self.user, env=env or {})

    # --- layering

    def test_defaults_load_with_no_overrides(self):
        cfg = self.load()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.budget("bash_output"), 2500)
        self.assertEqual(cfg.warnings, [])
        self.assertEqual(len(cfg.sources), 1)

    def test_user_overrides_defaults(self):
        self.write("user", {"budgets": {"bash_output": 4000}})
        self.assertEqual(self.load().budget("bash_output"), 4000)

    def test_project_overrides_user(self):
        self.write("user", {"budgets": {"bash_output": 4000}})
        self.write("project", {"budgets": {"bash_output": 1200}})
        cfg = self.load()
        self.assertEqual(cfg.budget("bash_output"), 1200)
        self.assertEqual(len(cfg.sources), 3)

    def test_deep_merge_keeps_untouched_defaults(self):
        self.write("project", {"budgets": {"bash_output": 1200}})
        cfg = self.load()
        self.assertEqual(cfg.budget("file_read"), 10000)
        self.assertEqual(cfg.compress_options["tail"], 40)

    def test_env_var_turns_sieve_off(self):
        self.assertFalse(self.load(env={"SIEVE_OFF": "1"}).enabled)
        self.assertTrue(self.load(env={"SIEVE_OFF": "0"}).enabled)

    # --- forgiving validation: bad input never crashes, always warns

    def test_invalid_json_is_skipped_with_warning(self):
        self.write("project", "{ not json")
        cfg = self.load()
        self.assertEqual(cfg.budget("bash_output"), 2500)
        self.assertEqual(len(cfg.warnings), 1)

    def test_wrong_type_is_ignored(self):
        self.write("project", {"budgets": {"bash_output": "lots"}, "enabled": "yes"})
        cfg = self.load()
        self.assertEqual(cfg.budget("bash_output"), 2500)
        self.assertTrue(cfg.enabled)
        self.assertEqual(len(cfg.warnings), 2)

    def test_too_small_budget_is_rejected(self):
        self.write("project", {"budgets": {"bash_output": MIN_BUDGET - 1}})
        cfg = self.load()
        self.assertEqual(cfg.budget("bash_output"), 2500)
        self.assertIn("at least", cfg.warnings[0])

    def test_warn_at_must_be_a_fraction(self):
        self.write("project", {"session": {"warn_at": 80}})
        self.assertEqual(self.load().warn_at, 0.8)

    def test_unknown_key_warns(self):
        self.write("project", {"budgets": {"bash_ouptut": 3000}})
        self.assertIn("unknown setting 'budgets.bash_ouptut'", self.load().warnings[0])

    def test_object_replaced_by_scalar_is_ignored(self):
        self.write("project", {"budgets": 5})
        self.assertEqual(self.load().budget("bash_output"), 2500)

    def test_bad_regex_is_ignored(self):
        self.write("project", {"passthrough_patterns": ["(unclosed"]})
        cfg = self.load()
        self.assertTrue(cfg.is_passthrough("make # sieve:off"))
        self.assertEqual(len(cfg.warnings), 1)

    def test_top_level_must_be_object(self):
        self.write("project", "[1, 2, 3]")
        self.assertEqual(len(self.load().warnings), 1)

    # --- passthrough

    def test_passthrough_marker(self):
        cfg = self.load()
        self.assertTrue(cfg.is_passthrough("npm test # sieve:off"))
        self.assertTrue(cfg.is_passthrough("npm test #sieve: off"))
        self.assertFalse(cfg.is_passthrough("npm test"))


if __name__ == "__main__":
    unittest.main()
