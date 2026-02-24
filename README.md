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
- Runs as a **system service** on both Linux (systemd) and Windows (NSSM)

---

## Requirements

| | Linux | Windows |
|---|---|---|
| OS | Debian 11/12, Ubuntu 20.04/22.04/24.04 | Windows 10/11, Server 2019/2022 |
| Python | 3.10+ | 3.10+ |
| SQL Server | 2017+ | 2017+ |
| ODBC Driver | Installed by `install.sh` | Installed by `install.ps1` |

---

## Installation

### Linux

```bash
git clone https://github.com/Rubes78/BrijjIngest.git
cd BrijjIngest
sudo ./install.sh
```

Optional overrides:
```bash
sudo INSTALL_DIR=/srv/brijj BRIJJ_PORT=8080 ./install.sh
```

| Variable | Default | Description |
|---|---|---|
| `INSTALL_DIR` | `/opt/brijjingest` | Installation directory |
| `BRIJJ_PORT` | `5000` | Web UI port |
| `BRIJJ_HOST` | `0.0.0.0` | Bind address |

### Windows

Run **PowerShell as Administrator**:

```powershell
git clone https://github.com/Rubes78/BrijjIngest.git
cd BrijjIngest
.\install.ps1
```

Optional overrides:
```powershell
.\install.ps1 -InstallDir "D:\Apps\BrijjIngest" -Port 8080
```

| Parameter | Default | Description |
|---|---|---|
| `-InstallDir` | `C:\BrijjIngest` | Installation directory |
| `-Port` | `5000` | Web UI port |
| `-BindHost` | `0.0.0.0` | Bind address |

Both installers handle:
1. Microsoft ODBC Driver 18 for SQL Server (skipped if already installed)
2. Python virtual environment + dependencies
3. `config.ini` scaffolded from the example template (never overwrites an existing file)
4. System service installation (systemd on Linux, NSSM on Windows)

---

## Configuration

After installing, open the web UI at `http://<server>:5000` and configure:

1. **Database** — SQL Server host, port, and login credentials
2. **API Endpoints** — Brijj API base URLs and page size (defaults are correct for production)
3. **Companies** — Add one entry per Brijj company with its API credentials and target database name

Or edit `config.ini` directly:

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

```bash
# Today only (default)
venv/bin/python ingest_brijj.py --company VD001

# Specific date range
venv/bin/python ingest_brijj.py --company VD001 --start-date 2026-01-01 --end-date 2026-01-31

# All enabled companies
venv/bin/python ingest_brijj.py --start-date 2026-02-01 --end-date 2026-02-24
```

On Windows, replace `venv/bin/python` with `venv\Scripts\python.exe`.

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

### Linux

```bash
sudo systemctl start   brijjdata
sudo systemctl stop    brijjdata
sudo systemctl restart brijjdata
sudo systemctl status  brijjdata

# View live logs
journalctl -u brijjdata -f
```

### Windows

```powershell
net start brijjdata
net stop  brijjdata

# Or via the helper scripts in the install directory:
start.bat
stop.bat

# View logs
Get-Content C:\BrijjIngest\logs\brijjdata.log -Tail 50 -Wait
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
├── install.sh            # Linux installer (Debian/Ubuntu)
├── install.ps1           # Windows installer (PowerShell)
├── requirements.txt      # Python dependencies
├── brijjdata.service     # systemd service definition (Linux)
├── start.sh / stop.sh    # Linux service helpers
├── install_service.sh    # Linux manual service install
├── config.ini.example    # Configuration template
└── config.ini            # Live config — gitignored, never committed
```
