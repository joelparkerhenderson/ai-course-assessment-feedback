"""Keep llms.json and llms.txt in step with the code, so AI assistants never see a stale command or option."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tools.update_llms_json import commands, versions

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "https://github.com/joelparkerhenderson/ai-course-assessment-feedback/blob/main/"


def heading_anchors(path: Path) -> set[str]:
    """GitHub-style anchors for every Markdown heading in a file."""
    headings = re.findall(r"^#+ (.+)$", path.read_text(encoding="utf-8"), re.M)
    return {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-") for h in headings}


class LlmsFilesTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / "llms.json").read_text(encoding="utf-8"))

    def test_commands_match_the_cli(self):
        # Every subcommand, argument and option, with help, choices and defaults.
        self.assertEqual(self.doc["commands"], commands(), "run: python3 tools/update_llms_json.py")

    def test_versions_match_the_code(self):
        self.assertEqual(self.doc["versions"], versions(), "run: python3 tools/update_llms_json.py")

    def assert_link_resolves(self, url: str) -> None:
        """A repository link must name an existing file and, if it has one, an existing heading."""
        path, _, anchor = url.removeprefix(PREFIX).partition("#")
        self.assertTrue((ROOT / path).exists(), url)
        if anchor:
            self.assertIn(anchor, heading_anchors(ROOT / path), url)

    def test_llms_txt_links_resolve(self):
        text = (ROOT / "llms.txt").read_text(encoding="utf-8")
        urls = re.findall(re.escape(PREFIX) + r"[^)\s]+", text)
        self.assertTrue(urls)
        for url in urls:
            with self.subTest(url=url):
                self.assert_link_resolves(url)

    def test_llms_json_links_resolve(self):
        for url in [*self.doc["docs"].values(), *self.doc["skills"].values()]:
            with self.subTest(url=url):
                self.assert_link_resolves(url)


if __name__ == "__main__":
    unittest.main()
