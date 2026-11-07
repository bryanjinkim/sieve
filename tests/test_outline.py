import unittest

from sieve.outline import format_outline, outline_lines

PY = """import os

class Store:
    def __init__(self):
        self.items = []

    async def fetch(self, key):
        return key

def main():
    pass
""".split("\n")


class OutlineTest(unittest.TestCase):
    def texts(self, lines, ext, **kw):
        return [t.strip() for _, t in outline_lines(lines, ext, **kw)]

    def test_python(self):
        self.assertEqual(outline_lines(PY, ".py"), [
            (3, "class Store:"),
            (4, "    def __init__(self):"),
            (7, "    async def fetch(self, key):"),
            (10, "def main():"),
        ])

    def test_javascript_and_typescript(self):
        src = ["export default function App() {", "const add = (a, b) => a + b;",
               "export interface User {", "  render() {", "let x = 5;"]
        self.assertEqual(self.texts(src, ".tsx"),
                         ["export default function App() {", "const add = (a, b) => a + b;",
                          "export interface User {", "render() {"])

    def test_go_rust_and_markdown(self):
        self.assertEqual(self.texts(["package x", "func Run() error {", "type T struct {"], ".go"),
                         ["func Run() error {", "type T struct {"])
        self.assertEqual(self.texts(["pub fn parse() {}", "impl Foo {", "let a = 1;"], ".rs"),
                         ["pub fn parse() {}", "impl Foo {"])
        self.assertEqual(self.texts(["# Title", "text", "## Install", "#nope"], ".md"),
                         ["# Title", "## Install"])

    def test_unknown_type_falls_back_to_unindented_lines(self):
        src = ["[section]", "  key = 1", "", "other = 2", "}"]
        self.assertEqual(self.texts(src, ".conf"), ["[section]", "other = 2"])

    def test_too_many_entries_keeps_top_level_first(self):
        src = []
        for i in range(50):
            src += [f"class C{i}:", f"    def m{i}(self):", "        pass"]
        entries = outline_lines(src, ".py", max_entries=50)
        self.assertEqual(len(entries), 50)
        self.assertTrue(all(t.startswith("class") for _, t in entries))
        self.assertEqual([n for n, _ in entries], sorted(n for n, _ in entries))

    def test_long_lines_are_shortened(self):
        (_, text), = outline_lines(["def f(" + "a, " * 100 + "):"], ".py")
        self.assertLessEqual(len(text), 125)

    def test_format_aligns_line_numbers(self):
        self.assertEqual(format_outline([(3, "a"), (120, "b")]), "  3: a\n120: b")


if __name__ == "__main__":
    unittest.main()
