import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.package_release import build_archives, write_checksums
from tools.repo_checks import (
    check_i18n_entrypoints,
    check_skill_metadata,
    local_link_target,
    matching_files,
    parse_frontmatter,
)
from tools.run_tests import find_test_directories


class RepositoryCheckTests(unittest.TestCase):
    def test_matching_files_ignores_skip_names_above_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dist" / "repo"
            root.mkdir(parents=True)
            expected = root / "README.md"
            expected.write_text("content", encoding="utf-8")

            self.assertEqual(matching_files(root, "*.md"), [expected])

    def test_test_discovery_ignores_skip_names_above_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".venv" / "repo"
            tests = root / "demo" / "tests"
            tests.mkdir(parents=True)
            (tests / "test_demo.py").write_text("", encoding="utf-8")

            self.assertEqual(find_test_directories(root), [tests])

    def test_frontmatter_parser_reads_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_text(
                "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_frontmatter(path)["name"], "demo")

    def test_frontmatter_parser_reads_folded_description(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_text(
                "---\nname: demo\ndescription: >-\n  first line\n  second line\n---\n",
                encoding="utf-8",
            )
            self.assertEqual(
                parse_frontmatter(path)["description"],
                "first line second line",
            )

    def test_skill_check_rejects_empty_folded_description(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: >-\n---\n",
                encoding="utf-8",
            )
            self.assertIn(
                "demo\\SKILL.md: description must not be empty"
                if os.name == "nt"
                else "demo/SKILL.md: description must not be empty",
                check_skill_metadata(root),
            )

    def test_skill_check_ignores_similarly_named_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "NOT_SKILL.md").write_text("no frontmatter", encoding="utf-8")
            self.assertEqual(check_skill_metadata(root), [])

    def test_local_link_parser_ignores_urls_and_anchors(self) -> None:
        self.assertIsNone(local_link_target("https://example.com/doc"))
        self.assertIsNone(local_link_target("#section"))
        self.assertEqual(local_link_target("docs/guide.md#start"), "docs/guide.md")

    def test_i18n_check_does_not_match_skill_name_as_substring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "can").mkdir()
            (root / "can" / "SKILL.md").write_text("", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "README.md").write_text("scan", encoding="utf-8")
            (root / "README.en.md").write_text("scan", encoding="utf-8")
            (root / "docs" / "getting-started.md").write_text("", encoding="utf-8")
            (root / "docs" / "getting-started.en.md").write_text("", encoding="utf-8")
            errors = check_i18n_entrypoints(root)
            self.assertIn("README.md does not list skill: can", errors)
            self.assertIn("README.en.md does not list skill: can", errors)


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

    def test_release_output_cannot_be_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                build_archives(root, root, "1.0.0")

    def test_invalid_version_does_not_delete_existing_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")
            output = root / "dist"
            output.mkdir()
            existing = output / "embeddedskills-demo-1.0.0.zip"
            existing.write_bytes(b"existing")

            with self.assertRaises(ValueError):
                build_archives(root, output, "*")

            self.assertEqual(existing.read_bytes(), b"existing")

    def test_rebuild_preserves_unrelated_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")
            output = root / "dist"
            output.mkdir()
            unrelated = output / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            previous_version = output / "embeddedskills-demo-0.9.0.zip"
            previous_version.write_bytes(b"previous")
            retired_skill = output / "embeddedskills-retired-1.0.0.zip"
            retired_skill.write_bytes(b"stale")

            build_archives(root, output, "1.0.0")
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            self.assertEqual(previous_version.read_bytes(), b"previous")
            self.assertFalse(retired_skill.exists())

    def test_custom_output_does_not_package_existing_dist_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            dist = skill / "dist"
            dist.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (dist / "previous-build.zip").write_bytes(b"previous")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")
            output = Path(directory) / "custom-output"

            archives = build_archives(root, output, "1.0.0")

            with zipfile.ZipFile(archives[0]) as archive:
                self.assertFalse(
                    any(name.startswith("demo/dist/") for name in archive.namelist())
                )

    def test_output_can_be_an_ancestor_of_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            root = output / "repo"
            skill = root / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")

            archives = build_archives(root, output, "1.0.0")

            with zipfile.ZipFile(archives[0]) as archive:
                self.assertIn("demo/SKILL.md", archive.namelist())

    def test_output_directory_is_not_discovered_as_a_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")
            output = root / "dist"
            output.mkdir()
            (output / "SKILL.md").write_text("stale", encoding="utf-8")

            archives = build_archives(root, output, "1.0.0")

            self.assertEqual(
                [path.name for path in archives],
                ["embeddedskills-1.0.0.zip", "embeddedskills-demo-1.0.0.zip"],
            )

    def test_output_inside_skill_is_not_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")
            output = skill / "release-output"
            output.mkdir()
            (output / "private.txt").write_text("not packaged", encoding="utf-8")

            archives = build_archives(root, output, "1.0.0")

            with zipfile.ZipFile(archives[0]) as archive:
                self.assertFalse(
                    any(
                        name.startswith("demo/release-output/")
                        for name in archive.namelist()
                    )
                )

    def test_output_inside_skill_does_not_validate_its_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            output = skill / "release-output"
            output.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")
            outside = Path(directory) / "private.txt"
            outside.write_text("private", encoding="utf-8")
            try:
                os.symlink(outside, output / "private-link.txt")
            except OSError:
                self.skipTest("symbolic links are unavailable")

            archives = build_archives(root, output, "1.0.0")

            with zipfile.ZipFile(archives[0]) as archive:
                self.assertFalse(
                    any(
                        name.startswith("demo/release-output/")
                        for name in archive.namelist()
                    )
                )

    def test_release_rejects_symbolic_link_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            skill = root / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT", encoding="utf-8")
            outside = Path(directory) / "private.txt"
            outside.write_text("private", encoding="utf-8")
            link = skill / "private-link.txt"
            try:
                os.symlink(outside, link)
            except OSError:
                self.skipTest("symbolic links are unavailable")

            with self.assertRaises(ValueError):
                build_archives(root, root / "dist", "1.0.0")


if __name__ == "__main__":
    unittest.main()
