import argparse
from pathlib import Path
import sys
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from ssh_probe import build_command, parse_probe_output, validate_alias  # noqa: E402


def arguments(**overrides: object) -> argparse.Namespace:
    values = {
        "alias": "test-gpu",
        "connect_timeout": 7,
        "accept_new_host_key": False,
        "known_hosts_file": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ProbeTests(unittest.TestCase):
    def test_command_disables_interactive_and_forwarding_features(self) -> None:
        command = build_command(arguments())
        rendered = " ".join(command[:-1])
        self.assertIn("BatchMode=yes", rendered)
        self.assertIn("StrictHostKeyChecking=yes", rendered)
        self.assertIn("ClearAllForwardings=yes", rendered)
        self.assertIn("ForwardAgent=no", rendered)
        self.assertIn("PermitLocalCommand=no", rendered)
        self.assertIn("ProxyCommand=none", rendered)
        self.assertEqual(command[-2], "test-gpu")

    def test_accept_new_host_key_must_be_explicit(self) -> None:
        command = build_command(arguments(accept_new_host_key=True))
        self.assertIn("StrictHostKeyChecking=accept-new", command)

    def test_alias_cannot_be_parsed_as_an_option(self) -> None:
        with self.assertRaises(ValueError):
            validate_alias("-oProxyCommand=example")

    def test_alias_rejects_whitespace(self) -> None:
        with self.assertRaises(ValueError):
            validate_alias("test gpu")

    def test_probe_output_is_structured(self) -> None:
        result = parse_probe_output(
            "os=Linux\narch=x86_64\ngpu=GPU 0: Example\nignored\n"
        )
        self.assertEqual(result, {
            "os": "Linux",
            "arch": "x86_64",
            "gpu": "GPU 0: Example",
        })


if __name__ == "__main__":
    unittest.main()
