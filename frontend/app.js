/**
 * Authenticon Frontend
 * Simple, reliable flow: Upload → Select Type → Analyze → Results
 */

const API = window.location.origin.includes("8000") ? "" : ""; // same origin when served by FastAPI
const DOCUMENT_TYPES = [
  "Driver’s License",
  "State ID",
  "Passport",
  "Social Security Card",
  "Utility Bill",
  "Bank Statement",
  "Bank Letterhead",
  "Mortgage Deed",
  "Court Document",
  "Paystub",
  "Payroll Check",
];

let state = {
  sessionId: null,
  documents: [], // {id, filename, size, file?, type?}
  results: null,
  cross: null,
};

// DOM refs
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dropZone = $("#dropZone");
const fileInput = $("#fileInput");
const cameraInput = $("#cameraInput");
const fileList = $("#fileList");
const btnSelectFiles = $("#btnSelectFiles");
const btnCamera = $("#btnCamera");
const btnToTypes = $("#btnToTypes");
const btnBackUpload = $("#btnBackUpload");
const btnAnalyze = $("#btnAnalyze");
const typeAssignments = $("#typeAssignments");
const resultsContainer = $("#resultsContainer");
const crossDoc = $("#crossDoc");
const sessionBadge = $("#sessionBadge");

function showStep(id) {
  $$(".step").forEach((s) => s.classList.remove("active"));
  $(`#${id}`).classList.add("active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
}

function renderFileList() {
  if (state.documents.length === 0) {
    fileList.hidden = true;
    btnToTypes.disabled = true;
    return;
  }
  fileList.hidden = false;
  fileList.innerHTML = state.documents
    .map(
      (d, i) => `
    <div class="file-item" data-idx="${i}">
      <div>
        <div class="name">${escapeHtml(d.filename)}</div>
        <div class="size">${formatSize(d.size)}</div>
      </div>
      <button type="button" class="remove" data-idx="${i}" title="Remove">×</button>
    </div>`
    )
    .join("");
  btnToTypes.disabled = false;
  fileList.querySelectorAll(".remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = +btn.dataset.idx;
      state.documents.splice(idx, 1);
      renderFileList();
    });
  });
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function uploadFiles(fileListOrArr) {
  const files = Array.from(fileListOrArr);
  if (files.length === 0) return;
  if (state.documents.length + files.length > 5) {
    alert("Maximum 5 documents allowed.");
    return;
  }

  const form = new FormData();
  if (state.sessionId) form.append("session_id", state.sessionId);
  files.forEach((f) => form.append("files", f));

  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    state.sessionId = data.session_id;
    sessionBadge.hidden = false;
    sessionBadge.textContent = `Session ${state.sessionId.slice(0, 8)}…`;

    data.uploaded.forEach((u) => {
      const original = files.find((f) => f.name === u.filename) || files[0];
      state.documents.push({
        id: u.id,
        filename: u.filename,
        size: u.size,
        file: original,
        type: null,
      });
    });
    renderFileList();
  } catch (e) {
    alert("Upload failed: " + e.message);
  }
}

// Event listeners – Upload
btnSelectFiles.addEventListener("click", () => fileInput.click());
btnCamera.addEventListener("click", () => cameraInput.click());
fileInput.addEventListener("change", () => {
  uploadFiles(fileInput.files);
  fileInput.value = "";
});
cameraInput.addEventListener("change", () => {
  uploadFiles(cameraInput.files);
  cameraInput.value = "";
});

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  uploadFiles(e.dataTransfer.files);
});

btnToTypes.addEventListener("click", () => {
  if (state.documents.length === 0) return;
  renderTypeAssignments();
  showStep("step-types");
});

btnBackUpload.addEventListener("click", () => showStep("step-upload"));

function renderTypeAssignments() {
  typeAssignments.innerHTML = state.documents
    .map(
      (d, i) => `
    <div class="type-row">
      <label>${escapeHtml(d.filename)}</label>
      <select data-idx="${i}" class="type-select">
        <option value="">— Select type —</option>
        ${DOCUMENT_TYPES.map((t) => `<option value="${escapeHtml(t)}" ${d.type === t ? "selected" : ""}>${escapeHtml(t)}</option>`).join("")}
      </select>
    </div>`
    )
    .join("");

  typeAssignments.querySelectorAll(".type-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const idx = +sel.dataset.idx;
      state.documents[idx].type = sel.value || null;
      updateAnalyzeButton();
    });
  });
  updateAnalyzeButton();
}

function updateAnalyzeButton() {
  const allSet = state.documents.every((d) => d.type);
  btnAnalyze.disabled = !allSet || state.documents.length === 0;
}

btnAnalyze.addEventListener("click", async () => {
  if (!state.sessionId) return;
  // Set types on server
  const ids = state.documents.map((d) => d.id).join(",");
  const types = state.documents.map((d) => d.type).join(",");
  const formTypes = new FormData();
  formTypes.append("session_id", state.sessionId);
  formTypes.append("document_ids", ids);
  formTypes.append("types", types);

  showStep("step-analyzing");
  $("#analyzeStatus").textContent = "Assigning document types…";

  try {
    let res = await fetch("/api/set-types", { method: "POST", body: formTypes });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Type assignment failed");

    $("#analyzeStatus").textContent = "Running multi-stage evidence pipeline on each document…";
    const formAnalyze = new FormData();
    formAnalyze.append("session_id", state.sessionId);
    res = await fetch("/api/analyze", { method: "POST", body: formAnalyze });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Analysis failed");

    const data = await res.json();
    state.results = data.results;
    state.cross = data.cross_document;
    renderResults();
    showStep("step-results");
  } catch (e) {
    alert("Analysis error: " + e.message);
    showStep("step-types");
  }
});

function scoreClass(score) {
  if (score >= 70) return "score-high";
  if (score >= 45) return "score-mid";
  if (score > 0) return "score-low";
  return "score-unknown";
}

function classClass(c) {
  if (!c) return "class-unable";
  const u = c.toUpperCase();
  if (u.includes("GENUINE") || u.includes("POSSIBLY REAL")) return "class-possibly";
  if (u.includes("FAKE") || u.includes("FRAUD")) return "class-fake";
  return "class-unable";
}

function renderResults() {
  if (state.cross && state.cross.status !== "Not applicable (fewer than 2 documents)") {
    crossDoc.hidden = false;
    crossDoc.innerHTML = `
      <strong>Cross-Document Consistency</strong>
      <p>${escapeHtml(state.cross.notes || state.cross.status)}</p>
      ${state.cross.observations ? `<ul>${state.cross.observations.map((o) => `<li>${escapeHtml(o)}</li>`).join("")}</ul>` : ""}
      ${state.cross.shared_date_strings?.length ? `<p>Shared date strings: ${state.cross.shared_date_strings.map(escapeHtml).join(", ")}</p>` : ""}
    `;
  } else {
    crossDoc.hidden = true;
  }

  resultsContainer.innerHTML = state.results
    .map((r) => {
      const rep = r.report;
      const score = rep.score ?? 0;
      const classification = rep.classification || "UNABLE TO DETERMINE";
      const conf = rep.confidence != null ? (rep.confidence * 100).toFixed(0) + "%" : "—";
      const confLabel = rep.confidence_label || "";

      return `
      <article class="result-card" data-doc="${r.document_id}">
        <div class="result-header">
          <div class="filename">${escapeHtml(r.filename)} <span style="color:var(--muted);font-weight:400">(${escapeHtml(r.type)})</span></div>
          <span class="score-badge ${scoreClass(score)}">${score}/100</span>
          <span class="class-label ${classClass(classification)}">${escapeHtml(classification)}</span>
          <span style="font-size:0.8rem;color:var(--muted)">Confidence: ${conf} (${confLabel})</span>
        </div>
        <div class="result-body">
          <div class="conclusion">${escapeHtml(rep.executive_conclusion || "")}</div>

          <h4>Authenticity Indicators</h4>
          <ul>${(rep.authenticity_indicators || []).map((i) => `<li>${escapeHtml(i)}</li>`).join("") || "<li>None recorded</li>"}</ul>

          <h4>Suspicious Indicators</h4>
          <ul>${(rep.suspicious_indicators || []).map((i) => `<li>${escapeHtml(i)}</li>`).join("") || "<li>None recorded</li>"}</ul>

          <h4>Image Quality</h4>
          <ul>
            <li>Resolution: ${rep.image_quality?.width || "?"} × ${rep.image_quality?.height || "?"} (${rep.image_quality?.megapixels || "?"} MP)</li>
            <li>Focus (Laplacian): ${rep.image_quality?.laplacian_variance ?? "?"} — ${escapeHtml(rep.image_quality?.focus_assessment || "")}</li>
            <li>Contrast / Exposure: ${escapeHtml(rep.image_quality?.contrast_assessment || "")} / ${escapeHtml(rep.image_quality?.exposure_assessment || "")}</li>
          </ul>

          <h4>OCR Findings</h4>
          <ul>
            <li>Mean confidence: ${rep.ocr?.mean_confidence ?? "?"}%</li>
            <li>Word count: ${rep.ocr?.word_count ?? "?"}</li>
            <li>Sample text (truncated): ${escapeHtml((rep.ocr?.full_text || "").slice(0, 280))}${(rep.ocr?.full_text || "").length > 280 ? "…" : ""}</li>
          </ul>

          <h4>Typography / Layout</h4>
          <ul>${(rep.typography_layout?.observations || []).map((o) => `<li>${escapeHtml(o)}</li>`).join("")}
              ${(rep.typography_layout?.suspicious || []).map((o) => `<li style="color:var(--warning)">${escapeHtml(o)}</li>`).join("")}
              <li style="color:var(--muted);font-size:0.85rem">${escapeHtml(rep.typography_layout?.notes || "")}</li>
          </ul>

          <h4>Image Forensics (Basic)</h4>
          <ul>${(rep.image_forensics?.observations || []).map((o) => `<li>${escapeHtml(o)}</li>`).join("")}
              ${(rep.image_forensics?.suspicious || []).map((o) => `<li style="color:var(--warning)">${escapeHtml(o)}</li>`).join("")}
              <li style="color:var(--muted);font-size:0.85rem">${escapeHtml(rep.image_forensics?.notes || "")}</li>
          </ul>

          <h4>MRZ / Barcode / QR</h4>
          <ul>
            <li>MRZ detected: ${rep.mrz_qr_barcode?.detected ? "Yes (pattern)" : "No"}</li>
            <li>${escapeHtml(rep.mrz_qr_barcode?.notes || "")}</li>
          </ul>

          <h4>Security Features</h4>
          <table class="security-table">
            <thead><tr><th>Feature</th><th>Status</th></tr></thead>
            <tbody>
              ${(rep.security_features || [])
                .map(
                  (sf) =>
                    `<tr><td>${escapeHtml(sf.feature)}</td><td class="${sf.status.includes("Unable") || sf.status.includes("Not Visible") ? "status-unable" : ""}">${escapeHtml(sf.status)}</td></tr>`
                )
                .join("")}
            </tbody>
          </table>
          <p style="font-size:0.8rem;color:var(--muted);margin-top:0.4rem">“Not Visible” / “Unable to Verify” is never treated as “Missing.” Optical features generally cannot be confirmed from consumer uploads.</p>

          <h4>Metadata</h4>
          <ul>
            <li>SHA-256: <code style="font-size:0.75rem;word-break:break-all">${escapeHtml(rep.file_hash_sha256 || "")}</code></li>
            <li>Size: ${formatSize(rep.file_size_bytes || 0)}</li>
            <li>EXIF keys present: ${Object.keys(rep.metadata?.exif || {}).length}</li>
          </ul>

          <h4>Reference Comparisons</h4>
          <p style="font-size:0.9rem">${escapeHtml(rep.reference_comparisons?.explanation || "Not performed.")}</p>

          <h4>Why This Score</h4>
          <ul>${(rep.score_explanation || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>

          <div class="limitations">
            <strong>Limitations</strong>
            <ul>${(rep.limitations || []).map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>
          </div>

          <div style="margin-top:1rem">
            <button type="button" class="btn secondary btn-view-evidence" data-doc="${r.document_id}">View Evidence / Zoom</button>
          </div>
        </div>
      </article>`;
    })
    .join("");

  resultsContainer.querySelectorAll(".btn-view-evidence").forEach((btn) => {
    btn.addEventListener("click", () => openViewer(btn.dataset.doc));
  });
}

function openViewer(docId) {
  const doc = state.documents.find((d) => d.id === docId);
  const result = state.results.find((r) => r.document_id === docId);
  if (!doc || !doc.file) {
    alert("Original file not available in browser for preview (session may have been refreshed). Analysis data is still shown above.");
    return;
  }
  const url = URL.createObjectURL(doc.file);
  const img = $("#viewerImg");
  img.src = url;
  img.style.transform = "scale(1)";
  $("#viewerTitle").textContent = doc.filename;
  $("#viewerDetails").innerHTML = `
    <p><strong>Classification:</strong> ${result?.report?.classification || "—"}</p>
    <p><strong>OCR confidence:</strong> ${result?.report?.ocr?.mean_confidence ?? "—"}%</p>
    <p>Use zoom controls. Bounding boxes for OCR words are drawn when available.</p>
  `;
  $("#viewerModal").hidden = false;

  img.onload = () => {
    // Draw OCR boxes if we have them
    const canvas = $("#overlayCanvas");
    const wrap = $("#viewerWrap");
    const words = result?.report?.ocr?.sample_words || [];
    if (words.length && img.naturalWidth) {
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.style.width = img.clientWidth + "px";
      canvas.style.height = img.clientHeight + "px";
      const ctx = canvas.getContext("2d");
      ctx.strokeStyle = "rgba(59,130,246,0.7)";
      ctx.lineWidth = 2;
      words.forEach((w) => {
        ctx.strokeRect(w.left, w.top, w.width, w.height);
      });
    } else {
      canvas.width = 0;
      canvas.height = 0;
    }
  };
}

$("#closeViewer").addEventListener("click", () => {
  $("#viewerModal").hidden = true;
  const img = $("#viewerImg");
  if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
});

let zoom = 1;
$("#zoomIn").addEventListener("click", () => {
  zoom = Math.min(4, zoom + 0.25);
  $("#viewerImg").style.transform = `scale(${zoom})`;
});
$("#zoomOut").addEventListener("click", () => {
  zoom = Math.max(0.5, zoom - 0.25);
  $("#viewerImg").style.transform = `scale(${zoom})`;
});
$("#zoomReset").addEventListener("click", () => {
  zoom = 1;
  $("#viewerImg").style.transform = "scale(1)";
});

$("#btnNewSession").addEventListener("click", () => {
  state = { sessionId: null, documents: [], results: null, cross: null };
  sessionBadge.hidden = true;
  renderFileList();
  showStep("step-upload");
});

$("#btnDeleteSession").addEventListener("click", async () => {
  if (!state.sessionId) return;
  if (!confirm("Permanently delete all uploaded files and analysis data for this session?")) return;
  try {
    await fetch(`/api/session/${state.sessionId}`, { method: "DELETE" });
  } catch (_) {}
  state = { sessionId: null, documents: [], results: null, cross: null };
  sessionBadge.hidden = true;
  renderFileList();
  showStep("step-upload");
});

// Init
showStep("step-upload");
console.info("Authenticon UI ready. Evidence-first pipeline.");

// PWA service worker
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("SW reg failed", e));
  });
}
