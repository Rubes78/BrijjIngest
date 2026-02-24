<#
.SYNOPSIS
    BrijjIngest Windows Installer

.DESCRIPTION
    Installs BrijjIngest and all dependencies on Windows 10/11 or Server 2019/2022.
    Installs the Microsoft ODBC Driver 18, creates a Python venv, and registers
    the web UI as a Windows service via NSSM.

.PARAMETER InstallDir
    Directory to install into. Default: C:\BrijjIngest

.PARAMETER Port
    Port for the web UI. Default: 5000

.PARAMETER BindHost
    Address to bind to. Default: 0.0.0.0

.EXAMPLE
    .\install.ps1
    .\install.ps1 -InstallDir "D:\Apps\BrijjIngest" -Port 8080
#>
param(
    [string]$InstallDir = "C:\BrijjIngest",
    [int]   $Port       = 5000,
    [string]$BindHost   = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$ServiceName = "brijjdata"
$Repo        = "https://github.com/Rubes78/BrijjIngest.git"
$WinSwUrl    = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
$OdbcUrl     = "https://go.microsoft.com/fwlink/?linkid=2280794"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Header { param($Text)
    Write-Host ""
    Write-Host "--- $Text ---" -ForegroundColor Cyan
}
function Write-Ok   { param($Text) Write-Host "[OK]   $Text" -ForegroundColor Green }
function Write-Info { param($Text) Write-Host "[INFO] $Text" -ForegroundColor Blue }
function Write-Warn { param($Text) Write-Host "[WARN] $Text" -ForegroundColor Yellow }
function Die        { param($Text) Write-Host "[ERROR] $Text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# Administrator check
# ---------------------------------------------------------------------------
Write-Header "BrijjIngest Installer"
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) {
    Die "Please run this script as Administrator (right-click -> Run as Administrator)."
}

# ---------------------------------------------------------------------------
# Check Python 3.10+
# ---------------------------------------------------------------------------
Write-Header "Checking Python"
$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 10) {
                $PythonCmd = $cmd
                Write-Ok "Found $ver"
                break
            }
        }
    } catch {}
}
if (-not $PythonCmd) {
    Write-Warn "Python 3.10+ not found. Attempting install via winget..."
    try {
        winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User")
        $PythonCmd = "python"
        Write-Ok "Python installed"
    } catch {
        Die "Could not install Python automatically. Install Python 3.10+ from https://python.org and re-run."
    }
}

# ---------------------------------------------------------------------------
# ODBC Driver 18
# ---------------------------------------------------------------------------
Write-Header "Checking ODBC Driver 18 for SQL Server"
$OdbcInstalled = Get-OdbcDriver -Name "ODBC Driver 18 for SQL Server" -ErrorAction SilentlyContinue
if ($OdbcInstalled) {
    Write-Ok "ODBC Driver 18 already installed - skipping"
} else {
    # Try winget first - cleanest and most reliable method
    $OdbcDone = $false
    $WingetAvailable = $null
    try { $WingetAvailable = & winget --version 2>&1 } catch {}

    if ($WingetAvailable) {
        Write-Info "Installing ODBC Driver 18 via winget..."
        try {
            winget install --id Microsoft.ODBCDriver18forSQLServer --silent `
                --accept-source-agreements --accept-package-agreements
            $OdbcDone = $true
            Write-Ok "ODBC Driver 18 installed via winget"
        } catch {
            Write-Warn "winget install failed - falling back to direct download..."
        }
    }

    if (-not $OdbcDone) {
        Write-Info "Downloading ODBC Driver 18 MSI..."
        $OdbcMsi = Join-Path $env:TEMP "msodbcsql18.msi"

        # Use WebClient which follows redirects more reliably than Invoke-WebRequest
        try {
            $WebClient = New-Object System.Net.WebClient
            $WebClient.DownloadFile($OdbcUrl, $OdbcMsi)
        } catch {
            # Last resort: Invoke-WebRequest with redirect following
            Invoke-WebRequest -Uri $OdbcUrl -OutFile $OdbcMsi -UseBasicParsing -MaximumRedirection 10
        }

        $MsiSize = (Get-Item $OdbcMsi).Length
        if ($MsiSize -lt 1MB) {
            Die "Downloaded file is too small ($MsiSize bytes) - download may have failed. Install ODBC Driver 18 manually from https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"
        }

        Write-Info "Installing ODBC Driver 18..."
        $proc = Start-Process msiexec -ArgumentList "/quiet /i `"$OdbcMsi`" IACCEPTMSODBCSQLLICENSETERMS=YES" -Wait -PassThru
        Remove-Item $OdbcMsi -Force -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) {
            Die "ODBC Driver installation failed (exit code $($proc.ExitCode)).`nInstall manually from https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"
        }
        Write-Ok "ODBC Driver 18 installed"
    }
}

# ---------------------------------------------------------------------------
# Clone or copy repo
# ---------------------------------------------------------------------------
Write-Header "Setting up application in $InstallDir"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$InstallDirFull = (New-Item -ItemType Directory -Force -Path $InstallDir).FullName
$ScriptDirFull  = (Resolve-Path $ScriptDir).Path

if ($ScriptDirFull -eq $InstallDirFull) {
    Write-Ok "Running from install directory - no copy needed"
} elseif (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Info "Repo already cloned - pulling latest..."
    git -C $InstallDir pull --ff-only
    Write-Ok "Repository updated"
} else {
    $GitAvailable = $null
    try { $GitAvailable = & git --version 2>&1 } catch {}
    if ($GitAvailable) {
        Write-Info "Cloning repository..."
        git clone $Repo $InstallDir
        Write-Ok "Repository cloned"
    } else {
        Write-Info "git not found - copying files from current directory..."
        $ExcludeDirs = @("venv", ".git", "nssm", "logs")
        Get-ChildItem -Path $ScriptDir |
            Where-Object { $_.Name -notin $ExcludeDirs } |
            Copy-Item -Destination $InstallDir -Recurse -Force
        Write-Ok "Files copied to $InstallDir"
    }
}

# ---------------------------------------------------------------------------
# Python venv + dependencies
# ---------------------------------------------------------------------------
Write-Header "Setting up Python environment"
$Venv      = Join-Path $InstallDir "venv"
$PythonExe = Join-Path $Venv "Scripts\python.exe"
$PipExe    = Join-Path $Venv "Scripts\pip.exe"
$ReqFile   = Join-Path $InstallDir "requirements.txt"

if (-not (Test-Path $PythonExe)) {
    Write-Info "Creating virtual environment..."
    & $PythonCmd -m venv $Venv
    Write-Ok "Virtual environment created"
} else {
    Write-Ok "Virtual environment already exists"
}

Write-Info "Installing Python dependencies..."
& $PythonExe -m pip install --quiet --upgrade pip
& $PythonExe -m pip install --quiet -r $ReqFile
Write-Ok "Dependencies installed"

# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------
Write-Header "Configuration"
$ConfigFile    = Join-Path $InstallDir "config.ini"
$ConfigExample = Join-Path $InstallDir "config.ini.example"
if (Test-Path $ConfigFile) {
    Write-Ok "config.ini already exists - leaving untouched"
} else {
    Copy-Item $ConfigExample $ConfigFile
    Write-Warn "Created config.ini from example."
    Write-Warn "Edit before starting the service: $ConfigFile"
}

# ---------------------------------------------------------------------------
# WinSW - Windows Service Wrapper (GitHub, always available)
# ---------------------------------------------------------------------------
Write-Header "Setting up Windows service (WinSW)"
$LogDir    = Join-Path $InstallDir "logs"
$WinSwExe  = Join-Path $InstallDir "$ServiceName.exe"
$WinSwXml  = Join-Path $InstallDir "$ServiceName.xml"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $WinSwExe)) {
    Write-Info "Downloading WinSW service wrapper..."
    $WebClient = New-Object System.Net.WebClient
    $WebClient.DownloadFile($WinSwUrl, $WinSwExe)
    Write-Ok "WinSW downloaded"
} else {
    Write-Ok "WinSW already present"
}

# Write the XML service definition
$WebConfigPy = Join-Path $InstallDir "web_config.py"
$WinSwConfig = @"
<service>
  <id>$ServiceName</id>
  <name>BrijjData Configuration UI</name>
  <description>BrijjData ingest and configuration web interface</description>
  <executable>$PythonExe</executable>
  <arguments>$WebConfigPy</arguments>
  <workingdirectory>$InstallDir</workingdirectory>
  <env name="BRIJJ_HOST" value="$BindHost"/>
  <env name="BRIJJ_PORT" value="$Port"/>
  <env name="PYTHONUNBUFFERED" value="1"/>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec"/>
  <logpath>$LogDir</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>3</keepFiles>
  </log>
</service>
"@
[System.IO.File]::WriteAllText($WinSwXml, $WinSwConfig)

# Remove existing service if present
$ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($ExistingService) {
    Write-Info "Removing existing service registration..."
    if ($ExistingService.Status -eq "Running") {
        & $WinSwExe stop | Out-Null
    }
    & $WinSwExe uninstall | Out-Null
}

Write-Info "Installing service '$ServiceName'..."
& $WinSwExe install
Write-Ok "Service installed"

# ---------------------------------------------------------------------------
# Management helper scripts
# ---------------------------------------------------------------------------
$StartBat = "@echo off`r`nnet start $ServiceName`r`npause`r`n"
$StopBat  = "@echo off`r`nnet stop $ServiceName`r`npause`r`n"
[System.IO.File]::WriteAllText((Join-Path $InstallDir "start.bat"), $StartBat)
[System.IO.File]::WriteAllText((Join-Path $InstallDir "stop.bat"),  $StopBat)

# ---------------------------------------------------------------------------
# Windows Firewall rule
# ---------------------------------------------------------------------------
Write-Header "Configuring Windows Firewall"
$RuleName = "BrijjData Web UI"
$ExistingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($ExistingRule) {
    Remove-NetFirewallRule -DisplayName $RuleName
}
New-NetFirewallRule -DisplayName $RuleName `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port | Out-Null
Write-Ok "Firewall rule added for port $Port"

# ---------------------------------------------------------------------------
# Start service
# ---------------------------------------------------------------------------
Write-Header "Starting service"
$ConfigContent = Get-Content $ConfigFile -Raw
if ($ConfigContent -match "YOUR_SQL_SERVER_HOST") {
    Write-Warn "Service NOT started - config.ini still has placeholder values."
    Write-Warn "Edit $ConfigFile then run:  net start $ServiceName"
} else {
    & $WinSwExe start
    Start-Sleep -Seconds 2
    $Svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($Svc -and $Svc.Status -eq "Running") {
        Write-Ok "Service started"
    } else {
        Write-Warn "Service did not start. Check the log for errors:"
        Write-Warn "  $LogDir\brijjdata.out.log"
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
$IP = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.InterfaceAlias -notmatch "Loopback" } |
       Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "+----------------------------------------------+" -ForegroundColor Cyan
Write-Host "|    BrijjIngest - Installation Complete       |" -ForegroundColor Cyan
Write-Host "+----------------------------------------------+" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Install dir : $InstallDir"
Write-Host "  Config file : $ConfigFile"
Write-Host "  Log file    : $LogDir\brijjdata.out.log"
Write-Host "  Service     : net start $ServiceName / net stop $ServiceName"
Write-Host "  Web UI      : " -NoNewline
Write-Host "http://${IP}:${Port}" -ForegroundColor Green
Write-Host ""
if ($ConfigContent -match "YOUR_SQL_SERVER_HOST") {
    Write-Host "  Next step:" -ForegroundColor Yellow
    Write-Host "    1. Edit $ConfigFile" -ForegroundColor Yellow
    Write-Host "    2. Run:  net start $ServiceName" -ForegroundColor Yellow
} else {
    Write-Host "  If the UI is not reachable, check the log:" -ForegroundColor Yellow
    Write-Host "    type $LogDir\brijjdata.out.log" -ForegroundColor Yellow
}
Write-Host ""
