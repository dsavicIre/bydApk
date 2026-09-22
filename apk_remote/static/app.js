const elements = {
  pairingForm: document.querySelector("#pairing-form"),
  pairHost: document.querySelector("#pair-host"),
  pairPort: document.querySelector("#pair-port"),
  pairingCode: document.querySelector("#pairing-code"),
  pairButton: document.querySelector("#pair-button"),
  pairingStatus: document.querySelector("#pairing-status"),
  connectionForm: document.querySelector("#connection-form"),
  host: document.querySelector("#host"),
  port: document.querySelector("#port"),
  connectButton: document.querySelector("#connect-button"),
  disconnectButton: document.querySelector("#disconnect-button"),
  headerStatus: document.querySelector("#header-status"),
  headerStatusLabel: document.querySelector("#header-status-label"),
  connectionDetail: document.querySelector("#connection-detail"),
  packageInput: document.querySelector("#package-input"),
  dropZone: document.querySelector("#drop-zone"),
  fileGlyph: document.querySelector("#file-glyph"),
  fileName: document.querySelector("#file-name"),
  fileMeta: document.querySelector("#file-meta"),
  readiness: document.querySelector("#readiness"),
  readinessText: document.querySelector("#readiness-text"),
  installButton: document.querySelector("#install-button"),
  jobState: document.querySelector("#job-state"),
  jobBadge: document.querySelector("#job-badge"),
  progressBar: document.querySelector("#progress-bar"),
  jobOutput: document.querySelector("#job-output"),
  toast: document.querySelector("#toast"),
};

const state = {
  connected: false,
  selectedFile: null,
  installing: false,
  toastTimer: null,
};

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** unitIndex)).toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with status ${response.status}.`);
  }
  return payload;
}

function showToast(message, success = false) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-success", success);
  elements.toast.classList.add("is-visible");
  state.toastTimer = window.setTimeout(() => {
    elements.toast.classList.remove("is-visible");
  }, 4500);
}

function setConnectionStatus(payload) {
  state.connected = Boolean(payload.connected);
  elements.headerStatus.dataset.state = state.connected ? "connected" : "disconnected";
  elements.headerStatusLabel.textContent = state.connected ? "Connected" : "Disconnected";
  elements.connectionDetail.textContent = state.connected
    ? payload.serial
    : payload.message || "No Android device is connected.";
  elements.disconnectButton.disabled = !state.connected || state.installing;
  updateReadiness();
}

function setConnectionBusy(busy) {
  elements.connectButton.disabled = busy;
  elements.pairButton.disabled = busy;
  elements.host.disabled = busy;
  elements.port.disabled = busy;
  elements.headerStatus.dataset.state = busy ? "checking" : state.connected ? "connected" : "disconnected";
  elements.headerStatusLabel.textContent = busy ? "Connecting" : state.connected ? "Connected" : "Disconnected";
}

function setPairingBusy(busy) {
  elements.pairButton.disabled = busy;
  elements.pairHost.disabled = busy;
  elements.pairPort.disabled = busy;
  elements.pairingCode.disabled = busy;
  elements.connectButton.disabled = busy;
  if (busy) {
    elements.pairingStatus.dataset.state = "working";
    elements.pairingStatus.textContent = "Pairing";
  }
}

function updateReadiness() {
  const ready = state.connected && state.selectedFile && !state.installing;
  elements.readiness.classList.toggle("is-ready", Boolean(ready));
  elements.installButton.disabled = !ready;
  if (state.installing) {
    elements.readinessText.textContent = "Installation in progress";
  } else if (!state.connected) {
    elements.readinessText.textContent = "Connect a device to continue";
  } else if (!state.selectedFile) {
    elements.readinessText.textContent = "Select a package to continue";
  } else {
    elements.readinessText.textContent = "Ready to install";
  }
}

function selectFile(file) {
  if (!file) return;
  const extension = file.name.split(".").pop().toLowerCase();
  if (!["apk", "xapk"].includes(extension)) {
    elements.packageInput.value = "";
    showToast("Only APK and XAPK files are supported.");
    return;
  }
  state.selectedFile = file;
  elements.fileGlyph.textContent = extension.toUpperCase();
  elements.fileName.textContent = file.name;
  elements.fileMeta.textContent = formatBytes(file.size);
  elements.dropZone.classList.add("has-file");
  updateReadiness();
}

function setJobPresentation(jobState, message, output = "") {
  elements.jobBadge.dataset.state = jobState;
  elements.jobBadge.textContent = jobState === "succeeded" ? "Success" : jobState;
  elements.jobState.textContent = message;
  elements.jobOutput.textContent = output || message;
  elements.progressBar.className = "";
  elements.progressBar.style.width = "";

  if (["uploading", "queued", "running"].includes(jobState)) {
    elements.progressBar.classList.add("is-indeterminate");
  } else if (jobState === "succeeded") {
    elements.progressBar.style.width = "100%";
  } else if (jobState === "failed") {
    elements.progressBar.style.width = "100%";
    elements.progressBar.classList.add("is-failed");
  } else {
    elements.progressBar.style.width = "0";
  }
}

async function refreshConnectionStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    setConnectionStatus(await readJson(response));
  } catch (error) {
    setConnectionStatus({ connected: false, message: "Unable to reach the APK Relay server." });
  }
}

elements.pairingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setPairingBusy(true);
  try {
    const response = await fetch("/api/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: elements.pairHost.value,
        port: elements.pairPort.value,
        code: elements.pairingCode.value,
      }),
    });
    const payload = await readJson(response);
    elements.pairingStatus.dataset.state = "paired";
    elements.pairingStatus.textContent = "Paired";
    if (!elements.host.value) elements.host.value = elements.pairHost.value;
    showToast(payload.message, true);
  } catch (error) {
    elements.pairingStatus.dataset.state = "failed";
    elements.pairingStatus.textContent = "Failed";
    showToast(error.message);
  } finally {
    elements.pairingCode.value = "";
    setPairingBusy(false);
  }
});

elements.connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setConnectionBusy(true);
  elements.connectionDetail.textContent = `Connecting to ${elements.host.value}:${elements.port.value}...`;
  try {
    const response = await fetch("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: elements.host.value, port: elements.port.value }),
    });
    const payload = await readJson(response);
    setConnectionStatus(payload);
    showToast(`Connected to ${payload.serial}.`, true);
  } catch (error) {
    setConnectionStatus({ connected: false, message: error.message });
    showToast(error.message);
  } finally {
    setConnectionBusy(false);
  }
});

elements.disconnectButton.addEventListener("click", async () => {
  elements.disconnectButton.disabled = true;
  try {
    const response = await fetch("/api/disconnect", { method: "POST" });
    const payload = await readJson(response);
    setConnectionStatus(payload);
    showToast("Device disconnected.", true);
  } catch (error) {
    showToast(error.message);
    await refreshConnectionStatus();
  }
});

elements.packageInput.addEventListener("change", () => {
  selectFile(elements.packageInput.files[0]);
});

for (const eventName of ["dragenter", "dragover"]) {
  elements.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropZone.classList.add("is-dragging");
  });
}

for (const eventName of ["dragleave", "drop"]) {
  elements.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropZone.classList.remove("is-dragging");
  });
}

elements.dropZone.addEventListener("drop", (event) => {
  const file = event.dataTransfer.files[0];
  if (file) selectFile(file);
});

function uploadPackage(file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("package", file);
    request.open("POST", "/api/install");
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      elements.progressBar.classList.remove("is-indeterminate");
      elements.progressBar.style.width = `${Math.max(2, (event.loaded / event.total) * 100)}%`;
      elements.jobState.textContent = `Uploading ${Math.round((event.loaded / event.total) * 100)}%`;
    });
    request.addEventListener("load", () => {
      let payload = {};
      try {
        payload = JSON.parse(request.responseText);
      } catch (_) {
        reject(new Error("The server returned an invalid response."));
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        reject(new Error(payload.error || `Upload failed with status ${request.status}.`));
        return;
      }
      resolve(payload);
    });
    request.addEventListener("error", () => reject(new Error("The upload could not reach the server.")));
    request.send(formData);
  });
}

async function pollJob(jobId) {
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
    const job = await readJson(response);
    setJobPresentation(job.state, job.message, job.output);
    if (["succeeded", "failed"].includes(job.state)) return job;
    await new Promise((resolve) => window.setTimeout(resolve, 850));
  }
}

elements.installButton.addEventListener("click", async () => {
  if (!state.selectedFile || !state.connected || state.installing) return;
  state.installing = true;
  updateReadiness();
  elements.disconnectButton.disabled = true;
  elements.progressBar.style.width = "0";
  setJobPresentation("uploading", "Uploading package to APK Relay.");

  try {
    const job = await uploadPackage(state.selectedFile);
    setJobPresentation(job.state, job.message, job.output);
    const result = await pollJob(job.id);
    if (result.state === "succeeded") {
      showToast(result.message, true);
    } else {
      showToast(result.message);
    }
  } catch (error) {
    setJobPresentation("failed", "Installation failed", error.message);
    showToast(error.message);
  } finally {
    state.installing = false;
    elements.disconnectButton.disabled = !state.connected;
    updateReadiness();
    await refreshConnectionStatus();
  }
});

refreshConnectionStatus();
window.setInterval(refreshConnectionStatus, 4000);