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
import pyodbc
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

# ── DB stats ──────────────────────────────────────────────────────────────────

def get_company_stats(cfg: configparser.ConfigParser, companies: list) -> dict:
    """Return row counts for each company's database. Never raises."""
    db   = get_section(cfg, "database")
    srv  = db.get("server", "")
    prt  = db.get("port", "1433")
    usr  = db.get("user", "")
    pwd  = db.get("password", "")
    if not srv:
        return {}

    stats = {}
    for c in companies:
        cid     = c["company_id"]
        db_name = c.get("db_name", "")
        if not db_name:
            stats[cid] = None
            continue
        try:
            conn = pyodbc.connect(
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={srv},{prt};DATABASE={db_name};"
                f"UID={usr};PWD={pwd};TrustServerCertificate=yes;",
                timeout=5, autocommit=True,
            )
            cur = conn.cursor()
            counts = {}
            for table in ("rcs_items", "sales_orders", "sales_order_items", "sales_payments"):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table] = cur.fetchone()[0]
                except Exception:
                    counts[table] = None
            conn.close()
            stats[cid] = counts
        except Exception:
            stats[cid] = None   # DB unreachable or not yet created
    return stats

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg       = load_config()
    db        = get_section(cfg, "database")
    apis      = get_section(cfg, "apis")
    companies = get_companies(cfg)
    stats     = get_company_stats(cfg, companies)
    return render_template("index.html",
                           db=db, apis=apis, companies=companies,
                           stats=stats, config_path=CONFIG_FILE)


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


# ── Reports ───────────────────────────────────────────────────────────────────

def run_report_query(cfg, company_id, sql, params=()):
    """Run a query against a company database. Returns (columns, rows) or raises."""
    db      = get_section(cfg, "database")
    srv     = db.get("server", "")
    prt     = db.get("port", "1433")
    usr     = db.get("user", "")
    pwd     = db.get("password", "")
    db_name = ""
    for sec in cfg.sections():
        if sec == f"company:{company_id}":
            db_name = cfg[sec].get("db_name", "")
            break
    if not srv or not db_name:
        raise ValueError("Database not configured.")
    conn = pyodbc.connect(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={srv},{prt};DATABASE={db_name};"
        f"UID={usr};PWD={pwd};TrustServerCertificate=yes;",
        timeout=10, autocommit=True,
    )
    cur = conn.cursor()
    cur.execute(sql, params)
    columns = [d[0] for d in cur.description]
    rows    = cur.fetchall()
    conn.close()
    return columns, rows


@app.route("/reports/items-produced", methods=["GET"])
def report_items_produced():
    cfg       = load_config()
    companies = get_companies(cfg)

    # Form inputs
    company_id = request.args.get("company_id", "")
    start_date = request.args.get("start_date", "")
    end_date   = request.args.get("end_date", "")
    group_by   = request.args.get("group_by", "department")

    VALID_GROUPS = {
        "department":       ("department",         "Department"),
        "category":         ("categoryName",       "Category"),
        "subcategory":      ("subcategoryName",    "Subcategory"),
        "quality":          ("qualityDescr",       "Quality"),
        "condition":        ("conditionDescr",     "Condition"),
        "producingstore":   ("productionStoreName","Production Store"),
        "processorname":    ("processorName",      "Processor"),
        "productioncycle":  ("productionCycle",    "Production Cycle"),
    }
    group_col, group_label = VALID_GROUPS.get(group_by, ("department", "Department"))

    columns, rows, error = [], [], None

    if company_id and start_date and end_date:
        try:
            sql = f"""
                SELECT
                    ISNULL([{group_col}], '(none)') AS group_label,
                    COUNT(*)                 AS item_count,
                    SUM(qtyProduced)         AS total_qty,
                    AVG(price)               AS avg_price,
                    SUM(price * qtyProduced) AS total_value
                FROM rcs_items
                WHERE CAST(lastUpdatedDt AS DATE) >= ? AND CAST(lastUpdatedDt AS DATE) <= ?
                GROUP BY [{group_col}]
                ORDER BY total_qty DESC
            """
            columns, rows = run_report_query(cfg, company_id, sql, (start_date, end_date))
        except Exception as e:
            error = str(e)

    return render_template(
        "report_items_produced.html",
        companies=companies,
        company_id=company_id,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
        group_label=group_label,
        columns=columns,
        rows=rows,
        error=error,
    )


@app.route("/reports/sales", methods=["GET"])
def report_sales():
    cfg       = load_config()
    companies = get_companies(cfg)

    company_id = request.args.get("company_id", "")
    start_date = request.args.get("start_date", "")
    end_date   = request.args.get("end_date", "")
    group_by   = request.args.get("group_by", "date")

    # group_col = SQL column, group_label = display name, group_mode = template branch
    VALID_GROUPS = {
        "date":           ("date",        "Date",           "order"),
        "store":          ("storeName",   "Store",          "order"),
        "sales_type":     ("salesType",   "Sales Type",     "order"),
        "cashier":        ("userName",    "Cashier",        "order"),
        "product":        (None,          "Product",        "product"),
        "payment_method": (None,          "Payment Method", "payment"),
    }
    group_col, group_label, group_mode = VALID_GROUPS.get(
        group_by, ("date", "Date", "order")
    )

    summary, rows, error = None, [], None

    if company_id and start_date and end_date:
        try:
            # Summary — always runs; joins all 3 tables
            summary_sql = """
                SELECT
                    COUNT(DISTINCT o.salesOrderId)   AS total_orders,
                    ISNULL(SUM(o.totalAmount),    0) AS total_revenue,
                    ISNULL(SUM(o.taxAmount),      0) AS total_tax,
                    ISNULL(SUM(o.discountAmount), 0) AS total_discounts,
                    ISNULL(AVG(o.totalAmount),    0) AS avg_order_value,
                    ISNULL(SUM(ic.item_count),    0) AS total_line_items,
                    ISNULL(SUM(pc.payment_total), 0) AS total_payments
                FROM sales_orders o
                LEFT JOIN (
                    SELECT salesOrderId, COUNT(*) AS item_count
                    FROM   sales_order_items
                    GROUP  BY salesOrderId
                ) ic ON ic.salesOrderId = o.salesOrderId
                LEFT JOIN (
                    SELECT salesOrderId, SUM(amount) AS payment_total
                    FROM   sales_payments
                    GROUP  BY salesOrderId
                ) pc ON pc.salesOrderId = o.salesOrderId
                WHERE o.date >= ? AND o.date <= ?
            """
            _, sum_rows = run_report_query(cfg, company_id, summary_sql, (start_date, end_date))
            if sum_rows:
                summary = sum_rows[0]

            # Detail — query and columns depend on group_by
            if group_mode == "order":
                order_clause = "ORDER BY group_label ASC" if group_by == "date" else "ORDER BY revenue DESC"
                detail_sql = f"""
                    SELECT
                        ISNULL(CAST([{group_col}] AS NVARCHAR(255)), '(none)') AS group_label,
                        COUNT(*)                        AS order_count,
                        ISNULL(SUM(totalAmount),    0) AS revenue,
                        ISNULL(AVG(totalAmount),    0) AS avg_order,
                        ISNULL(SUM(taxAmount),      0) AS tax,
                        ISNULL(SUM(discountAmount), 0) AS discounts
                    FROM  sales_orders
                    WHERE date >= ? AND date <= ?
                    GROUP BY [{group_col}]
                    {order_clause}
                """
                _, rows = run_report_query(cfg, company_id, detail_sql, (start_date, end_date))

            elif group_mode == "product":
                detail_sql = """
                    SELECT
                        ISNULL(i.productName, '(none)')   AS group_label,
                        COUNT(DISTINCT i.salesOrderId)    AS order_count,
                        ISNULL(SUM(i.quantity),       0)  AS qty_sold,
                        ISNULL(SUM(i.totalAmount),    0)  AS revenue,
                        ISNULL(AVG(i.sellingPrice),   0)  AS avg_price
                    FROM  sales_order_items i
                    JOIN  sales_orders o ON o.salesOrderId = i.salesOrderId
                    WHERE o.date >= ? AND o.date <= ?
                    GROUP BY i.productName
                    ORDER BY revenue DESC
                """
                _, rows = run_report_query(cfg, company_id, detail_sql, (start_date, end_date))

            elif group_mode == "payment":
                detail_sql = """
                    SELECT
                        ISNULL(p.paymentMethod, '(none)') AS group_label,
                        COUNT(*)                          AS transaction_count,
                        COUNT(DISTINCT p.salesOrderId)    AS order_count,
                        ISNULL(SUM(p.amount), 0)          AS total_amount
                    FROM  sales_payments p
                    JOIN  sales_orders o ON o.salesOrderId = p.salesOrderId
                    WHERE o.date >= ? AND o.date <= ?
                    GROUP BY p.paymentMethod
                    ORDER BY total_amount DESC
                """
                _, rows = run_report_query(cfg, company_id, detail_sql, (start_date, end_date))

        except Exception as e:
            error = str(e)

    return render_template(
        "report_sales.html",
        companies=companies,
        company_id=company_id,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
        group_label=group_label,
        group_mode=group_mode,
        summary=summary,
        rows=rows,
        error=error,
    )


@app.route("/reports/sell-through", methods=["GET"])
def report_sell_through():
    cfg       = load_config()
    companies = get_companies(cfg)

    company_id = request.args.get("company_id", "")
    start_date = request.args.get("start_date", "")
    end_date   = request.args.get("end_date", "")
    group_by   = request.args.get("group_by", "department")

    VALID_GROUPS = {
        "department":      ("department",          "Department"),
        "category":        ("categoryName",        "Category"),
        "subcategory":     ("subcategoryName",     "Subcategory"),
        "quality":         ("qualityDescr",        "Quality"),
        "condition":       ("conditionDescr",      "Condition"),
        "producingstore":  ("productionStoreName", "Production Store"),
        "processorname":   ("processorName",       "Processor"),
        "productioncycle": ("productionCycle",     "Production Cycle"),
    }
    group_col, group_label = VALID_GROUPS.get(group_by, ("department", "Department"))

    summary, rows, error = None, [], None

    if company_id and start_date and end_date:
        try:
            # Summary — overall sell-through metrics for the production date range.
            # RCS items are joined to TPM sales via department+category = tpmProductName.
            summary_sql = """
                WITH rcs AS (
                    SELECT
                        r.department + ' - ' + r.categoryName AS category,
                        ISNULL(SUM(r.qtyProduced), 0)          AS qty_produced
                    FROM rcs_items r
                    WHERE CAST(r.lastUpdatedDt AS DATE) >= ?
                      AND CAST(r.lastUpdatedDt AS DATE) <= ?
                    GROUP BY r.department + ' - ' + r.categoryName
                ),
                sales AS (
                    SELECT
                        soi.tpmProductName   AS category,
                        SUM(soi.quantity)    AS qty_sold,
                        SUM(soi.totalAmount) AS revenue
                    FROM sales_order_items soi
                    JOIN sales_orders o ON o.salesOrderId = soi.salesOrderId
                    WHERE o.date >= ?
                      AND o.date <= ?
                      AND soi.tpmProductId IS NOT NULL
                    GROUP BY soi.tpmProductName
                )
                SELECT
                    SUM(r.qty_produced)        AS total_qty_produced,
                    ISNULL(SUM(s.qty_sold), 0) AS total_qty_sold,
                    SUM(r.qty_produced)        AS dup_qty_produced,
                    ISNULL(SUM(s.qty_sold), 0) AS dup_qty_sold,
                    ISNULL(SUM(s.revenue), 0)  AS total_revenue,
                    NULL                       AS avg_days_to_sell,
                    NULL                       AS min_days_to_sell,
                    NULL                       AS max_days_to_sell
                FROM rcs r
                LEFT JOIN sales s ON s.category = r.category
            """
            _, sum_rows = run_report_query(cfg, company_id, summary_sql,
                                           (start_date, end_date, start_date, end_date))
            if sum_rows:
                summary = sum_rows[0]

            # Detail — grouped breakdown.
            # Sales are pro-rated within each category by qty_produced share to avoid
            # double-counting when grouping by quality, condition, processor, etc.
            detail_sql = f"""
                WITH rcs_detail AS (
                    SELECT
                        ISNULL(CAST(r.[{group_col}] AS NVARCHAR(255)), '(none)') AS group_val,
                        r.department + ' - ' + r.categoryName                     AS category,
                        ISNULL(SUM(r.qtyProduced), 0)                             AS qty_produced
                    FROM rcs_items r
                    WHERE CAST(r.lastUpdatedDt AS DATE) >= ?
                      AND CAST(r.lastUpdatedDt AS DATE) <= ?
                    GROUP BY r.[{group_col}], r.department + ' - ' + r.categoryName
                ),
                cat_totals AS (
                    SELECT category, SUM(qty_produced) AS cat_qty_total
                    FROM rcs_detail
                    GROUP BY category
                ),
                sales AS (
                    SELECT
                        soi.tpmProductName   AS category,
                        SUM(soi.quantity)    AS qty_sold,
                        SUM(soi.totalAmount) AS revenue
                    FROM sales_order_items soi
                    JOIN sales_orders o ON o.salesOrderId = soi.salesOrderId
                    WHERE o.date >= ?
                      AND o.date <= ?
                      AND soi.tpmProductId IS NOT NULL
                    GROUP BY soi.tpmProductName
                ),
                allocated AS (
                    SELECT
                        r.group_val,
                        r.qty_produced,
                        ISNULL(s.qty_sold * CAST(r.qty_produced AS FLOAT)
                               / NULLIF(ct.cat_qty_total, 0), 0) AS qty_sold_alloc,
                        ISNULL(s.revenue  * CAST(r.qty_produced AS FLOAT)
                               / NULLIF(ct.cat_qty_total, 0), 0) AS revenue_alloc
                    FROM rcs_detail r
                    JOIN cat_totals ct ON ct.category = r.category
                    LEFT JOIN sales s   ON s.category  = r.category
                )
                SELECT
                    group_val                                                              AS group_label,
                    SUM(qty_produced)                                                      AS items_produced,
                    CAST(ROUND(SUM(qty_sold_alloc), 0) AS INT)                           AS items_sold,
                    SUM(qty_produced)                                                      AS total_qty_produced,
                    CAST(ROUND(SUM(qty_sold_alloc), 0) AS INT)                           AS qty_sold,
                    ISNULL(CAST(
                        SUM(qty_sold_alloc) / NULLIF(CAST(SUM(qty_produced) AS FLOAT), 0) * 100
                    AS DECIMAL(5,1)), 0.0)                                                 AS sell_thru_pct,
                    NULL                                                                   AS avg_days_to_sell,
                    ISNULL(CAST(SUM(revenue_alloc) AS DECIMAL(12, 2)), 0)               AS revenue
                FROM allocated
                GROUP BY group_val
                ORDER BY revenue DESC
            """
            _, rows = run_report_query(cfg, company_id, detail_sql,
                                       (start_date, end_date, start_date, end_date))

        except Exception as e:
            error = str(e)

    return render_template(
        "report_sell_through.html",
        companies=companies,
        company_id=company_id,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
        group_label=group_label,
        summary=summary,
        rows=rows,
        error=error,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.environ.get("BRIJJ_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIJJ_PORT", 5000))
    print(f"BrijjData Config UI running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
