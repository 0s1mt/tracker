# Runs one pipeline cycle (ingest -> dedup -> alert -> health-check ->
# auto-mirror-adoption -> stats -> member-directory, see pipeline.run_full_cycle)
# then regenerates reports/dashboard.html from the result, logging everything
# with a timestamp to logs/cycle.log.
#
# This is the local-only replacement for .github/workflows/pipeline.yml's
# 30-minute schedule: since main is deliberately staying unpushed (diverged
# from origin's "forumwatch" pivot - see README), nothing on GitHub runs this
# project anymore. Registered as a Windows Scheduled Task (see README's
# "Local sürekli çalıştırma" section for the exact schtasks command) so the
# tracker keeps collecting without a terminal window staying open.
#
# Each invocation is a fresh, isolated `--once` cycle - never a long-lived
# `--loop` process - so one cycle's crash/exception can never take down all
# future collection; the next scheduled run just starts clean. This also
# bounds auto-mirror-adoption latency to (DEAD_THRESHOLD=3) x (this
# schedule's interval), since health-check + adoption both run inside
# run_full_cycle - see health.py and auto_mirror.py.

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "cycle.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Keep the log from growing forever - a plain append file with no rotation
# would eventually become unwieldy on a machine left running for months.
# Trim to the last 5000 lines before each run once it crosses ~2MB.
if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 2MB)) {
    $tail = Get-Content $LogFile -Tail 5000
    Set-Content -Path $LogFile -Value $tail -Encoding utf8
}

$stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
Add-Content -Path $LogFile -Value "`n===== $stamp cycle start =====" -Encoding utf8

Set-Location $ProjectRoot
$python = "C:\Users\Monster\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"

# `*>>` (native redirection) writes UTF-16LE on this PowerShell version,
# which renders as space-separated characters to any UTF-8 reader (cat,
# tail, a text editor) - piping through Out-File -Encoding utf8 -Append
# keeps the log in a normal, greppable encoding.
& $python -m forumtracker.cli --once 2>&1 | Out-File -FilePath $LogFile -Encoding utf8 -Append
$cycleExit = $LASTEXITCODE

& $python -m forumtracker.cli --build-dashboard 2>&1 | Out-File -FilePath $LogFile -Encoding utf8 -Append
$dashboardExit = $LASTEXITCODE

$stampEnd = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
Add-Content -Path $LogFile -Value "===== $stampEnd cycle end (cycle exit=$cycleExit, dashboard exit=$dashboardExit) =====" -Encoding utf8
