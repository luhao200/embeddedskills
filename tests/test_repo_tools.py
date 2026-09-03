import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.package_release import build_archives, write_checksums
from tools.repo_checks import local_link_target, parse_frontmatter


class RepositoryCheckTests(unittest.TestCase):
    def test_frontmatter_parser_reads_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_text(
                "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_frontmatter(path)["name"], "demo")

    def test_local_link_parser_ignores_urls_and_anchors(self) -> None:
        self.assertIsNone(local_link_target("https://example.com/doc"))
        self.assertIsNone(local_link_target("#section"))
        self.assertEqual(local_link_target("docs/guide.md#start"), "docs/guide.md")


class ReleasePackageTests(unittest.TestCase):
    def test_release_archives_exclude_tests_and_are_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            (skill / "tests").mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (skill / "script.py").write_text("print('ok')\n", encoding="utf-8")
            (skill / "tests" / "test_demo.py").write_text("", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")

            first = root / "first"
            second = root / "second"
            first_archives = build_archives(root, first, "1.0.0")
            second_archives = build_archives(root, second, "1.0.0")

            self.assertEqual(
                [path.read_bytes() for path in first_archives],
                [path.read_bytes() for path in second_archives],
            )
            with zipfile.ZipFile(first / "embeddedskills-demo-1.0.0.zip") as archive:
                self.assertIn("demo/SKILL.md", archive.namelist())
                self.assertNotIn("demo/tests/test_demo.py", archive.namelist())

            checksum_file = write_checksums(first, first_archives)
            self.assertIn("embeddedskills-demo-1.0.0.zip", checksum_file.read_text())


if __name__ == "__main__":
    unittest.main()
