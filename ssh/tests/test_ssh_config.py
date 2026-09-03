import unittest

from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from ssh_config import (  # noqa: E402
    parse_hosts,
    resolve_hosts,
    search_hosts,
    validate_single_line_field,
)


CONFIG = """\
# description: shared validation host
# groups: test environment, inference validation
# aliases: 4090D, 4090 test host
# tags: gpu, linux, k8s
Host test-4090d test-gpu
    HostName 192.0.2.10
    User ubuntu

# description: CPU integration host
# groups: test environment
# aliases: CPU test host
# tags: linux, cpu
Host test-cpu
    HostName 192.0.2.20
    User ubuntu
"""


class EnvironmentResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hosts = parse_hosts(CONFIG.splitlines())

    def test_parser_preserves_all_openssh_host_patterns(self) -> None:
        self.assertEqual(self.hosts[0]["alias"], "test-4090d")
        self.assertEqual(
            self.hosts[0]["host_patterns"],
            ["test-4090d", "test-gpu"],
        )

    def test_group_search_returns_all_environment_members(self) -> None:
        matches = search_hosts(self.hosts, ["test environment"])
        self.assertEqual(
            [host["alias"] for host in matches],
            ["test-4090d", "test-cpu"],
        )

    def test_multi_term_search_intersects_group_and_capability(self) -> None:
        matches = search_hosts(self.hosts, ["test environment", "4090D"])
        self.assertEqual([host["alias"] for host in matches], ["test-4090d"])

    def test_resolve_prioritizes_exact_openssh_alias(self) -> None:
        matches = resolve_hosts(self.hosts, ["test-gpu"])
        self.assertEqual([host["alias"] for host in matches], ["test-4090d"])

    def test_shared_group_is_reported_as_ambiguous(self) -> None:
        matches = resolve_hosts(self.hosts, ["test environment"])
        self.assertEqual(len(matches), 2)

    def test_openssh_pattern_ranks_before_metadata_alias(self) -> None:
        hosts = parse_hosts((CONFIG + """
# aliases: test-gpu
Host a-metadata-alias
    HostName 192.0.2.30
""").splitlines())
        matches = search_hosts(hosts, ["test-gpu"])
        self.assertEqual(matches[0]["alias"], "test-4090d")

    def test_config_metadata_must_remain_on_one_line(self) -> None:
        with self.assertRaises(ValueError):
            validate_single_line_field("groups", "test\nHost injected")


if __name__ == "__main__":
    unittest.main()
