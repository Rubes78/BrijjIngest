# BrijjIngest

A Python-based data ingestion service that pulls **RCS Items** and **Sales** data from the Brijj API suite into per-company SQL Server databases, with a Flask web UI for configuration and management.

---

## Features

- Pulls **RcsItems** and **Sales** (orders, items, payments) from the Brijj API
- **Per-company SQL Server databases** — each company gets its own database, created automatically
- **Date-range filtering** — defaults to today; specify any range via the UI or CLI
- **Bulk loading** via pyodbc `fast_executemany` — handles large datasets efficiently
- **Upsert on every run** — safe to re-run; no duplicate data ever created
- **Web UI** on port 5000 — manage database settings, API endpoints, and companies
- **Live ingest log** — real-time streaming output while an ingest is running
- **systemd service** — runs on boot, restartable at any time

---

## Requirements

- Debian 11/12 or Ubuntu 20.04/22.04/24.04
- Python 3.10+
- SQL Server 2017+ (tested on SQL Server 2022)
- Network access to `productionsystem-api.brijjworks.com`, `company-api.brijjworks.com`, `pos-api.brijjworks.com`

---

## Installation

```bash
git clone https://github.com/Rubes78/BrijjIngest.git
cd BrijjIngest
sudo ./install.sh
```

The installer will:
1. Install system packages (`python3-venv`, `unixodbc-dev`, etc.)
2. Install **Microsoft ODBC Driver 18 for SQL Server**
3. Create a Python virtual environment and install dependencies
4. Scaffold `config.ini` from the example template
5. Install and enable the `brijjdata` systemd service

### Optional environment overrides

```bash
sudo INSTALL_DIR=/srv/brijj BRIJJ_PORT=8080 ./install.sh
```

| Variable | Default | Description |
|---|---|---|
| `INSTALL_DIR` | `/opt/brijjingest` | Installation directory |
| `BRIJJ_PORT` | `5000` | Web UI port |
| `BRIJJ_HOST` | `0.0.0.0` | Bind address |

---

## Configuration

After installing, open the web UI and configure:

1. **Database** — SQL Server host, port, login credentials
2. **API Endpoints** — Brijj API base URLs and page size (defaults are correct for production)
3. **Companies** — Add one entry per Brijj company with its API credentials and target database name

Or edit `config.ini` directly:

```ini
[database]
server = YOUR_SQL_SERVER_HOST
port   = 1433
user   = sa
password = YOUR_PASSWORD

[apis]
rcs_base        = https://productionsystem-api.brijjworks.com
auth_base       = https://company-api.brijjworks.com/api
sales_base      = https://pos-api.brijjworks.com/api
sales_page_size = 1000

[company:EXAMPLE]
company_id     = EXAMPLE
company_int_id = 123
username       = YourApiUsername
password       = YourApiPassword
db_name        = BrijjData_EXAMPLE
enabled        = true
```

> **Note:** `config.ini` is gitignored and will never be committed. Use `config.ini.example` as a reference.

---

## Running an Ingest

### Via the Web UI

1. Go to **Companies**
2. Click **Ingest** next to a company
3. Select a start and end date (defaults to today)
4. Click **Run** — live output streams to the page

### Via the CLI

```bash
# Today only (default)
venv/bin/python ingest_brijj.py --company VD001

# Specific date range
venv/bin/python ingest_brijj.py --company VD001 --start-date 2026-01-01 --end-date 2026-01-31

# All enabled companies
venv/bin/python ingest_brijj.py --start-date 2026-02-01 --end-date 2026-02-24
```

---

## Database Schema

Each company database contains four tables:

| Table | Key | Description |
|---|---|---|
| `rcs_items` | `barcode + companyID` | Current item/inventory snapshot from RCS |
| `sales_orders` | `salesOrderId` | POS sales order headers |
| `sales_order_items` | `salesOrderItemId` | Line items for each order |
| `sales_payments` | `id` | Payment records for each order |

All tables use `MERGE` (upsert) — running the same date range multiple times updates records in place, no duplicates are created.

---

## Service Management

```bash
sudo systemctl start   brijjdata
sudo systemctl stop    brijjdata
sudo systemctl restart brijjdata
sudo systemctl status  brijjdata

# View logs
journalctl -u brijjdata -f
```

---

## Project Structure

```
BrijjIngest/
├── ingest_brijj.py       # Core ingestion script
├── web_config.py         # Flask web UI
├── templates/            # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── database.html
│   ├── apis.html
│   ├── companies.html
│   ├── company_form.html
│   └── ingest_log.html
├── install.sh            # Portable installer
├── requirements.txt      # Python dependencies
├── brijjdata.service     # systemd service definition
├── start.sh              # Quick start helper
├── stop.sh               # Quick stop helper
├── install_service.sh    # Manual service install helper
├── config.ini.example    # Configuration template
└── config.ini            # Live config — gitignored, never committed
```
