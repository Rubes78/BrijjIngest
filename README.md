# BrijjIngest

A Python-based data ingestion service that pulls **RCS Items** and **Sales** data from the Brijj API suite into per-company SQL Server databases, with a Flask web UI for configuration and management.

---

## Features

- Pulls **RcsItems** and **Sales** (orders, items, payments) from the Brijj API
- **Per-company SQL Server databases** — each company gets its own database, created automatically
- **Date-range filtering** — defaults to today; specify any range via the UI or CLI
- **Bulk loading** via pyodbc `fast_executemany` — handles large datasets efficiently
- **Upsert on every run** — safe to re-run; no duplicate data ever created
- **Web UI** on port 5001 — manage database settings, API endpoints, and companies
- **Live ingest log** — real-time streaming output while an ingest is running
- Runs as a **Windows service** via WinSW, starts automatically on boot

---

## Requirements

- Windows 10/11 or Windows Server 2019/2022
- Python 3.10+ (installed automatically if missing)
- SQL Server 2017+
- Microsoft ODBC Driver 18 for SQL Server (installed automatically)

---

## Installation

Clone the repo or download and extract the ZIP from GitHub, then **right-click `install.bat` and choose Run as administrator**:

```cmd
install.bat
```

The installer will:
1. Verify or install Python 3.10+ via winget
2. Install Microsoft ODBC Driver 18 for SQL Server
3. Create a Python virtual environment and install dependencies
4. Copy `config.ini.example` to `config.ini` (only on first install)
5. Install the `brijjdata` Windows service via WinSW
6. Open a Windows Firewall rule for port 5001
7. Start the service and open the web UI in your browser

To install to a different directory or port, run the PowerShell script directly from an Administrator PowerShell prompt:

```powershell
.\install.ps1 -InstallDir "D:\Apps\BrijjIngest" -Port 8080
```

| Parameter | Default | Description |
|---|---|---|
| `-InstallDir` | `C:\BrijjIngest` | Installation directory |
| `-Port` | `5001` | Web UI port |
| `-BindHost` | `0.0.0.0` | Bind address |

---

## Configuration

After the browser opens, configure:

1. **Database** — SQL Server host, port, and login credentials
2. **API Endpoints** — Brijj API base URLs and page size (defaults are correct for production)
3. **Companies** — Add one entry per Brijj company with its API credentials and target database name

Or edit `C:\BrijjIngest\config.ini` directly:

```ini
[database]
server   = YOUR_SQL_SERVER_HOST
port     = 1433
user     = sa
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

```cmd
venv\Scripts\python.exe ingest_brijj.py --company VD001

venv\Scripts\python.exe ingest_brijj.py --company VD001 --start-date 2026-01-01 --end-date 2026-01-31

venv\Scripts\python.exe ingest_brijj.py --start-date 2026-02-01 --end-date 2026-02-24
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

All tables use `MERGE` (upsert) — running the same date range multiple times updates records in place; no duplicates are ever created.

---

## Service Management

```cmd
net start brijjdata
net stop  brijjdata

:: Or use the helper scripts in the install directory:
start.bat
stop.bat

:: View logs:
type C:\BrijjIngest\logs\brijjdata.out.log
```

The service is set to start automatically on boot. It can also be managed from **Services** (`services.msc`).

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
├── install.bat           # Windows installer launcher (run as Administrator)
├── install.ps1           # Windows installer (PowerShell)
├── requirements.txt      # Python dependencies
├── config.ini.example    # Configuration template
└── config.ini            # Live config — gitignored, never committed
```
