<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Order Uploader · Shopify B2B</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0f0f0f;
    --surface: #1a1a1a;
    --surface2: #222222;
    --border: #2e2e2e;
    --border-bright: #444;
    --text: #e8e8e8;
    --text-muted: #888;
    --text-dim: #555;
    --green: #22c55e;
    --green-dim: #16532e;
    --green-glow: rgba(34,197,94,0.12);
    --amber: #f59e0b;
    --amber-dim: #78350f;
    --red: #ef4444;
    --red-dim: #7f1d1d;
    --blue: #60a5fa;
    --blue-dim: #1e3a5f;
    --mono: 'IBM Plex Mono', monospace;
    --sans: 'DM Sans', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14px;
    min-height: 100vh;
    line-height: 1.5;
  }

  /* ---- Layout ---- */
  .shell {
    max-width: 900px;
    margin: 0 auto;
    padding: 40px 24px 80px;
  }

  /* ---- Header ---- */
  .header {
    display: flex;
    align-items: baseline;
    gap: 16px;
    margin-bottom: 40px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }
  .header-logo {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--green);
    letter-spacing: 0.12em;
    text-transform: uppercase;
    background: var(--green-glow);
    border: 1px solid var(--green-dim);
    padding: 4px 10px;
    border-radius: 4px;
  }
  .header-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--text);
  }
  .header-sub {
    margin-left: auto;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-dim);
  }

  /* ---- Steps ---- */
  .steps {
    display: flex;
    gap: 0;
    margin-bottom: 36px;
  }
  .step {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px 8px 0;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-dim);
    transition: color 0.2s;
  }
  .step.active { color: var(--text); }
  .step.done { color: var(--green); }
  .step-num {
    width: 20px; height: 20px;
    border-radius: 50%;
    border: 1px solid currentColor;
    display: flex; align-items: center; justify-content: center;
    font-size: 10px;
    flex-shrink: 0;
  }
  .step.done .step-num { background: var(--green); color: #000; border-color: var(--green); }
  .step-arrow { color: var(--border-bright); margin-right: 8px; }

  /* ---- Cards ---- */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 24px;
    margin-bottom: 20px;
  }
  .card-title {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 16px;
  }

  /* ---- Drop Zone ---- */
  .dropzone {
    border: 2px dashed var(--border-bright);
    border-radius: 8px;
    padding: 48px 24px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    position: relative;
  }
  .dropzone:hover, .dropzone.drag-over {
    border-color: var(--green);
    background: var(--green-glow);
  }
  .dropzone-icon { font-size: 32px; margin-bottom: 12px; opacity: 0.6; }
  .dropzone-text { color: var(--text-muted); margin-bottom: 8px; }
  .dropzone-hint { font-family: var(--mono); font-size: 11px; color: var(--text-dim); }
  .dropzone input[type=file] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
  }
  .dropzone.has-file { border-color: var(--green); border-style: solid; background: var(--green-glow); }
  .dropzone.has-file .dropzone-icon { opacity: 1; }

  /* ---- Buttons ---- */
  .btn {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 10px 20px;
    border-radius: 6px;
    border: 1px solid transparent;
    font-family: var(--mono);
    font-size: 12px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.15s;
    font-weight: 500;
  }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-primary {
    background: var(--green);
    color: #000;
    border-color: var(--green);
  }
  .btn-primary:hover:not(:disabled) { background: #16a34a; border-color: #16a34a; }
  .btn-ghost {
    background: transparent;
    color: var(--text-muted);
    border-color: var(--border-bright);
  }
  .btn-ghost:hover:not(:disabled) { color: var(--text); border-color: var(--text-muted); }
  .btn-amber {
    background: var(--amber);
    color: #000;
    border-color: var(--amber);
  }
  .btn-amber:hover:not(:disabled) { background: #d97706; }
  .btn-blue {
    background: transparent;
    color: var(--blue);
    border-color: var(--blue-dim);
  }
  .btn-blue:hover:not(:disabled) { background: var(--blue-dim); }

  /* ---- Summary Stats ---- */
  .stats-row {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 20px;
  }
  .stat {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
  }
  .stat-val {
    font-family: var(--mono);
    font-size: 28px;
    font-weight: 500;
    color: var(--green);
    line-height: 1;
    margin-bottom: 4px;
  }
  .stat-label {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  /* ---- Table ---- */
  .table-wrap { overflow-x: auto; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-dim);
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  td {
    padding: 9px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--text-muted);
    vertical-align: middle;
  }
  td:first-child { font-family: var(--mono); font-size: 12px; color: var(--text); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--surface2); }

  /* ---- Mode Selection ---- */
  .mode-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 20px;
  }
  .mode-card {
    background: var(--surface2);
    border: 2px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }
  .mode-card:hover { border-color: var(--border-bright); }
  .mode-card.selected-draft { border-color: var(--amber); background: rgba(245,158,11,0.07); }
  .mode-card.selected-order { border-color: var(--green); background: var(--green-glow); }
  .mode-card-icon { font-size: 24px; margin-bottom: 10px; }
  .mode-card-title { font-weight: 600; margin-bottom: 4px; font-size: 15px; }
  .mode-card-desc { font-size: 12px; color: var(--text-muted); line-height: 1.4; }
  .mode-card input[type=radio] { display: none; }

  /* ---- Alerts ---- */
  .alert {
    padding: 12px 16px;
    border-radius: 6px;
    font-size: 13px;
    margin-bottom: 16px;
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }
  .alert-error { background: rgba(239,68,68,0.1); border: 1px solid var(--red-dim); color: #fca5a5; }
  .alert-warn { background: rgba(245,158,11,0.1); border: 1px solid var(--amber-dim); color: #fcd34d; }
  .alert-info { background: rgba(96,165,250,0.1); border: 1px solid var(--blue-dim); color: #93c5fd; }
  .alert-success { background: rgba(34,197,94,0.1); border: 1px solid var(--green-dim); color: #86efac; }
  .alert ul { margin-top: 4px; padding-left: 16px; }
  .alert li { margin-bottom: 2px; }

  /* ---- Log stream ---- */
  .log-box {
    background: #0a0a0a;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
    font-family: var(--mono);
    font-size: 12px;
    max-height: 280px;
    overflow-y: auto;
    margin-bottom: 20px;
  }
  .log-line { margin-bottom: 4px; line-height: 1.4; }
  .log-ts { color: var(--text-dim); margin-right: 8px; }
  .log-po { color: var(--blue); margin-right: 6px; }
  .log-created { color: var(--green); }
  .log-skipped { color: var(--text-dim); }
  .log-error { color: var(--red); }
  .log-processing { color: var(--amber); }

  /* ---- Results summary ---- */
  .result-chips {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 20px;
  }
  .chip {
    padding: 6px 14px;
    border-radius: 20px;
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 500;
  }
  .chip-green { background: var(--green-dim); color: var(--green); }
  .chip-amber { background: var(--amber-dim); color: var(--amber); }
  .chip-red { background: var(--red-dim); color: var(--red); }

  /* ---- Actions row ---- */
  .actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }

  /* ---- Spinner ---- */
  .spinner {
    width: 14px; height: 14px;
    border: 2px solid rgba(0,0,0,0.3);
    border-top-color: currentColor;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: inline-block;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ---- Progress bar ---- */
  .progress-bar-wrap {
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    margin-bottom: 20px;
    overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%;
    background: var(--green);
    border-radius: 2px;
    transition: width 0.3s ease;
  }

  /* ---- Warning tag ---- */
  .tag {
    display: inline-block;
    font-family: var(--mono);
    font-size: 10px;
    padding: 2px 7px;
    border-radius: 3px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .tag-green { background: var(--green-dim); color: var(--green); }
  .tag-amber { background: var(--amber-dim); color: var(--amber); }
  .tag-red { background: var(--red-dim); color: var(--red); }
  .tag-blue { background: var(--blue-dim); color: var(--blue); }

  /* ---- Hidden by default ---- */
  .hidden { display: none !important; }

  /* ---- Divider ---- */
  .divider { height: 1px; background: var(--border); margin: 24px 0; }

  /* ---- Confirm banner ---- */
  .confirm-banner {
    background: #111;
    border: 1px solid var(--border-bright);
    border-radius: 8px;
    padding: 20px 24px;
    display: flex;
    align-items: center;
    gap: 20px;
    flex-wrap: wrap;
  }
  .confirm-banner-text { flex: 1; min-width: 200px; }
  .confirm-banner-title { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
  .confirm-banner-sub { font-size: 13px; color: var(--text-muted); }
</style>
</head>
<body>
<div class="shell">

  <!-- Header -->
  <div class="header">
    <div class="header-logo">Shopify B2B</div>
    <div class="header-title">Order Uploader</div>
    <div class="header-sub" id="clock"></div>
  </div>

  <!-- Steps indicator -->
  <div class="steps" id="steps-bar">
    <div class="step active" id="step1"><div class="step-num">1</div> Upload</div>
    <div class="step-arrow">›</div>
    <div class="step" id="step2"><div class="step-num">2</div> Review</div>
    <div class="step-arrow">›</div>
    <div class="step" id="step3"><div class="step-num">3</div> Choose Mode</div>
    <div class="step-arrow">›</div>
    <div class="step" id="step4"><div class="step-num">4</div> Processing</div>
    <div class="step-arrow">›</div>
    <div class="step" id="step5"><div class="step-num">5</div> Done</div>
  </div>

  <!-- ============ PHASE 1: Upload ============ -->
  <div id="phase-upload">
    <div class="card">
      <div class="card-title">Select Excel File</div>
      <div class="dropzone" id="dropzone">
        <input type="file" id="file-input" accept=".xlsx,.xls">
        <div class="dropzone-icon" id="dz-icon">📂</div>
        <div class="dropzone-text" id="dz-text">Drop your <strong>Flat File</strong> Excel here, or click to browse</div>
        <div class="dropzone-hint" id="dz-hint">.xlsx or .xls · Requires "Flat File" sheet · Max 10MB</div>
      </div>
    </div>

    <div id="upload-errors" class="hidden"></div>

    <div class="actions">
      <button class="btn btn-primary" id="btn-validate" disabled>
        <span class="spinner hidden" id="validate-spinner"></span>
        Validate & Preview
      </button>
    </div>
  </div>

  <!-- ============ PHASE 2: Review ============ -->
  <div id="phase-review" class="hidden">
    <div id="review-warnings"></div>

    <div class="stats-row">
      <div class="stat">
        <div class="stat-val" id="stat-pos">—</div>
        <div class="stat-label">Purchase Orders</div>
      </div>
      <div class="stat">
        <div class="stat-val" id="stat-lines">—</div>
        <div class="stat-label">Line Items</div>
      </div>
      <div class="stat">
        <div class="stat-val" id="stat-companies">—</div>
        <div class="stat-label">Companies</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Order Preview</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>PO Number</th>
              <th>Company</th>
              <th>Ship To</th>
              <th>Lines</th>
              <th>Ship Date</th>
            </tr>
          </thead>
          <tbody id="preview-tbody"></tbody>
        </table>
      </div>
      <div id="preview-overflow" class="hidden" style="padding-top:12px;">
        <span style="font-family:var(--mono);font-size:11px;color:var(--text-dim)">
          Showing first 50 rows — all POs will be processed
        </span>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn-primary" id="btn-to-mode">Continue to Mode Selection →</button>
      <button class="btn btn-ghost" id="btn-back-upload">← Start Over</button>
    </div>
  </div>

  <!-- ============ PHASE 3: Mode Selection ============ -->
  <div id="phase-mode" class="hidden">
    <div class="card">
      <div class="card-title">How should these orders be created?</div>

      <div class="mode-grid">
        <label class="mode-card" id="mode-draft-card">
          <input type="radio" name="mode" value="draft" id="mode-draft">
          <div class="mode-card-icon">📋</div>
          <div class="mode-card-title">Draft Orders</div>
          <div class="mode-card-desc">Creates editable draft orders in Shopify. Review and complete manually. Safer for review before committing.</div>
        </label>

        <label class="mode-card" id="mode-order-card">
          <input type="radio" name="mode" value="order" id="mode-order">
          <div class="mode-card-icon">✅</div>
          <div class="mode-card-title">Live Orders</div>
          <div class="mode-card-desc">Creates orders directly with financial status <strong>PENDING</strong>. Immediate and final. Cannot be easily undone.</div>
        </label>
      </div>

      <div id="mode-warning" class="hidden">
        <div class="alert alert-warn">
          ⚠️ <div><strong>Live Order mode:</strong> This will create real orders immediately in your Shopify store. Duplicate POs are checked and skipped, but otherwise this action is final. Confirm you want to proceed.</div>
        </div>
      </div>
    </div>

    <div class="confirm-banner" id="confirm-banner">
      <div class="confirm-banner-text">
        <div class="confirm-banner-title" id="confirm-title">Select a mode above</div>
        <div class="confirm-banner-sub" id="confirm-sub">Choose draft or live order before submitting</div>
      </div>
      <div class="actions">
        <button class="btn btn-ghost" id="btn-back-review">← Back</button>
        <button class="btn btn-primary" id="btn-submit" disabled>Submit</button>
      </div>
    </div>
  </div>

  <!-- ============ PHASE 4: Processing ============ -->
  <div id="phase-processing" class="hidden">
    <div class="card">
      <div class="card-title">
        <span class="spinner" style="vertical-align:middle;margin-right:6px;border-top-color:var(--green)"></span>
        Processing Orders
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" id="progress-bar" style="width:0%"></div>
      </div>
      <div class="log-box" id="log-box">
        <div class="log-line log-processing"><span class="log-ts">—</span> Initialising…</div>
      </div>
    </div>
  </div>

  <!-- ============ PHASE 5: Done ============ -->
  <div id="phase-done" class="hidden">
    <div class="alert alert-success" id="done-banner">
      ✅ Processing complete
    </div>

    <div class="result-chips" id="result-chips"></div>

    <div class="card">
      <div class="card-title">Results</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>PO Number</th>
              <th>Company</th>
              <th>Status</th>
              <th>Detail</th>
              <th>Shopify ID</th>
            </tr>
          </thead>
          <tbody id="results-tbody"></tbody>
        </table>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn-green btn-primary" id="btn-download" style="display:none">
        ↓ Download Results CSV
      </button>
      <button class="btn btn-ghost" id="btn-new-upload">Upload Another File</button>
    </div>
  </div>

</div><!-- /shell -->

<script>
let currentJobId = null;
let pollInterval = null;
let totalOrders = 0;

// Clock
function updateClock() {
  const d = new Date();
  document.getElementById('clock').textContent =
    d.toLocaleDateString('en-US', {month:'short',day:'numeric'}) + ' · ' +
    d.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'});
}
updateClock();
setInterval(updateClock, 10000);

// Step helpers
function setStep(n) {
  for (let i = 1; i <= 5; i++) {
    const el = document.getElementById(`step${i}`);
    el.classList.remove('active', 'done');
    if (i < n) el.classList.add('done');
    else if (i === n) el.classList.add('active');
  }
}

// Phase helpers
function showPhase(name) {
  ['upload','review','mode','processing','done'].forEach(p => {
    document.getElementById(`phase-${p}`).classList.add('hidden');
  });
  document.getElementById(`phase-${name}`).classList.remove('hidden');
}

// File input
const fileInput = document.getElementById('file-input');
const dropzone = document.getElementById('dropzone');
const btnValidate = document.getElementById('btn-validate');

fileInput.addEventListener('change', () => {
  if (fileInput.files.length) {
    const f = fileInput.files[0];
    dropzone.classList.add('has-file');
    document.getElementById('dz-icon').textContent = '📄';
    document.getElementById('dz-text').innerHTML = `<strong>${escHtml(f.name)}</strong>`;
    document.getElementById('dz-hint').textContent = `${(f.size/1024).toFixed(1)} KB`;
    btnValidate.disabled = false;
  }
});

dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('drag-over'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
dropzone.addEventListener('drop', e => {
  e.preventDefault();
  dropzone.classList.remove('drag-over');
  const dt = e.dataTransfer;
  if (dt.files.length) {
    fileInput.files = dt.files;
    fileInput.dispatchEvent(new Event('change'));
  }
});

// Validate button
document.getElementById('btn-validate').addEventListener('click', async () => {
  const f = fileInput.files[0];
  if (!f) return;

  btnValidate.disabled = true;
  document.getElementById('validate-spinner').classList.remove('hidden');
  document.getElementById('upload-errors').classList.add('hidden');

  const fd = new FormData();
  fd.append('file', f);

  try {
    const res = await fetch('/api/validate', { method: 'POST', body: fd });
    const data = await res.json();

    if (!data.ok) {
      const errs = data.errors || [data.error || 'Unknown error'];
      document.getElementById('upload-errors').innerHTML =
        `<div class="alert alert-error">
          <div>⛔ <strong>Validation failed:</strong><ul>${errs.map(e=>`<li>${escHtml(e)}</li>`).join('')}</ul></div>
        </div>`;
      document.getElementById('upload-errors').classList.remove('hidden');
      btnValidate.disabled = false;
      document.getElementById('validate-spinner').classList.add('hidden');
      return;
    }

    currentJobId = data.job_id;
    totalOrders = data.summary.po_count;
    renderReview(data);
    setStep(2);
    showPhase('review');

  } catch(e) {
    alert('Network error: ' + e.message);
    btnValidate.disabled = false;
    document.getElementById('validate-spinner').classList.add('hidden');
  }
});

function renderReview(data) {
  const s = data.summary;
  document.getElementById('stat-pos').textContent = s.po_count;
  document.getElementById('stat-lines').textContent = s.total_lines;
  document.getElementById('stat-companies').textContent = s.companies.length;

  const warnDiv = document.getElementById('review-warnings');
  warnDiv.innerHTML = '';
  if (data.warnings && data.warnings.length) {
    warnDiv.innerHTML = `<div class="alert alert-warn">⚠️ <div><strong>Warnings:</strong><ul>${data.warnings.map(w=>`<li>${escHtml(w)}</li>`).join('')}</ul></div></div>`;
  }

  const tbody = document.getElementById('preview-tbody');
  tbody.innerHTML = data.preview.map(row => `
    <tr>
      <td>${escHtml(row.po)}</td>
      <td>${escHtml(row.company)}</td>
      <td>${escHtml(row.ship_to)}</td>
      <td><span class="tag tag-blue">${row.lines}</span></td>
      <td>${escHtml(row.ship_date)}</td>
    </tr>
  `).join('');

  if (s.po_count > 50) {
    document.getElementById('preview-overflow').classList.remove('hidden');
  }
}

// Navigation
document.getElementById('btn-to-mode').addEventListener('click', () => { setStep(3); showPhase('mode'); });
document.getElementById('btn-back-upload').addEventListener('click', resetAll);
document.getElementById('btn-back-review').addEventListener('click', () => { setStep(2); showPhase('review'); });

// Mode selection
const modeRadios = document.querySelectorAll('input[name=mode]');
const btnSubmit = document.getElementById('btn-submit');

modeRadios.forEach(r => r.addEventListener('change', () => {
  const val = document.querySelector('input[name=mode]:checked')?.value;
  document.getElementById('mode-draft-card').classList.remove('selected-draft');
  document.getElementById('mode-order-card').classList.remove('selected-order');

  if (val === 'draft') {
    document.getElementById('mode-draft-card').classList.add('selected-draft');
    document.getElementById('mode-warning').classList.add('hidden');
    document.getElementById('confirm-title').textContent = `Create ${totalOrders} Draft Order${totalOrders !== 1 ? 's' : ''}`;
    document.getElementById('confirm-sub').textContent = 'Orders will be created as editable drafts in Shopify';
    btnSubmit.textContent = '📋 Create Drafts';
    btnSubmit.className = 'btn btn-amber';
    btnSubmit.disabled = false;
  } else if (val === 'order') {
    document.getElementById('mode-order-card').classList.add('selected-order');
    document.getElementById('mode-warning').classList.remove('hidden');
    document.getElementById('confirm-title').textContent = `Create ${totalOrders} Live Order${totalOrders !== 1 ? 's' : ''}`;
    document.getElementById('confirm-sub').textContent = 'Orders will be created immediately with status PENDING';
    btnSubmit.textContent = '✅ Create Orders';
    btnSubmit.className = 'btn btn-primary';
    btnSubmit.disabled = false;
  }
}));

// Submit
document.getElementById('btn-submit').addEventListener('click', async () => {
  const mode = document.querySelector('input[name=mode]:checked')?.value;
  if (!mode || !currentJobId) return;

  if (mode === 'order') {
    const ok = confirm(`⚠️ You are about to create ${totalOrders} LIVE order(s) in Shopify with status PENDING.\n\nThis cannot be easily undone. Continue?`);
    if (!ok) return;
  }

  setStep(4);
  showPhase('processing');
  document.getElementById('log-box').innerHTML = '';

  try {
    const res = await fetch('/api/submit', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ job_id: currentJobId, mode })
    });
    const data = await res.json();
    if (!data.ok) { alert('Error: ' + data.error); return; }
    startPolling(currentJobId, totalOrders);
  } catch(e) {
    alert('Submit error: ' + e.message);
  }
});

// Polling
function startPolling(jobId, total) {
  let lastLogLen = 0;
  let processed = 0;

  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${jobId}`);
      const data = await res.json();

      // Append new log lines
      const newLogs = data.log.slice(lastLogLen);
      lastLogLen = data.log.length;
      const logBox = document.getElementById('log-box');

      newLogs.forEach(entry => {
        const ts = new Date(entry.ts).toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const cls = `log-${entry.status}`;
        logBox.innerHTML += `<div class="log-line"><span class="log-ts">${ts}</span><span class="log-po">[${escHtml(entry.po)}]</span><span class="${cls}">${escHtml(entry.msg)}</span></div>`;
        logBox.scrollTop = logBox.scrollHeight;

        if (['created','skipped','error'].includes(entry.status)) processed++;
      });

      // Progress bar
      if (total > 0) {
        const pct = Math.min(100, Math.round((processed / total) * 100));
        document.getElementById('progress-bar').style.width = pct + '%';
      }

      if (data.status === 'done' || data.status === 'error') {
        clearInterval(pollInterval);
        document.getElementById('progress-bar').style.width = '100%';
        renderResults(data);
        setStep(5);
        showPhase('done');
      }

    } catch(e) {
      console.error('Poll error:', e);
    }
  }, 1200);
}

function renderResults(data) {
  const results = data.results || [];
  const created = results.filter(r => r.status === 'created').length;
  const skipped = results.filter(r => r.status === 'skipped').length;
  const errors  = results.filter(r => r.status === 'error').length;

  document.getElementById('done-banner').innerHTML =
    `✅ Processing complete — <strong>${created}</strong> created, <strong>${skipped}</strong> skipped, <strong>${errors}</strong> errors`;

  document.getElementById('result-chips').innerHTML = `
    ${created ? `<span class="chip chip-green">✓ ${created} Created</span>` : ''}
    ${skipped ? `<span class="chip chip-amber">⊘ ${skipped} Skipped</span>` : ''}
    ${errors  ? `<span class="chip chip-red">✗ ${errors} Errors</span>` : ''}
  `;

  const tbody = document.getElementById('results-tbody');
  tbody.innerHTML = results.map(r => {
    const statusTag = r.status === 'created'
      ? `<span class="tag tag-green">created</span>`
      : r.status === 'skipped'
      ? `<span class="tag tag-amber">skipped</span>`
      : `<span class="tag tag-red">error</span>`;
    const shortId = r.id ? r.id.split('/').pop() : '—';
    return `<tr>
      <td>${escHtml(r.po || '—')}</td>
      <td>${escHtml(r.company || '—')}</td>
      <td>${statusTag}</td>
      <td style="max-width:280px;white-space:normal;font-size:12px">${escHtml(r.reason || '—')}</td>
      <td style="font-family:var(--mono);font-size:11px;color:var(--text-dim)">${escHtml(shortId)}</td>
    </tr>`;
  }).join('');

  if (data.has_csv) {
    const dl = document.getElementById('btn-download');
    dl.style.display = 'inline-flex';
    dl.onclick = () => window.location.href = `/api/download/${currentJobId}`;
  }
}

// New upload
document.getElementById('btn-new-upload').addEventListener('click', resetAll);

function resetAll() {
  if (pollInterval) clearInterval(pollInterval);
  currentJobId = null;
  totalOrders = 0;
  fileInput.value = '';
  dropzone.classList.remove('has-file','drag-over');
  document.getElementById('dz-icon').textContent = '📂';
  document.getElementById('dz-text').innerHTML = 'Drop your <strong>Flat File</strong> Excel here, or click to browse';
  document.getElementById('dz-hint').textContent = '.xlsx or .xls · Requires "Flat File" sheet · Max 10MB';
  btnValidate.disabled = true;
  document.getElementById('validate-spinner').classList.add('hidden');
  document.getElementById('upload-errors').classList.add('hidden');
  document.getElementById('mode-draft-card').classList.remove('selected-draft');
  document.getElementById('mode-order-card').classList.remove('selected-order');
  document.getElementById('mode-warning').classList.add('hidden');
  document.getElementById('btn-submit').disabled = true;
  document.getElementById('btn-submit').textContent = 'Submit';
  document.getElementById('btn-download').style.display = 'none';
  document.getElementById('progress-bar').style.width = '0%';
  setStep(1);
  showPhase('upload');
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>
