from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from apk_remote.adb import (
    AdbClient,
    AdbError,
    CommandResult,
    device_serial,
    extract_xapk_apks,
    pairing_code,
)


class RecordingRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = iter(results)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: object) -> CommandResult:
        self.commands.append(command)
        return next(self.results)


class DeviceSerialTests(unittest.TestCase):
    def test_formats_ipv4_and_ipv6(self) -> None:
        self.assertEqual(device_serial("192.168.1.42", 5555), "192.168.1.42:5555")
        self.assertEqual(device_serial("2001:db8::42", "5556"), "[2001:db8::42]:5556")

    def test_rejects_command_input_and_bad_ports(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid IPv4 or IPv6"):
            device_serial("192.168.1.42; rm -rf /", 5555)
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            device_serial("192.168.1.42", 70000)

    def test_validates_six_digit_pairing_code(self) -> None:
        self.assertEqual(pairing_code("012345"), "012345")
        for invalid_code in ("12345", "1234567", "12a456", "123456; reboot"):
            with self.subTest(invalid_code=invalid_code):
                with self.assertRaisesRegex(ValueError, "exactly 6 digits"):
                    pairing_code(invalid_code)


class AdbClientTests(unittest.TestCase):
    def test_pair_passes_code_as_a_separate_argument(self) -> None:
        runner = RecordingRunner([CommandResult(0, "Successfully paired")])
        client = AdbClient("/opt/adb", runner)

        output = client.pair("192.168.1.42:37125", "012345")

        self.assertEqual(output, "Successfully paired")
        self.assertEqual(
            runner.commands,
            [["/opt/adb", "pair", "192.168.1.42:37125", "012345"]],
        )

    def test_connect_verifies_device_state_using_argument_lists(self) -> None:
        runner = RecordingRunner(
            [CommandResult(0, "connected to 192.168.1.42:5555"), CommandResult(0, "device\n")]
        )
        client = AdbClient("/opt/adb", runner)

        client.connect("192.168.1.42:5555")

        self.assertEqual(
            runner.commands,
            [
                ["/opt/adb", "connect", "192.168.1.42:5555"],
                ["/opt/adb", "-s", "192.168.1.42:5555", "get-state"],
            ],
        )

    def test_failed_state_check_rejects_connection(self) -> None:
        runner = RecordingRunner([CommandResult(0, "connected"), CommandResult(1, "", "offline")])
        with self.assertRaisesRegex(AdbError, "connected"):
            AdbClient("adb", runner).connect("192.168.1.42:5555")

    def test_install_multiple_passes_every_apk_as_a_separate_argument(self) -> None:
        runner = RecordingRunner([CommandResult(0, "Success")])
        client = AdbClient("adb", runner)

        client.install_multiple("192.168.1.42:5555", [Path("base.apk"), Path("split.apk")])

        self.assertEqual(
            runner.commands[0],
            [
                "adb",
                "-s",
                "192.168.1.42:5555",
                "install-multiple",
                "-r",
                "base.apk",
                "split.apk",
            ],
        )


class XapkExtractionTests(unittest.TestCase):
    def test_extracts_only_apks_with_base_first_and_safe_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "bundle.xapk"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("splits/config.en.apk", b"split")
                archive.writestr("../../base.apk", b"base")
                archive.writestr("Android/obb/data.bin", b"ignored")

            paths = extract_xapk_apks(archive_path, root / "output")

            self.assertEqual([path.name for path in paths], ["000-base.apk", "001-config.en.apk"])
            self.assertEqual([path.read_bytes() for path in paths], [b"base", b"split"])
            self.assertTrue(all(path.parent == root / "output" for path in paths))


if __name__ == "__main__":
    unittest.main()