from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .adb import AdbClient, AdbError, extract_xapk_apks


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class ConnectionManager:
    def __init__(self, adb: AdbClient) -> None:
        self._adb = adb
        self._lock = threading.RLock()
        self._serial: str | None = None
        self._message = "No Android device is connected."

    def connect(self, serial: str) -> dict[str, Any]:
        with self._lock:
            try:
                message = self._adb.connect(serial)
            except AdbError as exc:
                self._message = str(exc)
                raise
            self._serial = serial
            self._message = message
            return self._status_payload(True)

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            if self._serial is None:
                self._message = "No Android device is connected."
                return self._status_payload(False)

            serial = self._serial
            message = self._adb.disconnect(serial)
            self._serial = None
            self._message = message
            return self._status_payload(False, serial=serial)

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._serial is None:
                return self._status_payload(False)
            try:
                connected = self._adb.is_connected(self._serial)
            except AdbError as exc:
                self._message = str(exc)
                connected = False
            if not connected and self._message.lower().startswith("connected"):
                self._message = "The Android device is offline or unreachable."
            return self._status_payload(connected)

    def connected_serial(self) -> str | None:
        status = self.status()
        return status["serial"] if status["connected"] else None

    def _status_payload(self, connected: bool, serial: str | None = None) -> dict[str, Any]:
        return {
            "connected": connected,
            "serial": self._serial if serial is None else serial,
            "message": self._message,
        }


@dataclass
class InstallJob:
    id: str
    filename: str
    serial: str
    state: str
    message: str
    output: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class InstallJobManager:
    def __init__(self, adb: AdbClient, upload_root: Path) -> None:
        self._adb = adb
        self._upload_root = upload_root
        self._lock = threading.Lock()
        self._jobs: dict[str, InstallJob] = {}
        # ADB package installs against one selected device should not overlap.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="apk-install")

    def submit(self, serial: str, package_path: Path, filename: str) -> dict[str, Any]:
        job = InstallJob(
            id=uuid4().hex,
            filename=filename,
            serial=serial,
            state="queued",
            message="Waiting to install.",
            output="",
            created_at=_timestamp(),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._trim_jobs()
        self._executor.submit(self._run_install, job.id, package_path)
        return self.get(job.id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return asdict(job)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run_install(self, job_id: str, package_path: Path) -> None:
        self._update(
            job_id,
            state="running",
            message="Installing on the Android device.",
            started_at=_timestamp(),
        )
        try:
            job = self.get(job_id)
            if not self._adb.is_connected(job["serial"]):
                raise AdbError("The Android device disconnected before installation started.")

            if package_path.suffix.lower() == ".apk":
                output = self._adb.install_apk(job["serial"], package_path)
            else:
                apk_paths = extract_xapk_apks(package_path, package_path.parent / "apks")
                output = self._adb.install_multiple(job["serial"], apk_paths)

            self._update(
                job_id,
                state="succeeded",
                message=f"{job['filename']} installed successfully.",
                output=output,
                finished_at=_timestamp(),
            )
        except (AdbError, OSError) as exc:
            self._update(
                job_id,
                state="failed",
                message=f"Installation failed: {exc}",
                output=str(exc),
                finished_at=_timestamp(),
            )
        finally:
            shutil.rmtree(package_path.parent, ignore_errors=True)

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for name, value in values.items():
                setattr(job, name, value)

    def _trim_jobs(self) -> None:
        completed = [
            job for job in self._jobs.values() if job.state in {"succeeded", "failed"}
        ]
        for job in sorted(completed, key=lambda item: item.created_at)[:-99]:
            self._jobs.pop(job.id, None)