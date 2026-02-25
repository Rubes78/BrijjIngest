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
import datetime
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, flash, Response, stream_with_context, jsonify

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
            # RCS items join to TPM sales via department+category = tpmProductName.
            # Avg days to sell uses FIFO: cumulative production slots matched to
            # cumulative sales slots ordered by date; overlap qty weighted average.
            summary_sql = """
                WITH prod_by_date AS (
                    SELECT
                        r.department + ' - ' + r.categoryName AS category,
                        CAST(r.lastUpdatedDt AS DATE)          AS prod_date,
                        SUM(r.qtyProduced)                     AS batch_qty
                    FROM rcs_items r
                    WHERE CAST(r.lastUpdatedDt AS DATE) >= ?
                      AND CAST(r.lastUpdatedDt AS DATE) <= ?
                    GROUP BY r.department + ' - ' + r.categoryName,
                             CAST(r.lastUpdatedDt AS DATE)
                ),
                prod_cum AS (
                    SELECT
                        category, prod_date, batch_qty,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY prod_date
                                             ROWS UNBOUNDED PRECEDING) AS cum_end,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY prod_date
                                             ROWS UNBOUNDED PRECEDING) - batch_qty AS cum_start
                    FROM prod_by_date
                ),
                prod_totals AS (
                    SELECT category, SUM(batch_qty) AS qty_produced
                    FROM prod_by_date GROUP BY category
                ),
                sold_by_date AS (
                    -- Normalise tpmProductName to "Dept - Cat" for matching:
                    -- cycle-tagged names ("ORANGE Dept - Cat_timestamp") have the
                    -- timestamp and leading colour word stripped; plain names are used as-is.
                    SELECT
                        CASE WHEN CHARINDEX('_', soi.tpmProductName) > 0
                             THEN SUBSTRING(
                                      LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1),
                                      CHARINDEX(' ', LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1)) + 1,
                                      LEN(LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1))
                                  )
                             ELSE soi.tpmProductName
                        END                  AS category,
                        o.date               AS sale_date,
                        SUM(soi.quantity)    AS batch_qty,
                        SUM(soi.totalAmount) AS batch_revenue
                    FROM sales_order_items soi
                    JOIN sales_orders o ON o.salesOrderId = soi.salesOrderId
                    WHERE o.date >= ?
                      AND o.date <= ?
                      AND soi.tpmProductId IS NOT NULL
                    GROUP BY
                        CASE WHEN CHARINDEX('_', soi.tpmProductName) > 0
                             THEN SUBSTRING(
                                      LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1),
                                      CHARINDEX(' ', LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1)) + 1,
                                      LEN(LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1))
                                  )
                             ELSE soi.tpmProductName
                        END,
                        o.date
                ),
                sold_cum AS (
                    SELECT
                        category, sale_date, batch_qty, batch_revenue,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY sale_date
                                             ROWS UNBOUNDED PRECEDING) AS cum_end,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY sale_date
                                             ROWS UNBOUNDED PRECEDING) - batch_qty AS cum_start
                    FROM sold_by_date
                ),
                sold_totals AS (
                    SELECT category,
                           SUM(batch_qty)      AS qty_sold,
                           SUM(batch_revenue)  AS revenue
                    FROM sold_by_date GROUP BY category
                ),
                fifo AS (
                    SELECT
                        p.category,
                        DATEDIFF(day, p.prod_date, s.sale_date) AS days,
                        (CASE WHEN p.cum_end < s.cum_end THEN p.cum_end ELSE s.cum_end END)
                        - (CASE WHEN p.cum_start > s.cum_start THEN p.cum_start ELSE s.cum_start END)
                            AS overlap_qty
                    FROM prod_cum p
                    JOIN sold_cum s ON s.category = p.category
                    WHERE p.cum_end > s.cum_start AND p.cum_start < s.cum_end
                      AND s.sale_date >= p.prod_date   -- exclude pre-range inventory matches
                ),
                fifo_avg AS (
                    SELECT
                        category,
                        CAST(SUM(CAST(days AS FLOAT) * overlap_qty)
                             / NULLIF(SUM(overlap_qty), 0) AS DECIMAL(5,1)) AS avg_days,
                        MIN(days) AS min_days,
                        MAX(days) AS max_days
                    FROM fifo GROUP BY category
                )
                SELECT
                    SUM(pt.qty_produced)                                                   AS total_qty_produced,
                    ISNULL(SUM(st.qty_sold), 0)                                            AS total_qty_sold,
                    SUM(pt.qty_produced)                                                   AS dup1,
                    ISNULL(SUM(st.qty_sold), 0)                                            AS dup2,
                    ISNULL(SUM(st.revenue), 0)                                             AS total_revenue,
                    CAST(
                        SUM(ISNULL(f.avg_days, 0) * ISNULL(st.qty_sold, 0)) /
                        NULLIF(SUM(ISNULL(st.qty_sold, 0)), 0)
                    AS DECIMAL(5,1))                                                        AS avg_days_to_sell,
                    MIN(f.min_days)                                                         AS min_days_to_sell,
                    MAX(f.max_days)                                                         AS max_days_to_sell
                FROM prod_totals pt
                LEFT JOIN sold_totals st ON st.category = pt.category
                LEFT JOIN fifo_avg f     ON f.category  = pt.category
            """
            _, sum_rows = run_report_query(cfg, company_id, summary_sql,
                                           (start_date, end_date, start_date, end_date))
            if sum_rows:
                summary = sum_rows[0]

            # Detail — grouped breakdown.
            # Sales and avg-days are pro-rated within each category by qty_produced
            # share to avoid double-counting when grouping by quality, condition, etc.
            # Avg days uses the same FIFO logic, weighted by pro-rated qty_sold.
            detail_sql = f"""
                WITH prod_by_date AS (
                    SELECT
                        ISNULL(CAST(r.[{group_col}] AS NVARCHAR(255)), '(none)') AS group_val,
                        r.department + ' - ' + r.categoryName                     AS category,
                        CAST(r.lastUpdatedDt AS DATE)                             AS prod_date,
                        SUM(r.qtyProduced)                                        AS batch_qty
                    FROM rcs_items r
                    WHERE CAST(r.lastUpdatedDt AS DATE) >= ?
                      AND CAST(r.lastUpdatedDt AS DATE) <= ?
                    GROUP BY r.[{group_col}],
                             r.department + ' - ' + r.categoryName,
                             CAST(r.lastUpdatedDt AS DATE)
                ),
                rcs_detail AS (
                    SELECT group_val, category, SUM(batch_qty) AS qty_produced
                    FROM prod_by_date GROUP BY group_val, category
                ),
                cat_totals AS (
                    SELECT category, SUM(qty_produced) AS cat_qty_total
                    FROM rcs_detail GROUP BY category
                ),
                prod_cum AS (
                    SELECT
                        category, prod_date, batch_qty,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY prod_date
                                             ROWS UNBOUNDED PRECEDING) AS cum_end,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY prod_date
                                             ROWS UNBOUNDED PRECEDING) - batch_qty AS cum_start
                    FROM (
                        SELECT category, prod_date, SUM(batch_qty) AS batch_qty
                        FROM prod_by_date GROUP BY category, prod_date
                    ) cat_date
                ),
                sold_by_date AS (
                    SELECT
                        CASE WHEN CHARINDEX('_', soi.tpmProductName) > 0
                             THEN SUBSTRING(
                                      LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1),
                                      CHARINDEX(' ', LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1)) + 1,
                                      LEN(LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1))
                                  )
                             ELSE soi.tpmProductName
                        END                  AS category,
                        o.date               AS sale_date,
                        SUM(soi.quantity)    AS batch_qty,
                        SUM(soi.totalAmount) AS batch_revenue
                    FROM sales_order_items soi
                    JOIN sales_orders o ON o.salesOrderId = soi.salesOrderId
                    WHERE o.date >= ?
                      AND o.date <= ?
                      AND soi.tpmProductId IS NOT NULL
                    GROUP BY
                        CASE WHEN CHARINDEX('_', soi.tpmProductName) > 0
                             THEN SUBSTRING(
                                      LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1),
                                      CHARINDEX(' ', LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1)) + 1,
                                      LEN(LEFT(soi.tpmProductName, CHARINDEX('_', soi.tpmProductName) - 1))
                                  )
                             ELSE soi.tpmProductName
                        END,
                        o.date
                ),
                sold_cum AS (
                    SELECT
                        category, sale_date, batch_qty, batch_revenue,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY sale_date
                                             ROWS UNBOUNDED PRECEDING) AS cum_end,
                        SUM(batch_qty) OVER (PARTITION BY category ORDER BY sale_date
                                             ROWS UNBOUNDED PRECEDING) - batch_qty AS cum_start
                    FROM sold_by_date
                ),
                sold_totals AS (
                    SELECT category, SUM(batch_qty) AS qty_sold, SUM(batch_revenue) AS revenue
                    FROM sold_by_date GROUP BY category
                ),
                fifo AS (
                    SELECT
                        p.category,
                        DATEDIFF(day, p.prod_date, s.sale_date) AS days,
                        (CASE WHEN p.cum_end < s.cum_end THEN p.cum_end ELSE s.cum_end END)
                        - (CASE WHEN p.cum_start > s.cum_start THEN p.cum_start ELSE s.cum_start END)
                            AS overlap_qty
                    FROM prod_cum p
                    JOIN sold_cum s ON s.category = p.category
                    WHERE p.cum_end > s.cum_start AND p.cum_start < s.cum_end
                      AND s.sale_date >= p.prod_date   -- exclude pre-range inventory matches
                ),
                fifo_avg AS (
                    SELECT
                        category,
                        CAST(SUM(CAST(days AS FLOAT) * overlap_qty)
                             / NULLIF(SUM(overlap_qty), 0) AS DECIMAL(5,1)) AS avg_days
                    FROM fifo GROUP BY category
                ),
                allocated AS (
                    SELECT
                        r.group_val,
                        r.qty_produced,
                        ISNULL(st.qty_sold * CAST(r.qty_produced AS FLOAT)
                               / NULLIF(ct.cat_qty_total, 0), 0)   AS qty_sold_alloc,
                        ISNULL(st.revenue  * CAST(r.qty_produced AS FLOAT)
                               / NULLIF(ct.cat_qty_total, 0), 0)   AS revenue_alloc,
                        f.avg_days                                   AS cat_avg_days
                    FROM rcs_detail r
                    JOIN cat_totals ct    ON ct.category = r.category
                    LEFT JOIN sold_totals st ON st.category = r.category
                    LEFT JOIN fifo_avg f      ON f.category  = r.category
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
                    CAST(
                        SUM(ISNULL(cat_avg_days, 0) * qty_sold_alloc) /
                        NULLIF(SUM(qty_sold_alloc), 0)
                    AS DECIMAL(5,1))                                                       AS avg_days_to_sell,
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


CUSTOMER_SORT_COLS = {
    "name":       "c.lastName {dir}, c.firstName {dir}",
    "email":      "c.email {dir}",
    "phone":      "c.phoneNumber {dir}",
    "loyalty":    "c.loyaltyPoints {dir}",
    "credit":     "c.storeCredit {dir}",
    "onaccount":  "c.onAccountBalance {dir}",
    "since":      "c.createdAt {dir}",
    "groups":     "STRING_AGG(cg.groupName, ', ') {dir}",
}


@app.route("/reports/customers", methods=["GET"])
def report_customers():
    cfg       = load_config()
    companies = get_companies(cfg)

    company_id   = request.args.get("company_id", "")
    search       = request.args.get("search", "").strip()
    group_filter = request.args.get("group_filter", "").strip()
    customer_id  = request.args.get("customer_id", "").strip()
    sort_by      = request.args.get("sort_by", "name")
    sort_dir     = "DESC" if request.args.get("sort_dir", "asc").lower() == "desc" else "ASC"
    page         = max(1, int(request.args.get("page", 1) or 1))
    page_size    = 50

    if sort_by not in CUSTOMER_SORT_COLS:
        sort_by = "name"

    sort_expr    = CUSTOMER_SORT_COLS[sort_by].replace("{dir}", sort_dir)
    rows, total_count, available_groups, error = [], 0, [], None

    # Query string fragments for building sort/page links in the template
    filter_qs = urlencode({k: v for k, v in {
        "company_id":   company_id,
        "search":       search,
        "group_filter": group_filter,
        "customer_id":  customer_id,
    }.items() if v})
    sort_qs = urlencode({k: v for k, v in {
        "company_id":   company_id,
        "search":       search,
        "group_filter": group_filter,
        "customer_id":  customer_id,
        "sort_by":      sort_by,
        "sort_dir":     sort_dir.lower(),
    }.items() if v})

    if company_id:
        try:
            like         = f"%{search}%" if search else "%"
            offset       = (page - 1) * page_size
            group_params = (group_filter,) if group_filter else ()

            # Available groups for the filter dropdown
            _, grp_rows = run_report_query(
                cfg, company_id,
                "SELECT DISTINCT groupName FROM customer_groups WHERE groupName IS NOT NULL ORDER BY groupName",
                ()
            )
            available_groups = [r[0] for r in grp_rows]

            group_join  = "JOIN customer_groups cg2 ON cg2.customerId = c.id AND cg2.groupName = ?" if group_filter else ""
            id_where    = "AND c.id = ?" if customer_id else ""
            id_param    = (int(customer_id),) if customer_id else ()
            search_where = """(
                    ? = ''
                    OR ISNULL(c.firstName,'') + ' ' + ISNULL(c.lastName,'') LIKE ?
                    OR ISNULL(c.email,'')       LIKE ?
                    OR ISNULL(c.phoneNumber,'') LIKE ?
                )"""

            count_sql = f"""
                SELECT COUNT(DISTINCT c.id)
                FROM customers c
                {group_join}
                WHERE {search_where} {id_where}
            """
            _, cnt_rows = run_report_query(cfg, company_id, count_sql,
                                           group_params + (search, like, like, like) + id_param)
            total_count = cnt_rows[0][0] if cnt_rows else 0

            list_sql = f"""
                SELECT
                    c.id,
                    ISNULL(c.firstName,'')          AS firstName,
                    ISNULL(c.lastName,'')           AS lastName,
                    ISNULL(c.email,'')              AS email,
                    ISNULL(c.phoneNumber,'')        AS phoneNumber,
                    ISNULL(c.loyaltyPoints, 0)      AS loyaltyPoints,
                    ISNULL(c.storeCredit, 0)        AS storeCredit,
                    ISNULL(c.onAccountBalance, 0)   AS onAccountBalance,
                    CONVERT(NVARCHAR(10), c.createdAt, 23) AS memberSince,
                    c.enableLoyaltyProgram,
                    c.enableTaxExemption,
                    c.allowToSendPromotionalEmails,
                    ISNULL(c.addressLine1,'')       AS addressLine1,
                    ISNULL(c.addressLine2,'')       AS addressLine2,
                    ISNULL(c.addressCity,'')        AS addressCity,
                    ISNULL(c.addressStateName,'')   AS addressStateName,
                    ISNULL(c.addressZipCode,'')     AS addressZipCode,
                    ISNULL(c.addressCountryName,'') AS addressCountryName,
                    ISNULL(c.externalId,'')         AS externalId,
                    ISNULL(STRING_AGG(cg.groupName, ', '), '') AS groups
                FROM customers c
                {group_join}
                LEFT JOIN customer_groups cg ON cg.customerId = c.id
                WHERE {search_where} {id_where}
                GROUP BY
                    c.id, c.firstName, c.lastName, c.email, c.phoneNumber,
                    c.loyaltyPoints, c.storeCredit, c.onAccountBalance, c.createdAt,
                    c.enableLoyaltyProgram, c.enableTaxExemption,
                    c.allowToSendPromotionalEmails,
                    c.addressLine1, c.addressLine2, c.addressCity, c.addressStateName,
                    c.addressZipCode, c.addressCountryName, c.externalId
                ORDER BY {sort_expr}, c.id
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """
            _, rows = run_report_query(cfg, company_id, list_sql,
                                       group_params + (search, like, like, like) + id_param + (offset, page_size))
        except Exception as e:
            error = str(e)

    total_pages = max(1, -(-total_count // page_size))  # ceiling division

    return render_template(
        "report_customers.html",
        companies=companies,
        company_id=company_id,
        search=search,
        group_filter=group_filter,
        customer_id=customer_id,
        available_groups=available_groups,
        sort_by=sort_by,
        sort_dir=sort_dir.lower(),
        filter_qs=filter_qs,
        sort_qs=sort_qs,
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
        rows=rows,
        error=error,
    )


SALES_HISTORY_SORT_COLS = {
    "date":     "o.date {dir}",
    "customer": "o.customerLastName {dir}, o.customerFirstName {dir}",
    "cashier":  "o.userName {dir}",
    "total":    "o.totalAmount {dir}",
    "status":   "o.paymentStatus {dir}",
    "items":    "ic.itemCount {dir}",
}


@app.route("/reports/sales-history", methods=["GET"])
def report_sales_history():
    cfg       = load_config()
    companies = get_companies(cfg)

    today      = datetime.date.today().isoformat()
    month_ago  = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

    company_id     = request.args.get("company_id", "")
    start_date     = request.args.get("start_date", month_ago)
    end_date       = request.args.get("end_date",   today)
    sales_type     = request.args.get("sales_type",     "").strip()
    payment_status = request.args.get("payment_status", "").strip()
    search         = request.args.get("search", "").strip()
    customer_id    = request.args.get("customer_id", "").strip()
    sort_by        = request.args.get("sort_by", "date")
    sort_dir       = "DESC" if request.args.get("sort_dir", "desc").lower() == "desc" else "ASC"
    page           = max(1, int(request.args.get("page", 1) or 1))
    page_size      = 50

    if sort_by not in SALES_HISTORY_SORT_COLS:
        sort_by = "date"

    sort_expr = SALES_HISTORY_SORT_COLS[sort_by].replace("{dir}", sort_dir)
    rows, total_count, available_sales_types, available_payment_statuses, error = [], 0, [], [], None

    filter_qs = urlencode({k: v for k, v in {
        "company_id":     company_id,
        "start_date":     start_date,
        "end_date":       end_date,
        "sales_type":     sales_type,
        "payment_status": payment_status,
        "search":         search,
        "customer_id":    customer_id,
    }.items() if v})
    filter_qs_no_cust = urlencode({k: v for k, v in {
        "company_id":     company_id,
        "start_date":     start_date,
        "end_date":       end_date,
        "sales_type":     sales_type,
        "payment_status": payment_status,
        "search":         search,
    }.items() if v})
    sort_qs = urlencode({k: v for k, v in {
        "company_id":     company_id,
        "start_date":     start_date,
        "end_date":       end_date,
        "sales_type":     sales_type,
        "payment_status": payment_status,
        "search":         search,
        "customer_id":    customer_id,
        "sort_by":        sort_by,
        "sort_dir":       sort_dir.lower(),
    }.items() if v})

    if company_id:
        try:
            like = f"%{search}%" if search else "%"

            # Filter dropdowns
            _, st_rows = run_report_query(cfg, company_id,
                "SELECT DISTINCT salesType FROM sales_orders WHERE salesType IS NOT NULL AND salesType != '' ORDER BY salesType", ())
            available_sales_types = [r[0] for r in st_rows]

            _, ps_rows = run_report_query(cfg, company_id,
                "SELECT DISTINCT paymentStatus FROM sales_orders WHERE paymentStatus IS NOT NULL AND paymentStatus != '' ORDER BY paymentStatus", ())
            available_payment_statuses = [r[0] for r in ps_rows]

            # Build WHERE dynamically
            where_parts, where_params = [], []
            if start_date:
                where_parts.append("o.date >= ?")
                where_params.append(start_date)
            if end_date:
                where_parts.append("o.date <= ?")
                where_params.append(end_date)
            if sales_type:
                where_parts.append("o.salesType = ?")
                where_params.append(sales_type)
            if payment_status:
                where_parts.append("o.paymentStatus = ?")
                where_params.append(payment_status)
            if search:
                where_parts.append("""(
                    ISNULL(o.referenceNumber,'') LIKE ?
                    OR ISNULL(o.customerFirstName,'') + ' ' + ISNULL(o.customerLastName,'') LIKE ?
                )""")
                where_params.extend([like, like])
            if customer_id:
                where_parts.append("o.customerId = ?")
                where_params.append(int(customer_id))

            where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

            count_sql = f"SELECT COUNT(*) FROM sales_orders o {where_sql}"
            _, cnt_rows = run_report_query(cfg, company_id, count_sql, tuple(where_params))
            total_count = cnt_rows[0][0] if cnt_rows else 0

            list_sql = f"""
                SELECT
                    o.salesOrderId,
                    CONVERT(NVARCHAR(10), o.date, 23)             AS saleDate,
                    ISNULL(o.referenceNumber, '')                  AS referenceNumber,
                    ISNULL(o.salesType, '')                        AS salesType,
                    ISNULL(o.paymentStatus, '')                    AS paymentStatus,
                    ISNULL(o.returnType, '')                       AS returnType,
                    ISNULL(o.totalAmount, 0)                       AS totalAmount,
                    o.customerId,
                    ISNULL(o.customerFirstName + ' ' + o.customerLastName, '') AS customerName,
                    ISNULL(o.storeName, '')                        AS storeName,
                    ISNULL(o.userName, '')                         AS cashier,
                    ic.itemCount,
                    ISNULL(pm.paymentMethods, '')                  AS paymentMethods
                FROM sales_orders o
                OUTER APPLY (
                    SELECT COUNT(*) AS itemCount
                    FROM sales_order_items
                    WHERE salesOrderId = o.salesOrderId
                ) ic
                OUTER APPLY (
                    SELECT STRING_AGG(paymentMethod, ', ') AS paymentMethods
                    FROM (
                        SELECT DISTINCT paymentMethod
                        FROM sales_payments
                        WHERE salesOrderId = o.salesOrderId AND paymentStatus = 'Success'
                    ) pd
                ) pm
                {where_sql}
                ORDER BY {sort_expr}, o.salesOrderId DESC
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """
            _, rows = run_report_query(cfg, company_id, list_sql,
                                       tuple(where_params) + (((page - 1) * page_size), page_size))
        except Exception as e:
            error = str(e)

    total_pages = max(1, -(-total_count // page_size))

    return render_template(
        "report_sales_history.html",
        companies=companies,
        company_id=company_id,
        start_date=start_date,
        end_date=end_date,
        sales_type=sales_type,
        payment_status=payment_status,
        available_sales_types=available_sales_types,
        available_payment_statuses=available_payment_statuses,
        search=search,
        customer_id=customer_id,
        sort_by=sort_by,
        sort_dir=sort_dir.lower(),
        filter_qs=filter_qs,
        filter_qs_no_cust=filter_qs_no_cust,
        sort_qs=sort_qs,
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
        rows=rows,
        error=error,
    )


@app.route("/api/order-detail")
def api_order_detail():
    cfg        = load_config()
    company_id = request.args.get("company_id", "")
    order_id   = request.args.get("order_id",   "")

    if not company_id or not order_id:
        return jsonify({"error": "Missing params"}), 400

    try:
        _, o_rows = run_report_query(cfg, company_id, """
            SELECT salesOrderId,
                   CONVERT(NVARCHAR(10), date, 23),
                   ISNULL(referenceNumber,''),
                   ISNULL(salesType,''), ISNULL(paymentStatus,''),
                   ISNULL(returnType,''), ISNULL(checkoutType,''),
                   ISNULL(totalAmount,0), ISNULL(discountAmount,0),
                   ISNULL(taxAmount,0),  ISNULL(roundupAmount,0),
                   ISNULL(orderTotalBeforeDiscount,0),
                   ISNULL(totalDiscountPercentageOnOrder,0),
                   customerId,
                   ISNULL(customerFirstName,''), ISNULL(customerLastName,''),
                   ISNULL(customerLoyaltyPoints,0), ISNULL(customerStoreCredit,0),
                   ISNULL(storeName,''), ISNULL(registerName,''), ISNULL(userName,''),
                   ISNULL(loyaltyPoints,0), ISNULL(note,''),
                   CONVERT(NVARCHAR(19), createdAt, 120)
            FROM sales_orders WHERE salesOrderId = ?
        """, (order_id,))

        if not o_rows:
            return jsonify({"error": "Order not found"}), 404

        r = o_rows[0]
        order = {
            "salesOrderId": r[0],  "date": r[1],
            "referenceNumber": r[2],
            "salesType": r[3],     "paymentStatus": r[4],
            "returnType": r[5],    "checkoutType": r[6],
            "totalAmount": float(r[7]),       "discountAmount": float(r[8]),
            "taxAmount": float(r[9]),         "roundupAmount": float(r[10]),
            "orderTotalBeforeDiscount": float(r[11]),
            "totalDiscountPct": float(r[12]),
            "customerId": r[13],
            "customerFirstName": r[14],       "customerLastName": r[15],
            "customerLoyaltyPoints": float(r[16]), "customerStoreCredit": float(r[17]),
            "storeName": r[18],   "registerName": r[19],  "cashier": r[20],
            "loyaltyPoints": float(r[21]),    "note": r[22],
            "createdAt": r[23],
        }

        _, i_rows = run_report_query(cfg, company_id, """
            SELECT salesOrderItemId,
                   ISNULL(productName, ISNULL(tpmProductName,'')) AS itemName,
                   ISNULL(productSKU,''),
                   ISNULL(tpmProductName,''),
                   ISNULL(productCondition,''),
                   ISNULL(salesOrderItemType,''),
                   quantity, ISNULL(returnedQuantity,0),
                   ISNULL(sellingPrice,0), ISNULL(totalAmount,0),
                   ISNULL(discountAmount,0), ISNULL(taxAmount,0),
                   ISNULL(note,''),
                   CASE WHEN tpmProductId IS NOT NULL THEN 1 ELSE 0 END AS isTpm
            FROM sales_order_items
            WHERE salesOrderId = ?
            ORDER BY salesOrderItemId
        """, (order_id,))

        items = [{
            "id": r[0], "name": r[1], "sku": r[2], "tpmProductName": r[3],
            "condition": r[4], "itemType": r[5],
            "qty": r[6], "returnedQty": r[7],
            "price": float(r[8]), "total": float(r[9]),
            "discount": float(r[10]), "tax": float(r[11]),
            "note": r[12], "isTpm": bool(r[13]),
        } for r in i_rows]

        _, p_rows = run_report_query(cfg, company_id, """
            SELECT id, ISNULL(paymentMethod,''), ISNULL(paymentStatus,''),
                   ISNULL(currency,''), ISNULL(amount,0)
            FROM sales_payments WHERE salesOrderId = ? ORDER BY id
        """, (order_id,))

        payments = [{
            "id": r[0], "method": r[1], "status": r[2],
            "currency": r[3], "amount": float(r[4]),
        } for r in p_rows]

        customer = None
        if order["customerId"]:
            _, c_rows = run_report_query(cfg, company_id, """
                SELECT ISNULL(firstName,''), ISNULL(lastName,''),
                       ISNULL(email,''), ISNULL(phoneNumber,''),
                       ISNULL(loyaltyPoints,0), ISNULL(storeCredit,0), ISNULL(onAccountBalance,0)
                FROM customers WHERE id = ?
            """, (order["customerId"],))
            if c_rows:
                cr = c_rows[0]
                customer = {
                    "firstName": cr[0], "lastName": cr[1],
                    "email": cr[2], "phoneNumber": cr[3],
                    "loyaltyPoints": float(cr[4]), "storeCredit": float(cr[5]),
                    "onAccountBalance": float(cr[6]),
                }

        return jsonify({"order": order, "items": items, "payments": payments, "customer": customer})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.environ.get("BRIJJ_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIJJ_PORT", 5000))
    print(f"BrijjData Config UI running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
