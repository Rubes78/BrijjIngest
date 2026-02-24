#!/usr/bin/env python3
"""
BrijjData Web Configuration UI
Run with: venv/bin/python web_config.py
Access at: http://<host>:5000
"""

import configparser
import os
import queue
import subprocess
import sys
import threading
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, Response, stream_with_context

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")
PYTHON      = os.path.join(BASE_DIR, "venv",
                           "Scripts" if sys.platform == "win32" else "bin",
                           "python.exe" if sys.platform == "win32" else "python")
INGEST      = os.path.join(BASE_DIR, "ingest_brijj.py")

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ── Job store ─────────────────────────────────────────────────────────────────
# jobs[job_id] = {"company_id": str, "status": "running|done|error",
#                 "lines": [str], "queue": Queue}
JOBS: dict = {}

# ── Config helpers ────────────────────────────────────────────────────────────

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE)
    return cfg


def save_config(cfg: configparser.ConfigParser):
    with open(CONFIG_FILE, "w") as f:
        cfg.write(f)


def get_section(cfg, section, defaults=None) -> dict:
    if cfg.has_section(section):
        return dict(cfg[section])
    return defaults or {}


def get_companies(cfg) -> list:
    companies = []
    for section in cfg.sections():
        if not section.startswith("company:"):
            continue
        sec = cfg[section]
        companies.append({
            "company_id":     sec.get("company_id", ""),
            "company_int_id": sec.get("company_int_id", ""),
            "username":       sec.get("username", ""),
            "password":       sec.get("password", ""),
            "db_name":        sec.get("db_name", ""),
            "enabled":        sec.get("enabled", "false").lower() == "true",
        })
    return companies

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg = load_config()
    db  = get_section(cfg, "database")
    apis = get_section(cfg, "apis")
    companies = get_companies(cfg)
    return render_template("index.html",
                           db=db, apis=apis, companies=companies,
                           config_path=CONFIG_FILE)


@app.route("/database", methods=["GET", "POST"])
def database():
    cfg = load_config()
    if request.method == "POST":
        if not cfg.has_section("database"):
            cfg.add_section("database")
        cfg["database"]["server"]   = request.form["server"].strip()
        cfg["database"]["port"]     = request.form["port"].strip()
        cfg["database"]["user"]     = request.form["user"].strip()
        cfg["database"]["password"] = request.form["password"].strip()
        save_config(cfg)
        flash("Database settings saved.", "success")
        return redirect(url_for("database"))

    db = get_section(cfg, "database", {
        "server": "", "port": "1433", "user": "", "password": ""
    })
    return render_template("database.html", db=db)


@app.route("/apis", methods=["GET", "POST"])
def apis():
    cfg = load_config()
    if request.method == "POST":
        if not cfg.has_section("apis"):
            cfg.add_section("apis")
        cfg["apis"]["rcs_base"]        = request.form["rcs_base"].strip()
        cfg["apis"]["auth_base"]       = request.form["auth_base"].strip()
        cfg["apis"]["sales_base"]      = request.form["sales_base"].strip()
        cfg["apis"]["sales_page_size"] = request.form["sales_page_size"].strip()
        save_config(cfg)
        flash("API endpoint settings saved.", "success")
        return redirect(url_for("apis"))

    api_data = get_section(cfg, "apis", {
        "rcs_base":        "https://productionsystem-api.brijjworks.com",
        "auth_base":       "https://company-api.brijjworks.com/api",
        "sales_base":      "https://pos-api.brijjworks.com/api",
        "sales_page_size": "1000",
    })
    return render_template("apis.html", apis=api_data)


@app.route("/companies")
def companies():
    cfg = load_config()
    return render_template("companies.html", companies=get_companies(cfg))


@app.route("/companies/add", methods=["GET", "POST"])
def company_add():
    if request.method == "POST":
        cfg = load_config()
        cid = request.form["company_id"].strip().upper()
        if not cid:
            flash("Company ID is required.", "error")
            return redirect(url_for("company_add"))

        section = f"company:{cid}"
        if not cfg.has_section(section):
            cfg.add_section(section)

        cfg[section]["company_id"]     = cid
        cfg[section]["company_int_id"] = request.form.get("company_int_id", "").strip()
        cfg[section]["db_name"]        = request.form["db_name"].strip()
        cfg[section]["username"]       = request.form["username"].strip()
        cfg[section]["password"]       = request.form["password"].strip()
        cfg[section]["enabled"]        = "true" if "enabled" in request.form else "false"
        save_config(cfg)
        flash(f"Company {cid} added.", "success")
        return redirect(url_for("companies"))

    blank = {"company_id": "", "company_int_id": "", "db_name": "", "username": "", "password": "", "enabled": True}
    return render_template("company_form.html", company=blank, editing=False)


@app.route("/companies/<company_id>/edit", methods=["GET", "POST"])
def company_edit(company_id):
    cfg = load_config()
    section = f"company:{company_id}"

    if not cfg.has_section(section):
        flash(f"Company '{company_id}' not found.", "error")
        return redirect(url_for("companies"))

    if request.method == "POST":
        cfg[section]["company_int_id"] = request.form.get("company_int_id", "").strip()
        cfg[section]["db_name"]        = request.form["db_name"].strip()
        cfg[section]["username"]       = request.form["username"].strip()
        cfg[section]["password"]       = request.form["password"].strip()
        cfg[section]["enabled"]        = "true" if "enabled" in request.form else "false"
        save_config(cfg)
        flash(f"Company {company_id} updated.", "success")
        return redirect(url_for("companies"))

    sec = cfg[section]
    company = {
        "company_id":     sec.get("company_id", company_id),
        "company_int_id": sec.get("company_int_id", ""),
        "db_name":        sec.get("db_name", ""),
        "username":       sec.get("username", ""),
        "password":       sec.get("password", ""),
        "enabled":        sec.get("enabled", "false").lower() == "true",
    }
    return render_template("company_form.html", company=company, editing=True)


@app.route("/companies/<company_id>/toggle", methods=["POST"])
def company_toggle(company_id):
    cfg = load_config()
    section = f"company:{company_id}"
    if cfg.has_section(section):
        current = cfg[section].get("enabled", "false").lower() == "true"
        cfg[section]["enabled"] = "false" if current else "true"
        save_config(cfg)
        state = "disabled" if current else "enabled"
        flash(f"Company {company_id} {state}.", "success")
    else:
        flash(f"Company '{company_id}' not found.", "error")
    return redirect(url_for("companies"))


@app.route("/companies/<company_id>/delete", methods=["POST"])
def company_delete(company_id):
    cfg = load_config()
    section = f"company:{company_id}"
    if cfg.has_section(section):
        cfg.remove_section(section)
        save_config(cfg)
        flash(f"Company {company_id} removed.", "success")
    else:
        flash(f"Company '{company_id}' not found.", "error")
    return redirect(url_for("companies"))


# ── Ingest routes ─────────────────────────────────────────────────────────────

@app.route("/companies/<company_id>/ingest", methods=["POST"])
def company_ingest(company_id):
    from datetime import date
    today      = date.today().isoformat()
    start_date = request.form.get("start_date", today).strip() or today
    end_date   = request.form.get("end_date",   today).strip() or today

    job_id = uuid.uuid4().hex[:10]
    q = queue.Queue()
    JOBS[job_id] = {
        "company_id": company_id,
        "status":     "running",
        "lines":      [],
        "queue":      q,
    }

    def run():
        try:
            proc = subprocess.Popen(
                [PYTHON, "-u", INGEST,
                 "--company",    company_id,
                 "--start-date", start_date,
                 "--end-date",   end_date],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            for line in proc.stdout:
                line = line.rstrip()
                JOBS[job_id]["lines"].append(line)
                q.put(line)
            proc.wait()
            JOBS[job_id]["status"] = "done" if proc.returncode == 0 else "error"
        except Exception as e:
            JOBS[job_id]["lines"].append(f"ERROR: {e}")
            JOBS[job_id]["status"] = "error"
        finally:
            q.put(None)  # sentinel — signals stream end

    threading.Thread(target=run, daemon=True).start()
    return redirect(url_for("ingest_log", job_id=job_id))


@app.route("/ingest/<job_id>")
def ingest_log(job_id):
    job = JOBS.get(job_id)
    if not job:
        flash("Ingest job not found.", "error")
        return redirect(url_for("companies"))
    return render_template("ingest_log.html", job=job, job_id=job_id)


@app.route("/ingest/<job_id>/status")
def ingest_status(job_id):
    from flask import jsonify
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"status": "error", "error": "not found"}), 404
    return jsonify({"status": job["status"]})


@app.route("/ingest/<job_id>/stream")
def ingest_stream(job_id):
    job = JOBS.get(job_id)

    def generate():
        if not job:
            yield "data: Job not found.\n\nevent: done\ndata: {}\n\n"
            return

        # Replay already-captured lines for late-connecting clients
        for line in list(job["lines"]):
            yield f"data: {line}\n\n"

        if job["status"] != "running":
            yield "event: done\ndata: {}\n\n"
            return

        # Stream live lines from the queue
        while True:
            try:
                line = job["queue"].get(timeout=30)
                if line is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"  # prevents proxy timeouts

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.environ.get("BRIJJ_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIJJ_PORT", 5000))
    print(f"BrijjData Config UI running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
