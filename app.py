import os
import csv
import uuid
import json
import secrets
import threading
from datetime import datetime, UTC
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, make_response
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from shopify_core import init_shopify, validate_excel, load_orders_from_excel, clear_variant_cache
from draft_processor import process_draft_orders
from order_processor import process_live_orders

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", os.urandom(24).hex())

# ── Password protection ───────────────────────────────────────────────────────
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "edpd")
AUTH_COOKIE = "ops_auth"
_authed_tokens = set()

LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>LifeLines Order Uploader — Login</title>
<style>
@import url('https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f0f0eb;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:'Satoshi',sans-serif}
.box{background:#f7f7f3;border:1px solid #d4d4cc;border-radius:4px;padding:40px;width:320px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
.brand{font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#4a6741;margin-bottom:24px}
h1{font-size:16px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#2a2a28;margin-bottom:24px}
input{width:100%%;background:#fff;border:1px solid #c8c8c0;color:#2a2a28;font-family:'Satoshi',sans-serif;font-size:14px;padding:10px 12px;border-radius:2px;outline:none;margin-bottom:12px}
input:focus{border-color:#4a6741}
button{width:100%%;background:#4a6741;color:#fff;border:none;font-family:'Satoshi',sans-serif;font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:10px;border-radius:2px;cursor:pointer}
button:hover{opacity:.85}
.err{color:#8a2a2a;font-size:12px;margin-top:8px}
</style></head>
<body><div class="box">
<div class="brand">LifeLines</div>
<h1>Order Uploader</h1>
<form method="POST" action="/login">
<input type="password" name="password" placeholder="Password" autofocus>
<button type="submit">Enter</button>
%(error)s
</form>
</div></body></html>"""

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == SITE_PASSWORD:
            token = secrets.token_hex(32)
            _authed_tokens.add(token)
            resp = make_response(redirect("/"))
            resp.set_cookie(AUTH_COOKIE, token, max_age=60*60*24*30, httponly=True)
            return resp
        return LOGIN_HTML % {"error": '<div class="err">Incorrect password</div>'}, 401
    return LOGIN_HTML % {"error": ""}

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get(AUTH_COOKIE)
        if not token or token not in _authed_tokens:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# ── Config ────────────────────────────────────────────────────────────────────
UPLOAD_FOLDER = BASE_DIR / "uploads"
EXPORT_FOLDER = BASE_DIR / "exports"
JOBS_FOLDER   = BASE_DIR / "jobs"
UPLOAD_FOLDER.mkdir(exist_ok=True)
EXPORT_FOLDER.mkdir(exist_ok=True)
JOBS_FOLDER.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# Thread lock for job file writes
_job_locks = {}
_job_locks_lock = threading.Lock()

# Cancellation events per job
_job_cancel_events: dict[str, threading.Event] = {}
_job_cancel_lock = threading.Lock()


def _get_cancel_event(job_id: str) -> threading.Event:
    with _job_cancel_lock:
        if job_id not in _job_cancel_events:
            _job_cancel_events[job_id] = threading.Event()
        return _job_cancel_events[job_id]


def _get_job_lock(job_id):
    with _job_locks_lock:
        if job_id not in _job_locks:
            _job_locks[job_id] = threading.Lock()
        return _job_locks[job_id]

def _job_path(job_id):
    return JOBS_FOLDER / f"{job_id}.json"

def _load_job(job_id):
    p = _job_path(job_id)
    if not p.exists():
        return None
    with _get_job_lock(job_id):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

def _save_job(job_id, job):
    with _get_job_lock(job_id):
        with open(_job_path(job_id), "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)

def _update_job(job_id, **kwargs):
    job = _load_job(job_id) or {}
    job.update(kwargs)
    _save_job(job_id, job)
    return job

def _append_log(job_id, entry):
    with _get_job_lock(job_id):
        p = _job_path(job_id)
        job = {}
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                job = json.load(f)
        job.setdefault("log", []).append(entry)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def make_job_id():
    return str(uuid.uuid4())[:8]


@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/api/validate", methods=["POST"])
@require_auth
def api_validate():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename or not allowed_file(f.filename):
        return jsonify({"ok": False, "error": "File must be .xlsx or .xls"}), 400

    try:
        init_shopify()
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    filename = secure_filename(f.filename)
    job_id = make_job_id()
    save_path = UPLOAD_FOLDER / f"{job_id}_{filename}"
    f.save(str(save_path))

    is_valid, errors, warnings, columns = validate_excel(str(save_path))

    if not is_valid:
        save_path.unlink(missing_ok=True)
        return jsonify({"ok": False, "errors": errors, "warnings": warnings}), 422

    try:
        orders = load_orders_from_excel(str(save_path))
    except Exception as e:
        save_path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": f"Failed to parse file: {e}"}), 422

    total_lines = sum(len(o.get("details", [])) for o in orders)
    companies = list({o.get("billToName") or "Unknown" for o in orders})
    po_list = [o.get("poNumber") for o in orders]

    preview_rows = []
    for o in orders[:50]:
        preview_rows.append({
            "po": o.get("poNumber"),
            "company": o.get("billToName") or "—",
            "ship_to": o.get("shipToCity") or "—",
            "lines": len(o.get("details", [])),
            "ship_date": o.get("shipDate") or "—",
        })

    job = {
        "file_path": str(save_path),
        "status": "ready",
        "orders": orders,
        "results": [],
        "log": [],
        "csv_path": None,
    }
    _save_job(job_id, job)

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "warnings": warnings,
        "summary": {
            "po_count": len(orders),
            "total_lines": total_lines,
            "companies": companies[:10],
            "po_list": po_list,
        },
        "preview": preview_rows,
    })


def _run_job(job_id, mode):
    cancel_event = _get_cancel_event(job_id)
    cancel_event.clear()

    job = _load_job(job_id)
    orders = job["orders"]
    _update_job(job_id, status="running", log=[])

    def progress(po, status, msg):
        _append_log(job_id, {
            "po": po, "status": status, "msg": msg,
            "ts": datetime.now(UTC).isoformat()
        })

    try:
        clear_variant_cache()
        if mode == "draft":
            results = process_draft_orders(orders=orders, progress_callback=progress, cancel_event=cancel_event)
        else:
            results = process_live_orders(orders=orders, progress_callback=progress, cancel_event=cancel_event)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode_label = "draft" if mode == "draft" else "order"
        csv_path = EXPORT_FOLDER / f"shopify_{mode_label}_export_{ts}_{job_id}.csv"
        fields = ["po", "company", "status", "reason", "id", "line_count"]
        with open(str(csv_path), "w", newline="", encoding="utf-8") as cf:
            w = csv.DictWriter(cf, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)

        final_status = "cancelled" if cancel_event.is_set() else "done"
        _update_job(job_id, results=results, csv_path=str(csv_path))
        _update_job(job_id, status=final_status)

    except Exception as e:
        _append_log(job_id, {
            "po": "—", "status": "error", "msg": str(e),
            "ts": datetime.now(UTC).isoformat()
        })
        _update_job(job_id, status="error", error=str(e))


@app.route("/api/submit", methods=["POST"])
@require_auth
def api_submit():
    data = request.get_json()
    job_id = data.get("job_id")
    mode = data.get("mode")

    job = _load_job(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Unknown job"}), 404
    if mode not in ("draft", "order"):
        return jsonify({"ok": False, "error": "mode must be 'draft' or 'order'"}), 400
    if job["status"] not in ("ready",):
        return jsonify({"ok": False, "error": f"Job is already {job['status']}"}), 409

    try:
        init_shopify()
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    t = threading.Thread(target=_run_job, args=(job_id, mode), daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/status/<job_id>")
@require_auth
def api_status(job_id):
    job = _load_job(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Unknown job"}), 404

    return jsonify({
        "ok": True,
        "status": job.get("status"),
        "log": job.get("log", []),
        "results": job.get("results", []),
        "error": job.get("error"),
        "has_csv": bool(job.get("csv_path")),
    })


@app.route("/api/cancel/<job_id>", methods=["POST"])
@require_auth
def api_cancel(job_id):
    job = _load_job(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Unknown job"}), 404
    if job.get("status") != "running":
        return jsonify({"ok": False, "error": "Job is not running"}), 409
    _get_cancel_event(job_id).set()
    return jsonify({"ok": True})


@app.route("/api/download/<job_id>")
@require_auth
def api_download(job_id):
    job = _load_job(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Unknown job"}), 404
    csv_path = job.get("csv_path")
    if not csv_path or not Path(csv_path).exists():
        return jsonify({"ok": False, "error": "No export available"}), 404
    return send_file(csv_path, as_attachment=True)


@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": datetime.now(UTC).isoformat()})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
