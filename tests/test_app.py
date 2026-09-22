from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from apk_remote import create_app
from apk_remote.adb import AdbError


class FakeAdbClient:
    def __init__(self) -> None:
        self.connected = False
        self.pair_calls: list[tuple[str, str]] = []
        self.install_calls: list[tuple[str, list[str]]] = []

    def pair(self, serial: str, code: str) -> str:
        self.pair_calls.append((serial, code))
        return f"Successfully paired to {serial}."

    def connect(self, serial: str) -> str:
        self.connected = True
        return f"Connected to {serial}."

    def disconnect(self, serial: str) -> str:
        self.connected = False
        return f"Disconnected from {serial}."

    def is_connected(self, serial: str) -> bool:
        return self.connected

    def install_apk(self, serial: str, apk_path: Path) -> str:
        self.install_calls.append((serial, [apk_path.name]))
        return "Success"

    def install_multiple(self, serial: str, apk_paths: list[Path]) -> str:
        self.install_calls.append((serial, [path.name for path in apk_paths]))
        return "Success"


class FailingAdbClient(FakeAdbClient):
    def install_apk(self, serial: str, apk_path: Path) -> str:
        raise AdbError("INSTALL_FAILED_TEST")


class BlockingAdbClient(FakeAdbClient):
    def __init__(self) -> None:
        super().__init__()
        self.install_started = threading.Event()
        self.release_install = threading.Event()

    def install_apk(self, serial: str, apk_path: Path) -> str:
        self.install_started.set()
        if not self.release_install.wait(timeout=2):
            raise AdbError("Test installation was not released.")
        return super().install_apk(serial, apk_path)


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.adb = FakeAdbClient()
        self.app = create_app(
            {
                "TESTING": True,
                "UPLOAD_ROOT": self.temp_dir.name,
            },
            adb_client=self.adb,
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.app.extensions["install_jobs"].close()
        self.temp_dir.cleanup()

    def connect(self) -> None:
        response = self.client.post(
            "/api/connect", json={"host": "192.168.1.42", "port": 5555}
        )
        self.assertEqual(response.status_code, 200)

    def wait_for_job(self, job_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            payload = self.client.get(f"/api/jobs/{job_id}").get_json()
            if payload["state"] in {"succeeded", "failed"}:
                return payload
        self.fail("Background installation did not finish.")

    def test_connection_status_changes_after_connect_and_disconnect(self) -> None:
        self.assertFalse(self.client.get("/api/status").get_json()["connected"])

        self.connect()
        connected = self.client.get("/api/status").get_json()
        self.assertTrue(connected["connected"])
        self.assertEqual(connected["serial"], "192.168.1.42:5555")

        disconnected = self.client.post("/api/disconnect").get_json()
        self.assertFalse(disconnected["connected"])

    def test_pairing_does_not_mark_device_connected(self) -> None:
        response = self.client.post(
            "/api/pair",
            json={"host": "192.168.1.42", "port": 37125, "code": "012345"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["paired"])
        self.assertEqual(self.adb.pair_calls, [("192.168.1.42:37125", "012345")])
        self.assertFalse(self.client.get("/api/status").get_json()["connected"])

    def test_pairing_rejects_invalid_code(self) -> None:
        response = self.client.post(
            "/api/pair",
            json={"host": "192.168.1.42", "port": 37125, "code": "12345; reboot"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.adb.pair_calls, [])

    def test_rejects_invalid_address_and_install_without_connection(self) -> None:
        response = self.client.post("/api/connect", json={"host": "phone.local; reboot"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            "/api/install",
            data={"package": (io.BytesIO(b"apk"), "demo.apk")},
        )
        self.assertEqual(response.status_code, 409)

    def test_apk_install_runs_as_a_background_job(self) -> None:
        self.connect()

        response = self.client.post(
            "/api/install",
            data={"package": (io.BytesIO(b"apk"), "demo.apk")},
        )

        self.assertEqual(response.status_code, 202)
        job = self.wait_for_job(response.get_json()["id"])
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(self.adb.install_calls, [("192.168.1.42:5555", ["package.apk"])])

    def test_install_request_returns_while_adb_is_still_running(self) -> None:
        self.app.extensions["install_jobs"].close()
        blocking_adb = BlockingAdbClient()
        self.app = create_app(
            {"TESTING": True, "UPLOAD_ROOT": self.temp_dir.name},
            adb_client=blocking_adb,
        )
        self.client = self.app.test_client()
        self.connect()

        response = self.client.post(
            "/api/install",
            data={"package": (io.BytesIO(b"apk"), "demo.apk")},
        )

        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["id"]
        self.assertTrue(blocking_adb.install_started.wait(timeout=1))
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").get_json()["state"], "running")
        blocking_adb.release_install.set()
        self.assertEqual(self.wait_for_job(job_id)["state"], "succeeded")

    def test_xapk_uses_install_multiple(self) -> None:
        self.connect()
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("base.apk", b"base")
            archive.writestr("split_config.en.apk", b"split")
        bundle.seek(0)

        response = self.client.post(
            "/api/install",
            data={"package": (bundle, "demo.xapk")},
        )

        job = self.wait_for_job(response.get_json()["id"])
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(
            self.adb.install_calls,
            [("192.168.1.42:5555", ["000-base.apk", "001-split_config.en.apk"])],
        )

    def test_install_failure_is_reported_by_the_job(self) -> None:
        self.app.extensions["install_jobs"].close()
        self.app = create_app(
            {"TESTING": True, "UPLOAD_ROOT": self.temp_dir.name},
            adb_client=FailingAdbClient(),
        )
        self.client = self.app.test_client()
        self.connect()

        response = self.client.post(
            "/api/install",
            data={"package": (io.BytesIO(b"apk"), "broken.apk")},
        )

        job = self.wait_for_job(response.get_json()["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("INSTALL_FAILED_TEST", job["message"])


if __name__ == "__main__":
    unittest.main()