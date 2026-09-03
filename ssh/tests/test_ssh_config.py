import argparse
import contextlib
import io
import unittest

from pathlib import Path
import sys
import tempfile
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from ssh_config import (  # noqa: E402
    cmd_add,
    concrete_host_patterns,
    format_config_value,
    parse_hosts,
    resolve_hosts,
    search_hosts,
    validate_add_arguments,
    validate_host_alias,
    validate_port,
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


def add_arguments(**overrides: object) -> argparse.Namespace:
    values = {
        "alias": "test-gpu",
        "host": "192.0.2.10",
        "user": "ubuntu",
        "port": 22,
        "key": None,
        "proxy_jump": None,
        "description": None,
        "aliases": None,
        "groups": None,
        "tags": None,
        "location": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


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

    def test_exact_metadata_alias_ranks_before_fuzzy_text(self) -> None:
        hosts = parse_hosts((CONFIG + """
# description: fallback host mentioning 4090D
Host fallback-gpu
    HostName 192.0.2.30
""").splitlines())
        matches = resolve_hosts(hosts, ["4090D"])
        self.assertEqual([host["alias"] for host in matches], ["test-4090d"])

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

    def test_add_rejects_newline_in_port(self) -> None:
        with self.assertRaises(ValueError):
            validate_add_arguments(add_arguments(port="22\nHost injected"))

    def test_wildcard_and_match_blocks_do_not_pollute_previous_host(self) -> None:
        hosts = parse_hosts("""\
# description: concrete host
Host gpu-a
    HostName 192.0.2.10

Host *
    User default-user

Match host gpu-a
    ProxyCommand unsafe-helper
""".splitlines())
        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0]["options"], {"hostname": "192.0.2.10"})

    def test_literal_pattern_after_wildcard_is_preserved(self) -> None:
        hosts = parse_hosts("""\
Host *.example.test gpu-a !blocked
    HostName 192.0.2.10
""".splitlines())
        self.assertEqual(hosts[0]["alias"], "gpu-a")
        self.assertEqual(concrete_host_patterns(hosts[0]), ["gpu-a"])
        self.assertEqual(resolve_hosts(hosts, ["gpu-a"]), hosts)
        self.assertEqual(resolve_hosts(hosts, ["*.example.test"]), [])

    def test_blank_lines_do_not_end_an_openssh_host_block(self) -> None:
        hosts = parse_hosts("""\
Host gpu-a
    HostName 192.0.2.10

    User ubuntu
""".splitlines())
        self.assertEqual(
            hosts[0]["options"],
            {"hostname": "192.0.2.10", "user": "ubuntu"},
        )

    def test_host_options_do_not_require_indentation(self) -> None:
        hosts = parse_hosts("""\
Host gpu-a
HostName 192.0.2.10
User ubuntu
""".splitlines())
        self.assertEqual(hosts[0]["options"]["user"], "ubuntu")

    def test_alias_validation_rejects_patterns_and_options(self) -> None:
        for alias in (
            "-oProxyCommand=helper",
            "*.example.test",
            "!blocked",
            "a b",
            "#comment",
        ):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                validate_host_alias(alias)

    def test_add_rejects_comment_delimiters_in_unquoted_tokens(self) -> None:
        for name in ("host", "user", "proxy_jump"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_add_arguments(add_arguments(**{name: "#invalid"}))

    def test_port_validation_rejects_invalid_values(self) -> None:
        for port in ("not-a-port", 0, 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                validate_port(port)

    def test_port_validation_accepts_full_valid_range(self) -> None:
        self.assertEqual(validate_port("1"), 1)
        self.assertEqual(validate_port("65535"), 65535)

    def test_identity_file_with_spaces_is_quoted_for_openssh(self) -> None:
        self.assertEqual(
            format_config_value(r"C:\Users\Test User\.ssh\id_ed25519"),
            r'"C:\\Users\\Test User\\.ssh\\id_ed25519"',
        )
        self.assertEqual(format_config_value("#private-key"), '"#private-key"')

    def test_add_writes_a_parseable_single_host_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config"
            args = add_arguments(
                key=r"C:\Users\Test User\.ssh\id_ed25519",
                groups="test environment",
                tags="gpu,linux",
            )
            with (
                mock.patch("ssh_config.ssh_config_path", return_value=config_path),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cmd_add(args), 0)

            hosts = parse_hosts(config_path.read_text(encoding="utf-8").splitlines())
            self.assertEqual(len(hosts), 1)
            self.assertEqual(hosts[0]["alias"], "test-gpu")
            self.assertEqual(hosts[0]["options"]["port"], "22")
            self.assertEqual(hosts[0]["metadata"]["groups"], "test environment")
            self.assertIn('IdentityFile "C:\\\\Users', config_path.read_text())

    def test_add_rejects_case_insensitive_duplicate_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config"
            original = "Host Test-GPU\n    HostName 192.0.2.10\n"
            config_path.write_text(original, encoding="utf-8")
            with (
                mock.patch("ssh_config.ssh_config_path", return_value=config_path),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(cmd_add(add_arguments(alias="test-gpu")), 1)
            self.assertEqual(config_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
