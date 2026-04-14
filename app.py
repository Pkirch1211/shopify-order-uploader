import os
import csv
import uuid
import json
import threading
from datetime import datetime, UTC
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, session
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

UPLOAD_FOLDER = BASE_DIR / "uploads"
EXPORT_FOLDER = BASE_DIR / "exports"
UPLOAD_FOLDER.mkdir(exist_ok=True)
EXPORT_FOLDER.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# In-memory job store (fine for 4 users, single-process)
jobs = {}


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def make_job_id():
    return str(uuid.uuid4())[:8]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/validate", methods=["POST"])
def api_validate():
    """Upload file and validate it. Returns preview data."""
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

    # Load orders for preview
    try:
        orders = load_orders_from_excel(str(save_path))
    except Exception as e:
        save_path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": f"Failed to parse file: {e}"}), 422

    # Build preview summary
    total_lines = sum(len(o.get("details", [])) for o in orders)
    companies = list({o.get("billToName") or "Unknown" for o in orders})
    po_list = [o.get("poNumber") for o in orders]

    preview_rows = []
    for o in orders[:50]:  # cap preview at 50
        preview_rows.append({
            "po": o.get("poNumber"),
            "company": o.get("billToName") or "—",
            "ship_to": o.get("shipToCity") or "—",
            "lines": len(o.get("details", [])),
            "ship_date": o.get("shipDate") or "—",
        })

    jobs[job_id] = {
        "file_path": str(save_path),
        "status": "ready",
        "orders": orders,
        "results": [],
        "log": [],
    }

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
    job = jobs[job_id]
    orders = job["orders"]
    job["status"] = "running"
    job["log"] = []

    def progress(po, status, msg):
        job["log"].append({"po": po, "status": status, "msg": msg, "ts": datetime.now(UTC).isoformat()})

    try:
        clear_variant_cache()
        if mode == "draft":
            results = process_draft_orders(orders, progress_callback=progress)
        else:
            results = process_live_orders(orders, progress_callback=progress)

        job["results"] = results
        job["status"] = "done"

        # Write export CSV
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode_label = "draft" if mode == "draft" else "order"
        csv_path = EXPORT_FOLDER / f"shopify_{mode_label}_export_{ts}_{job_id}.csv"
        fields = ["po", "company", "status", "reason", "id", "line_count"]
        with open(str(csv_path), "w", newline="", encoding="utf-8") as cf:
            w = csv.DictWriter(cf, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
        job["csv_path"] = str(csv_path)

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["log"].append({"po": "—", "status": "error", "msg": str(e), "ts": datetime.now(UTC).isoformat()})


@app.route("/api/submit", methods=["POST"])
def api_submit():
    data = request.get_json()
    job_id = data.get("job_id")
    mode = data.get("mode")  # "draft" or "order"

    if job_id not in jobs:
        return jsonify({"ok": False, "error": "Unknown job"}), 404
    if mode not in ("draft", "order"):
        return jsonify({"ok": False, "error": "mode must be 'draft' or 'order'"}), 400

    job = jobs[job_id]
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
def api_status(job_id):
    if job_id not in jobs:
        return jsonify({"ok": False, "error": "Unknown job"}), 404

    job = jobs[job_id]
    response = {
        "ok": True,
        "status": job["status"],
        "log": job.get("log", []),
        "results": job.get("results", []),
        "error": job.get("error"),
        "has_csv": bool(job.get("csv_path")),
    }
    return jsonify(response)


@app.route("/api/download/<job_id>")
def api_download(job_id):
    if job_id not in jobs:
        return jsonify({"ok": False, "error": "Unknown job"}), 404
    csv_path = jobs[job_id].get("csv_path")
    if not csv_path or not Path(csv_path).exists():
        return jsonify({"ok": False, "error": "No export available"}), 404
    return send_file(csv_path, as_attachment=True)


@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": datetime.now(UTC).isoformat()})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
