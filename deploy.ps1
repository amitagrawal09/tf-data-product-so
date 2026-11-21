<#
.SYNOPSIS
  Sales Order data product - deployment script for Windows (PowerShell 5.1+ / PowerShell 7+).

.DESCRIPTION
  Native Windows counterpart of deploy.sh.

    .\deploy.ps1 local         run all 4 services on this machine (SQLite, HTTP bus) + smoke test
    .\deploy.ps1 local-stop    stop the local services
    .\deploy.ps1 test          run the automated test suite (pytest)
    .\deploy.ps1 smoke [URL]   run the smoke test against an API base URL (default http://localhost:8080/api)
    .\deploy.ps1 demo-data     load demo orders across all regions / channels (after local)
    .\deploy.ps1 docker        docker compose (Postgres + 4 containers) + smoke test
    .\deploy.ps1 docker-down   stop the compose stack
    .\deploy.ps1 azure         runs deploy.sh azure through WSL or Git Bash (the Azure flow is bash-only)

  If script execution is blocked, run once per session:
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#>
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest
)

# "Continue": Windows PowerShell 5.1 turns any stderr output of native tools (pip warnings) into
# terminating errors under "Stop". Native failures are detected through $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$ApiKey   = if ($env:LOCAL_API_KEY) { $env:LOCAL_API_KEY } else { "local-dev-key" }
$VenvDir  = Join-Path $Root ".venv"
$VenvPy   = Join-Path $VenvDir "Scripts\python.exe"
$RunDir   = Join-Path $Root "data\run"
$Services = @(
  @{ Name = "system";     Port = 8003 },
  @{ Name = "process";    Port = 8002 },
  @{ Name = "experience"; Port = 8001 },
  @{ Name = "gateway";    Port = $(if ($env:GATEWAY_PORT) { [int]$env:GATEWAY_PORT } else { 8080 }) }
)

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ ok ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "[fail] $m" -ForegroundColor Red; exit 1 }

function Find-Python {
  # Sets $script:PyExe / $script:PyArgs. `py -3` is the Python launcher; `python`/`python3` may be
  # Microsoft Store stubs, so each candidate is actually executed.
  $candidates = @()
  if ($env:PYTHON) { $candidates += $env:PYTHON }
  $candidates += @("py -3", "python", "python3")
  foreach ($cand in $candidates) {
    $parts = $cand -split " "
    $exe = $parts[0]
    $extra = @($parts | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
      & $exe @extra -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 3)" 2>$null | Out-Null
    } catch { continue }
    if ($LASTEXITCODE -eq 0) { $script:PyExe = $exe; $script:PyArgs = $extra; return }
  }
  Die "Python 3.11+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
}

function Ensure-Python {
  if (-not (Test-Path $VenvPy)) {
    Find-Python
    Step "Creating virtual environment ($VenvDir)"
    & $script:PyExe @($script:PyArgs) -m venv $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Die "Could not create the virtual environment" }
  }
  $stamp = Join-Path $VenvDir ".tfso-installed"
  $needs = -not (Test-Path $stamp)
  if (-not $needs) {
    $t = (Get-Item $stamp).LastWriteTime
    $needs = ((Get-Item requirements.txt).LastWriteTime -gt $t) -or ((Get-Item pyproject.toml).LastWriteTime -gt $t)
  }
  if ($needs) {
    Step "Installing dependencies (first run takes a few minutes)"
    & $VenvPy -m pip install --quiet --disable-pip-version-check --upgrade pip
    & $VenvPy -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Die "pip install -r requirements.txt failed" }
    & $VenvPy -m pip install --quiet --disable-pip-version-check -e .
    if ($LASTEXITCODE -ne 0) { Die "pip install -e . failed" }
    Set-Content -Path $stamp -Value (Get-Date)
  }
  Ok ("Python environment ready (" + (& $VenvPy --version) + ")")
}

function Test-Health([int]$Port, [int]$TimeoutSec = 1) {
  try {
    $r = Invoke-WebRequest -Uri "http://localhost:$Port/health" -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.StatusCode -eq 200
  } catch { return $false }
}

function Show-Log([string]$Name) {
  foreach ($f in @("$Name.err.log", "$Name.log")) {
    $path = Join-Path $RunDir $f
    if ((Test-Path $path) -and (Get-Item $path).Length -gt 0) {
      Write-Host "----- last lines of data\run\$f -----" -ForegroundColor Yellow
      Get-Content $path -Tail 25 | ForEach-Object { Write-Host "  $_" }
    }
  }
}

function Wait-Health([int]$Port, [string]$Name, [int]$Seconds = 60) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  $pidFile = Join-Path $RunDir "$Name.pid"
  while ((Get-Date) -lt $deadline) {
    if (Test-Health $Port 3) { Ok "$Name healthy (http://localhost:$Port/health)"; return }
    if (Test-Path $pidFile) {
      $procId = [int](Get-Content $pidFile)
      if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        Show-Log $Name
        Die "$Name exited during startup (see log above)"
      }
    }
    Start-Sleep -Milliseconds 700
  }
  Show-Log $Name
  Die "$Name did not become healthy on :$Port (see log above)"
}

function Test-PortFree([int]$Port) {
  # Binding is the only reliable test on Windows: ports can be in use by another program *or* reserved
  # by Windows/Hyper-V (see: netsh interface ipv4 show excludedportrange protocol=tcp).
  foreach ($ip in @([System.Net.IPAddress]::Loopback, [System.Net.IPAddress]::Any)) {
    $l = $null
    try { $l = [System.Net.Sockets.TcpListener]::new($ip, $Port); $l.Start() }
    catch { return $false }
    finally { if ($l) { $l.Stop() } }
  }
  return $true
}

function Invoke-Smoke([string]$Base) {
  if ($env:SKIP_SMOKE -eq "true") { Warn "SKIP_SMOKE=true - smoke test skipped"; return }
  Step "Smoke test against $Base"
  & $VenvPy -m tfso.tools.smoke --base $Base --api-key $ApiKey
  if ($LASTEXITCODE -ne 0) { Die "Smoke test failed" }
  Ok "Smoke test passed"
}

function Stop-Local {
  Step "Stopping local services"
  $any = $false
  if (Test-Path $RunDir) {
    Get-ChildItem $RunDir -Filter *.pid | ForEach-Object {
      $procId = [int](Get-Content $_.FullName)
      $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
      if ($p) {
        # /T also stops the child python processes uvicorn may have spawned
        & taskkill /PID $procId /T /F *> $null
        Write-Host "stopped $($_.BaseName) (pid $procId)"; $any = $true
      }
      Remove-Item $_.FullName -Force
    }
  }
  if (-not $any) { Write-Host "nothing running" }
}

function Start-Local {
  Ensure-Python
  New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
  foreach ($s in $Services) {
    if (Test-Health $s.Port) { Die "port $($s.Port) is already serving something; run '.\deploy.ps1 local-stop' or free the port" }
    if (-not (Test-PortFree $s.Port)) {
      if ($s.Name -eq "gateway") {
        $alt = (@(8081..8099) + @(18080..18099)) | Where-Object { Test-PortFree $_ } | Select-Object -First 1
        if (-not $alt) { Die "port $($s.Port) is in use and no free fallback port was found; set `$env:GATEWAY_PORT" }
        Warn "port $($s.Port) is in use or reserved by Windows; the gateway/console will use port $alt instead"
        $s.Port = $alt
      } else {
        $owner = (Get-NetTCPConnection -LocalPort $s.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
        $hint = if ($owner) { " (in use by " + (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName + ", pid $owner)" } else { " (possibly reserved by Windows: netsh interface ipv4 show excludedportrange protocol=tcp)" }
        Die "port $($s.Port) for the $($s.Name) API is not available$hint"
      }
    }
  }
  $gw = ($Services | Where-Object { $_.Name -eq "gateway" }).Port
  Step "Starting services (SQLite + local HTTP bus)"
  $common = @{
    TFSO_ENV = "local"; TFSO_BUS_MODE = "http"; TFSO_AUTH_MODE = "dev"; TFSO_API_KEYS = $ApiKey
    TFSO_PROCESS_URL = "http://localhost:8002"; TFSO_SYSTEM_URL = "http://localhost:8003"
    TFSO_EXPERIENCE_URL = "http://localhost:8001"; TFSO_D365_MODE = "simulator"; TFSO_LAKEHOUSE_MODE = "file"
    TFSO_FRONTEND_DIR = (Join-Path $Root "frontend"); PYTHONUNBUFFERED = "1"; PYTHONIOENCODING = "utf-8"
  }
  foreach ($k in $common.Keys) { Set-Item -Path "env:$k" -Value $common[$k] }
  foreach ($s in $Services) {
    $name = $s.Name
    switch ($name) {
      "process" { $env:TFSO_DB_URL = "sqlite:///./data/process.db" }
      "system"  { $env:TFSO_DB_URL = "sqlite:///./data/system.db" }
      default   { Remove-Item env:TFSO_DB_URL -ErrorAction SilentlyContinue }
    }
    $env:SERVICE = $name
    $p = Start-Process -FilePath $VenvPy -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
      -ArgumentList @("-m", "uvicorn", "tfso.$name.app:app", "--host", "127.0.0.1", "--port", "$($s.Port)") `
      -RedirectStandardOutput (Join-Path $RunDir "$name.log") -RedirectStandardError (Join-Path $RunDir "$name.err.log")
    Set-Content -Path (Join-Path $RunDir "$name.pid") -Value $p.Id
    Write-Host "started $name on :$($s.Port) (pid $($p.Id), logs data\run\$name.log / $name.err.log)"
  }
  Remove-Item env:TFSO_DB_URL -ErrorAction SilentlyContinue
  foreach ($s in $Services) { Wait-Health $s.Port $s.Name }
  Invoke-Smoke "http://localhost:$gw/api"
  Write-Host ""
  Write-Host "Local deployment ready" -ForegroundColor Green
  Write-Host "  Order Operations Console : http://localhost:$gw/"
  Write-Host "  Process API docs         : http://localhost:8002/docs   (Order Status API)"
  Write-Host "  Experience API docs      : http://localhost:8001/docs"
  Write-Host "  System API docs          : http://localhost:8003/docs"
  Write-Host "  API key (x-api-key)      : $ApiKey"
  Write-Host "  Stop                     : .\deploy.ps1 local-stop"
}

function Invoke-Bash([string]$Sub) {
  $sh = $null
  if (Get-Command wsl -ErrorAction SilentlyContinue) { $sh = "wsl" }
  elseif (Test-Path "$env:ProgramFiles\Git\bin\bash.exe") { $sh = "$env:ProgramFiles\Git\bin\bash.exe" }
  if (-not $sh) { Die "'$Sub' needs bash: install WSL (wsl --install) or Git for Windows, then rerun." }
  if ($sh -eq "wsl") { & wsl bash ./deploy.sh $Sub @Rest } else { & $sh ./deploy.sh $Sub @Rest }
  exit $LASTEXITCODE
}

switch ($Command.ToLower()) {
  "local"       { Start-Local }
  "local-stop"  { Stop-Local }
  "test"        { Ensure-Python; Step "Running tests"; & $VenvPy -m pytest -q -p no:logging @Rest; exit $LASTEXITCODE }
  "demo-data"   { Ensure-Python; $u = if ($Rest) { $Rest[0] } else { "http://localhost:$(($Services | Where-Object { $_.Name -eq 'gateway' }).Port)/api" }
                  Step "Loading demo orders across all regions"; & $VenvPy -m tfso.tools.seed_demo --base $u --api-key $ApiKey; exit $LASTEXITCODE }
  "smoke"       { Ensure-Python; $u = if ($Rest) { $Rest[0] } else { "http://localhost:$(($Services | Where-Object { $_.Name -eq 'gateway' }).Port)/api" }; Invoke-Smoke $u }
  "docker"      {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "Docker Desktop is required for this mode" }
    Step "docker compose up"; docker compose up -d --build; if ($LASTEXITCODE -ne 0) { Die "docker compose failed" }
    Wait-Health 8080 "gateway" 180; Ensure-Python; Invoke-Smoke "http://localhost:8080/api"
  }
  "docker-down" { docker compose down @Rest }
  "azure"       { Invoke-Bash "azure" }
  "destroy"     { Invoke-Bash "destroy" }
  default       { Get-Help $PSCommandPath -Detailed | Out-String | Write-Host }
}
