from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from .adb import AdbClient, AdbError, device_serial, pairing_code
from .services import ConnectionManager, InstallJobManager


MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".apk", ".xapk"}


def create_app(
    test_config: dict[str, Any] | None = None,
    *,
    adb_client: AdbClient | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        UPLOAD_ROOT=str(Path(app.instance_path) / "uploads"),
        SECRET_KEY=os.environ.get("SECRET_KEY", "development-only"),
    )
    if test_config:
        app.config.update(test_config)

    upload_root = Path(app.config["UPLOAD_ROOT"])
    upload_root.mkdir(parents=True, exist_ok=True)
    adb = adb_client or AdbClient()
    connections = ConnectionManager(adb)
    jobs = InstallJobManager(adb, upload_root)
    app.extensions["adb_connections"] = connections
    app.extensions["install_jobs"] = jobs

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/status")
    def connection_status():
        return jsonify(connections.status())

    @app.post("/api/pair")
    def pair_device():
        payload = request.get_json(silent=True) or {}
        try:
            serial = device_serial(payload.get("host", ""), payload.get("port", ""))
            code = pairing_code(payload.get("code", ""))
            message = adb.pair(serial, code)
            return jsonify(paired=True, serial=serial, message=message)
        except ValueError as exc:
            return jsonify(error=str(exc), paired=False), 400
        except AdbError as exc:
            return jsonify(error=str(exc), paired=False), 503

    @app.post("/api/connect")
    def connect_device():
        payload = request.get_json(silent=True) or {}
        try:
            serial = device_serial(payload.get("host", ""), payload.get("port", 5555))
            return jsonify(connections.connect(serial))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except AdbError as exc:
            return jsonify(error=str(exc), connected=False), 503

    @app.post("/api/disconnect")
    def disconnect_device():
        try:
            return jsonify(connections.disconnect())
        except AdbError as exc:
            return jsonify(error=str(exc)), 503

    @app.post("/api/install")
    def install_package():
        serial = connections.connected_serial()
        if serial is None:
            return jsonify(error="Connect to an Android device before installing an app."), 409

        upload = request.files.get("package")
        filename = secure_filename(upload.filename or "") if upload else ""
        extension = Path(filename).suffix.lower()
        if upload is None or not filename:
            return jsonify(error="Select an APK or XAPK file."), 400
        if extension not in SUPPORTED_EXTENSIONS:
            return jsonify(error="Only .apk and .xapk files are supported."), 400

        job_directory = upload_root / uuid4().hex
        package_path = job_directory / f"package{extension}"
        try:
            job_directory.mkdir(parents=True)
            upload.save(package_path)
            job = jobs.submit(serial, package_path, filename)
        except OSError as exc:
            shutil.rmtree(job_directory, ignore_errors=True)
            return jsonify(error=f"Unable to save the upload: {exc}"), 500
        return jsonify(job), 202

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        try:
            return jsonify(jobs.get(job_id))
        except KeyError:
            return jsonify(error="Installation job not found."), 404

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_: RequestEntityTooLarge):
        return jsonify(error="The selected file exceeds the 4 GB upload limit."), 413

    return app