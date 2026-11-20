# NIFTYBEES Algo - uninstall (run via uninstall.bat).
#   1. Stops the app if it is running (asks it to Quit; then stops anything still running from this folder)
#   2. Offers to back up your settings, order history and API keys to Documents
#   3. Removes the "NIFTYBEES Algo" shortcuts (Desktop + Start menu)
#   4. Offers to delete this folder
# Only touches this folder, the two shortcuts and (if you say yes) a new backup folder in Documents.
param(
    [int]$Port = 4200,          # launcher port (testing)
    [string]$BackupDir = '',    # backup parent folder (default: Documents)
    [switch]$Unattended,        # no questions: back up, remove shortcuts, keep the folder
    [switch]$DeleteFolder       # with -Unattended: also delete the folder
)
$ErrorActionPreference = 'Stop'

$Root    = $PSScriptRoot
$AppName = 'NIFTYBEES Algo'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ask($question, [bool]$default) {
    if ($Unattended) { return $default }
    $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
    $a = Read-Host "$question $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a -match '^(y|yes)$'
}
function Get-AppProcesses {
    # Every process whose program lives inside this folder (portable Python running launcher/backend).
    $prefix = (Join-Path $Root 'runtime') + '\'
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) }
}

Write-Host "$AppName - uninstall  ($Root)" -ForegroundColor Green

# Make sure this really is a NIFTYBEES Algo folder before doing anything (protects against deleting the wrong folder).
$isAppFolder = (Test-Path (Join-Path $Root 'launcher.py')) -and (Test-Path (Join-Path $Root 'app\index.html')) `
               -and (Test-Path (Join-Path $Root 'backend\src\app\main.py'))
if (-not $isAppFolder) {
    Write-Host "This folder doesn't look like a NIFTYBEES Algo installation - stopping." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 1. stop the app
Step "Stopping $AppName (if it is running)"
try {
    $null = Invoke-RestMethod -Method Post -Uri "http://localhost:$Port/launcher/quit" -Headers @{ Origin = "http://localhost:$Port" } -TimeoutSec 5
    Write-Host "  Asked the app to quit."
    Start-Sleep -Seconds 4
} catch {
    Write-Host "  The app's launcher isn't answering (probably not running)."
}
$left = @(Get-AppProcesses)
if ($left.Count -gt 0) {
    foreach ($p in $left) {
        Write-Host "  Stopping leftover process $($p.ProcessId) ($($p.Name))"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}
if (@(Get-AppProcesses).Count -gt 0) {
    Write-Host "  Some app processes are still running - restart Windows and run uninstall.bat again." -ForegroundColor Red
    exit 1
}
Write-Host "  Nothing from this folder is running."

# ---------------------------------------------------------------- 2. backup
$data = Join-Path $Root 'backend\data'
$envFile = Join-Path $Root 'backend\.env'
if ((Test-Path $data) -or (Test-Path $envFile)) {
    Step "Backup"
    Write-Host "  backend\data holds your settings, order history and activity log; backend\.env holds your Kite API key/secret."
    if (Ask "  Back them up to your Documents folder?" $true) {
        $parent = if ($BackupDir) { $BackupDir } else { [Environment]::GetFolderPath('MyDocuments') }
        $dest = Join-Path $parent ("NIFTYBEES-Algo-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        if (Test-Path $data)    { Copy-Item $data (Join-Path $dest 'data') -Recurse }
        if (Test-Path $envFile) { Copy-Item $envFile (Join-Path $dest '.env') }
        Write-Host "  Backed up to: $dest" -ForegroundColor Green
        Write-Host "  (Keep it private - it contains your API secret. To restore: copy 'data' and '.env' into backend\ of a new install.)"
    } else {
        Write-Host "  No backup made."
    }
}

# ---------------------------------------------------------------- 3. shortcuts
Step "Removing the '$AppName' shortcuts"
$removed = 0
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $lnk = Join-Path $dir "$AppName.lnk"
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "  Removed $lnk"; $removed++ }
}
if ($removed -eq 0) { Write-Host "  No shortcuts found." }

# ---------------------------------------------------------------- 4. delete the folder
$delete = if ($Unattended) { [bool]$DeleteFolder } else { Ask "`nDelete the whole folder $Root now? This can't be undone." $false }
if ($delete) {
    # This script runs from inside the folder, so the deletion is done by a hidden helper after this window
    # has closed. It retries for ~30 s: right after the app stops, a virus scanner or the search indexer can
    # still hold some files for a few seconds. (Start-Sleep, not 'timeout' - timeout fails without a console.)
    $q = $Root.Replace("'", "''")
    $helper = "Start-Sleep -Seconds 4; for (`$i = 0; `$i -lt 15; `$i++) { " +
              "try { [IO.Directory]::Delete('$q', `$true) } catch { }; " +
              "if (-not [IO.Directory]::Exists('$q')) { break }; Start-Sleep -Seconds 2 }"
    # -EncodedCommand: no quoting problems with spaces or special characters in the folder path.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($helper))
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $env:TEMP -WindowStyle Hidden `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
    Write-Host "`nUninstalled. The folder will be deleted a few seconds after this window closes." -ForegroundColor Green
} else {
    Write-Host "`nUninstalled. You can delete the folder $Root yourself whenever you like." -ForegroundColor Green
}
if (-not $Unattended) { Read-Host "`nPress Enter to close" | Out-Null }
exit 0
