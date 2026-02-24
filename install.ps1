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
$NssmUrl     = "https://nssm.cc/release/nssm-2.24.zip"
$OdbcUrl     = "https://go.microsoft.com/fwlink/?linkid=2282755"

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
    Write-Info "Downloading ODBC Driver 18..."
    $OdbcMsi = Join-Path $env:TEMP "msodbcsql18.msi"
    Invoke-WebRequest -Uri $OdbcUrl -OutFile $OdbcMsi -UseBasicParsing
    Write-Info "Installing ODBC Driver 18 (silent)..."
    $proc = Start-Process msiexec -ArgumentList "/quiet /i `"$OdbcMsi`" IACCEPTMSODBCSQLLICENSETERMS=YES" -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        Die "ODBC Driver installation failed (exit code $($proc.ExitCode))."
    }
    Remove-Item $OdbcMsi -Force
    Write-Ok "ODBC Driver 18 installed"
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
& $PipExe install --quiet --upgrade pip
& $PipExe install --quiet -r $ReqFile
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
# NSSM - Windows service manager
# ---------------------------------------------------------------------------
Write-Header "Setting up Windows service (NSSM)"
$NssmDir = Join-Path $InstallDir "nssm"
$NssmExe = Join-Path $NssmDir "nssm.exe"

if (-not (Test-Path $NssmExe)) {
    Write-Info "Downloading NSSM..."
    $NssmZip     = Join-Path $env:TEMP "nssm.zip"
    $NssmExtract = Join-Path $env:TEMP "nssm-extract"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $NssmZip -UseBasicParsing
    Expand-Archive -Path $NssmZip -DestinationPath $NssmExtract -Force
    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    Copy-Item (Join-Path $NssmExtract "nssm-2.24\win64\nssm.exe") $NssmExe -Force
    Remove-Item $NssmZip    -Force
    Remove-Item $NssmExtract -Recurse -Force
    Write-Ok "NSSM downloaded"
} else {
    Write-Ok "NSSM already present"
}

# Remove existing service if present
$ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($ExistingService) {
    Write-Info "Removing existing service registration..."
    if ($ExistingService.Status -eq "Running") {
        & $NssmExe stop $ServiceName confirm | Out-Null
    }
    & $NssmExe remove $ServiceName confirm | Out-Null
}

# Create log directory
$LogDir = Join-Path $InstallDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Install service
$WebConfigPy = Join-Path $InstallDir "web_config.py"
Write-Info "Installing service '$ServiceName'..."
& $NssmExe install $ServiceName $PythonExe $WebConfigPy
& $NssmExe set $ServiceName AppDirectory       $InstallDir
& $NssmExe set $ServiceName AppEnvironmentExtra "BRIJJ_HOST=$BindHost" "BRIJJ_PORT=$Port" "PYTHONUNBUFFERED=1"
& $NssmExe set $ServiceName Start              SERVICE_AUTO_START
& $NssmExe set $ServiceName AppStdout          (Join-Path $LogDir "brijjdata.log")
& $NssmExe set $ServiceName AppStderr          (Join-Path $LogDir "brijjdata.log")
& $NssmExe set $ServiceName AppRotateFiles     1
& $NssmExe set $ServiceName AppRotateBytes     10485760
& $NssmExe set $ServiceName DisplayName        "BrijjData Configuration UI"
& $NssmExe set $ServiceName Description        "BrijjData ingest and configuration web interface"
Write-Ok "Service installed"

# ---------------------------------------------------------------------------
# Management helper scripts
# ---------------------------------------------------------------------------
$StartBat = "@echo off`r`nnet start $ServiceName`r`npause`r`n"
$StopBat  = "@echo off`r`nnet stop $ServiceName`r`npause`r`n"
[System.IO.File]::WriteAllText((Join-Path $InstallDir "start.bat"), $StartBat)
[System.IO.File]::WriteAllText((Join-Path $InstallDir "stop.bat"),  $StopBat)

# ---------------------------------------------------------------------------
# Start service
# ---------------------------------------------------------------------------
Write-Header "Starting service"
$ConfigContent = Get-Content $ConfigFile -Raw
if ($ConfigContent -match "YOUR_SQL_SERVER_HOST") {
    Write-Warn "Service NOT started - config.ini still has placeholder values."
    Write-Warn "Edit $ConfigFile then run:  net start $ServiceName"
} else {
    Start-Service $ServiceName
    Write-Ok "Service started"
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
Write-Host "  Log file    : $(Join-Path $LogDir 'brijjdata.log')"
Write-Host "  Service     : $ServiceName  (net start/stop $ServiceName)"
Write-Host "  Web UI      : " -NoNewline
Write-Host "http://${IP}:${Port}" -ForegroundColor Green
Write-Host ""
if ($ConfigContent -match "YOUR_SQL_SERVER_HOST") {
    Write-Host "  Next step: edit config.ini with your credentials," -ForegroundColor Yellow
    Write-Host "  then run:  net start $ServiceName" -ForegroundColor Yellow
}
Write-Host ""
