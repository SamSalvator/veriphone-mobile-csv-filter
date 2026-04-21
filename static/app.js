const state = {
  uploadId: null,
  inspection: null,
  jobId: null,
  pollTimer: null,
};

const appConfig = {
  clientUploadEnabled: document.body.dataset.clientUploadEnabled === "true",
};

let blobClientPromise = null;

const uploadForm = document.getElementById("upload-form");
const csvFileInput = document.getElementById("csv-file");
const inspectButton = document.getElementById("inspect-button");
const uploadStatus = document.getElementById("upload-status");
const configPanel = document.getElementById("config-panel");
const phoneColumnSelect = document.getElementById("phone-column");
const selectedColumnPreview = document.getElementById("selected-column-preview");
const firstDataRowLabel = document.getElementById("first-data-row-label");
const sampleTable = document.getElementById("sample-table");
const processButton = document.getElementById("process-button");
const jobPanel = document.getElementById("job-panel");
const jobStatus = document.getElementById("job-status");
const jobMetrics = document.getElementById("job-metrics");
const downloadLink = document.getElementById("download-link");

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const file = csvFileInput.files[0];
  if (!file) {
    setUploadStatus("Choose a CSV file before uploading.", true);
    return;
  }

  inspectButton.disabled = true;
  configPanel.classList.add("hidden");
  clearDownloadLink();

  try {
    const payload = appConfig.clientUploadEnabled
      ? await inspectViaBlobUpload(file)
      : await inspectViaMultipartUpload(file);

    state.uploadId = payload.upload_id;
    state.inspection = payload;

    renderInspection(payload);
    setUploadStatus(`Ready: ${payload.filename} inspected successfully.`);
  } catch (error) {
    setUploadStatus(error.message, true);
  } finally {
    inspectButton.disabled = false;
  }
});

phoneColumnSelect.addEventListener("change", () => {
  updateSelectedColumnPreview();
});

processButton.addEventListener("click", async () => {
  if (!state.uploadId || !state.inspection) {
    setJobStatus("Inspect a CSV first.", true);
    return;
  }

  processButton.disabled = true;
  setJobStatus("Starting Veriphone verification...");
  clearDownloadLink();

  try {
    const payload = await fetchJson("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        upload_id: state.uploadId,
        phone_column_index: Number(phoneColumnSelect.value),
        first_data_row: Number(state.inspection.first_data_row),
      }),
    });

    state.jobId = payload.job_id;
    jobPanel.classList.remove("hidden");
    renderJob(payload);
    schedulePoll();
  } catch (error) {
    setJobStatus(error.message, true);
    processButton.disabled = false;
  }
});

async function inspectViaMultipartUpload(file) {
  setUploadStatus(`Inspecting ${file.name}...`);

  const formData = new FormData();
  formData.append("file", file);

  return fetchJson("/api/uploads/inspect", {
    method: "POST",
    body: formData,
  });
}

async function inspectViaBlobUpload(file) {
  setUploadStatus(`Uploading ${file.name} to secure storage...`);

  const blob = await uploadToBlob(file);

  setUploadStatus(`Inspecting ${file.name}...`);
  return fetchJson("/api/uploads/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: file.name,
      blob_pathname: blob.pathname,
      blob_url: blob.url,
    }),
  });
}

async function uploadToBlob(file) {
  const { upload } = await loadBlobClient();
  const pathname = `veriphone/uploads/${crypto.randomUUID()}/${sanitizeFilename(file.name)}`;

  return upload(pathname, file, {
    access: "private",
    handleUploadUrl: "/api/blob-upload",
    contentType: file.type || "text/csv",
    multipart: file.size > 4_000_000,
    onUploadProgress(progress) {
      const percentage = Math.round(progress.percentage || 0);
      setUploadStatus(`Uploading ${file.name}... ${percentage}%`);
    },
  });
}

async function loadBlobClient() {
  if (!blobClientPromise) {
    blobClientPromise = import("https://esm.sh/@vercel/blob/client@2.3.3");
  }
  return blobClientPromise;
}

function renderInspection(payload) {
  configPanel.classList.remove("hidden");
  firstDataRowLabel.textContent = String(payload.first_data_row);

  phoneColumnSelect.innerHTML = "";
  payload.columns.forEach((column) => {
    const option = document.createElement("option");
    option.value = String(column.index);
    option.textContent = column.label;
    if (column.is_suggested) {
      option.textContent = `${column.label} (suggested)`;
      option.selected = true;
    }
    phoneColumnSelect.appendChild(option);
  });

  if (payload.suggested_phone_column_index === null && payload.columns.length > 0) {
    phoneColumnSelect.value = String(payload.columns[0].index);
  }

  renderSampleTable(payload.columns, payload.sample_rows);
  updateSelectedColumnPreview();
}

function renderSampleTable(columns, sampleRows) {
  const headerCells = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const bodyRows = sampleRows
    .map(
      (row) =>
        `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`,
    )
    .join("");

  sampleTable.innerHTML = `
    <thead><tr>${headerCells}</tr></thead>
    <tbody>${bodyRows || `<tr><td colspan="${columns.length}">No preview rows available.</td></tr>`}</tbody>
  `;
}

function updateSelectedColumnPreview() {
  if (!state.inspection) {
    return;
  }

  const index = Number(phoneColumnSelect.value);
  const column = state.inspection.columns.find((candidate) => candidate.index === index);
  const previewValues = column?.sample_values || [];

  selectedColumnPreview.innerHTML = "";
  if (previewValues.length === 0) {
    selectedColumnPreview.innerHTML = "<li>No sample values available for this column.</li>";
    return;
  }

  previewValues.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    selectedColumnPreview.appendChild(item);
  });
}

function renderJob(payload) {
  const summary = payload.summary || {};
  setJobStatus(buildStatusText(payload), payload.job_status === "error");

  const progress = payload.progress || {};
  const metricItems = [
    ["Remote status", payload.remote_status || "unknown"],
    ["Rows processed", progress.position ?? 0],
    ["Rows total", progress.lastrow ?? "unknown"],
    ["Valid", progress.valid ?? 0],
    ["Invalid", progress.invalid ?? 0],
    ["Syntax errors", progress.syntaxerr ?? 0],
    ["Rows kept", summary.mobile_rows ?? "pending"],
    ["Rows removed", summary.excluded_rows ?? "pending"],
  ];

  jobMetrics.innerHTML = metricItems
    .map(
      ([label, value]) => `<div><dt>${escapeHtml(String(label))}</dt><dd>${escapeHtml(String(value))}</dd></div>`,
    )
    .join("");

  if (payload.download_ready && payload.download_url) {
    downloadLink.href = payload.download_url;
    downloadLink.classList.remove("disabled");
    downloadLink.setAttribute("aria-disabled", "false");
    processButton.disabled = false;
  } else {
    clearDownloadLink();
  }

  if (payload.job_status === "completed" || payload.job_status === "error") {
    processButton.disabled = false;
  }
}

function buildStatusText(payload) {
  if (payload.job_status === "error") {
    return payload.error || "The job failed.";
  }
  if (payload.job_status === "completed") {
    const mobileRows = payload.summary?.mobile_rows ?? 0;
    return `Completed. ${mobileRows} mobile rows are ready to download.`;
  }
  return `Veriphone status: ${payload.remote_status || "processing"}...`;
}

function schedulePoll() {
  if (!state.jobId) {
    return;
  }

  window.clearTimeout(state.pollTimer);
  state.pollTimer = window.setTimeout(async () => {
    try {
      const payload = await fetchJson(`/api/jobs/${state.jobId}`);
      renderJob(payload);
      if (!["completed", "error"].includes(payload.job_status)) {
        schedulePoll();
      }
    } catch (error) {
      setJobStatus(error.message, true);
      processButton.disabled = false;
    }
  }, 3000);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = {};

  try {
    payload = await response.json();
  } catch (error) {
    throw new Error("The server returned an unreadable response.");
  }

  if (!response.ok || payload.status === "error") {
    throw new Error(payload.message || payload.error || "Request failed.");
  }
  return payload;
}

function setUploadStatus(message, isError = false) {
  uploadStatus.textContent = message;
  uploadStatus.style.color = isError ? "#b91c1c" : "";
}

function setJobStatus(message, isError = false) {
  jobPanel.classList.remove("hidden");
  jobStatus.textContent = message;
  jobStatus.style.color = isError ? "#b91c1c" : "";
}

function clearDownloadLink() {
  downloadLink.href = "#";
  downloadLink.classList.add("disabled");
  downloadLink.setAttribute("aria-disabled", "true");
}

function sanitizeFilename(value) {
  return String(value || "upload.csv")
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-/, "")
    .replace(/-$/, "") || "upload.csv";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
