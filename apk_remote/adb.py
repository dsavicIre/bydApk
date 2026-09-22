from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ADB_PORT = 5555
MAX_XAPK_FILES = 200
MAX_XAPK_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
PAIRING_CODE_PATTERN = re.compile(r"^[0-9]{6}$")


class AdbError(RuntimeError):
    """Raised when an ADB operation cannot be completed."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return "\n".join(part.strip() for part in (self.stdout, self.stderr) if part.strip())


def device_serial(host: str, port: int | str = DEFAULT_ADB_PORT) -> str:
    """Validate an IP address and return the ADB network serial."""
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError as exc:
        raise ValueError("Enter a valid IPv4 or IPv6 address.") from exc

    try:
        port_number = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Port must be a number between 1 and 65535.") from exc

    if not 1 <= port_number <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Enter a unicast device IP address.")

    if address.version == 6:
        return f"[{address.compressed}]:{port_number}"
    return f"{address.compressed}:{port_number}"


def pairing_code(value: object) -> str:
    code = str(value).strip()
    if not PAIRING_CODE_PATTERN.fullmatch(code):
        raise ValueError("Pairing code must contain exactly 6 digits.")
    return code


class AdbClient:
    def __init__(
        self,
        adb_path: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str] | CommandResult] = subprocess.run,
    ) -> None:
        configured_path = adb_path or os.environ.get("ADB_PATH") or shutil.which("adb")
        self.adb_path = configured_path or "adb"
        self._available = configured_path is not None
        self._runner = runner

    def _run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str] | CommandResult:
        if not self._available:
            raise AdbError("ADB was not found. Install Android Platform Tools or set ADB_PATH.")
        try:
            result = self._runner(
                [self.adb_path, *arguments],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"ADB timed out after {timeout} seconds.") from exc
        except OSError as exc:
            raise AdbError(f"Unable to start ADB: {exc}") from exc

        if check and result.returncode != 0:
            output = CommandResult(result.returncode, result.stdout, result.stderr).output
            raise AdbError(output or f"ADB exited with status {result.returncode}.")
        return result

    def connect(self, serial: str) -> str:
        result = self._run(["connect", serial], timeout=20)
        output = CommandResult(result.returncode, result.stdout, result.stderr).output
        if not self.is_connected(serial):
            raise AdbError(output or "ADB could not establish a device connection.")
        return output or f"Connected to {serial}."

    def pair(self, serial: str, code: str) -> str:
        result = self._run(["pair", serial, code], timeout=60)
        return CommandResult(result.returncode, result.stdout, result.stderr).output or (
            f"Successfully paired to {serial}."
        )

    def disconnect(self, serial: str) -> str:
        result = self._run(["disconnect", serial], timeout=10, check=False)
        output = CommandResult(result.returncode, result.stdout, result.stderr).output
        if result.returncode != 0:
            raise AdbError(output or "ADB could not disconnect the device.")
        return output or f"Disconnected from {serial}."

    def is_connected(self, serial: str) -> bool:
        result = self._run(["-s", serial, "get-state"], timeout=10, check=False)
        return result.returncode == 0 and result.stdout.strip() == "device"

    def install_apk(self, serial: str, apk_path: Path) -> str:
        result = self._run(
            ["-s", serial, "install", "-r", str(apk_path)],
            timeout=15 * 60,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr).output or "Success"

    def install_multiple(self, serial: str, apk_paths: Sequence[Path]) -> str:
        if not apk_paths:
            raise AdbError("The XAPK does not contain any APK files.")
        result = self._run(
            ["-s", serial, "install-multiple", "-r", *(str(path) for path in apk_paths)],
            timeout=20 * 60,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr).output or "Success"


def extract_xapk_apks(xapk_path: Path, destination: Path) -> list[Path]:
    """Extract APK payloads without trusting archive paths or expanding unrelated files."""
    try:
        archive = zipfile.ZipFile(xapk_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AdbError("The selected XAPK is not a valid ZIP archive.") from exc

    with archive:
        apk_entries = [
            entry
            for entry in archive.infolist()
            if not entry.is_dir() and Path(entry.filename).suffix.lower() == ".apk"
        ]
        if not apk_entries:
            raise AdbError("The XAPK does not contain any APK files.")
        if len(apk_entries) > MAX_XAPK_FILES:
            raise AdbError(f"The XAPK contains more than {MAX_XAPK_FILES} APK files.")
        if any(entry.flag_bits & 0x1 for entry in apk_entries):
            raise AdbError("Password-protected XAPK files are not supported.")
        if sum(entry.file_size for entry in apk_entries) > MAX_XAPK_UNCOMPRESSED_BYTES:
            raise AdbError("The APK payloads in the XAPK are too large.")

        destination.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        for index, entry in enumerate(sorted(apk_entries, key=_apk_sort_key)):
            safe_name = Path(entry.filename).name
            target = destination / f"{index:03d}-{safe_name}"
            with archive.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(target)
        return extracted


def _apk_sort_key(entry: zipfile.ZipInfo) -> tuple[int, str]:
    filename = Path(entry.filename).name.lower()
    is_base = filename == "base.apk" or filename.startswith("base-")
    is_split = filename.startswith(("split_", "config."))
    return (0 if is_base else 2 if is_split else 1, filename)