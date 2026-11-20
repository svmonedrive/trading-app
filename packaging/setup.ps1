# NIFTYBEES Algo - one-time setup (run via setup.bat). Safe to run again. Needs internet.
#   1. Portable Python into .\runtime\python   (official python.org "embeddable" build; no admin, nothing system-wide)
#   2. Python packages from backend\requirements.txt (from PyPI) into that portable Python
#   3. backend\.env created from backend\.env.example (the app asks for your Kite API key/secret on first start)
#   4. "NIFTYBEES Algo" shortcuts on the Desktop and in the Start menu
#   5. Opens the app
# To uninstall: run uninstall.bat, then delete this folder. Nothing else on the PC is touched.
param(
    [switch]$NoStart,      # don't open the app at the end (unattended runs)
    [switch]$NoShortcuts   # don't create shortcuts (testing)
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar on PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PyVersion = '3.12.10'
$Root      = $PSScriptRoot
$RtDir     = Join-Path $Root 'runtime\python'
$Py        = Join-Path $RtDir 'python.exe'
$PyW       = Join-Path $RtDir 'pythonw.exe'
$Backend   = Join-Path $Root 'backend'
$AppName   = 'NIFTYBEES Algo'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

try {
    Write-Host "$AppName setup  ($Root)" -ForegroundColor Green
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        Fail "This app supports 64-bit Intel/AMD Windows only (this PC: $env:PROCESSOR_ARCHITECTURE)."
    }
    # Windows' 260-character path limit: the deepest installed package file adds ~125 characters to this path.
    if ($Root.Length -gt 120) {
        Fail "This folder's path is too long ($($Root.Length) characters). Move the NIFTYBEES-Algo folder somewhere shorter, e.g. C:\NIFTYBEES-Algo or Documents\NIFTYBEES-Algo, and run setup.bat again."
    }
    if (-not (Test-Path (Join-Path $Backend 'requirements.txt'))) {
        Fail "backend\requirements.txt not found - unzip the whole NIFTYBEES-Algo folder and run setup.bat from inside it."
    }

    # ---------------------------------------------------------------- 1. portable Python
    if (Test-Path $Py) {
        Step "Portable Python already present: $(& $Py --version)"
    } else {
        Step "Downloading portable Python $PyVersion from python.org (about 11 MB)"
        $zip = Join-Path $env:TEMP "python-$PyVersion-embed-amd64.zip"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile $zip
        } catch {
            Fail "Could not download Python ($($_.Exception.Message)). Check the internet connection / company proxy and run setup.bat again."
        }
        New-Item -ItemType Directory -Force -Path $RtDir | Out-Null
        Expand-Archive -Path $zip -DestinationPath $RtDir -Force
        Remove-Item $zip -Force

        # The embeddable build ignores site-packages until 'import site' is enabled in its ._pth file.
        $pth = Get-ChildItem -Path $RtDir -Filter 'python*._pth' | Select-Object -First 1
        if (-not $pth) { Fail "Unexpected Python download (no ._pth file)." }
        (Get-Content $pth.FullName) -replace '^#\s*import site\s*$', 'import site' | Set-Content -Encoding Ascii $pth.FullName

        Step "Installing pip into portable Python"
        $getPip = Join-Path $env:TEMP 'get-pip.py'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getPip
        } catch {
            Fail "Could not download pip ($($_.Exception.Message))."
        }
        & $Py $getPip --no-warn-script-location --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { Fail "pip installation failed." }
        Remove-Item $getPip -Force
    }

    # ---------------------------------------------------------------- 2. packages
    Step "Installing Python packages from PyPI (first time: a few minutes)"
    & $Py -m pip install --upgrade --no-warn-script-location --disable-pip-version-check -r (Join-Path $Backend 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail "Installing packages failed - see the messages above. Run setup.bat again to retry." }

    # ---------------------------------------------------------------- 3. .env
    $envFile = Join-Path $Backend '.env'
    if (Test-Path $envFile) {
        Step "backend\.env already exists - kept as it is"
    } else {
        Copy-Item (Join-Path $Backend '.env.example') $envFile
        Step "Created backend\.env (PAPER mode - orders are simulated)"
    }

    # ---------------------------------------------------------------- 4. shortcuts
    if (-not $NoShortcuts) {
        Step "Creating '$AppName' shortcuts (Desktop and Start menu)"
        $shell = New-Object -ComObject WScript.Shell
        foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
            $lnk = $shell.CreateShortcut((Join-Path $dir "$AppName.lnk"))
            $lnk.TargetPath       = $PyW                              # windowless Python: no black window
            $lnk.Arguments        = "`"$(Join-Path $Root 'launcher.py')`" --app"
            $lnk.WorkingDirectory = $Root
            $lnk.IconLocation     = (Join-Path $Root 'app\favicon.ico')
            $lnk.Description      = 'Open NIFTYBEES Algo (starts the trading backend in the background)'
            $lnk.Save()
            Write-Host "  $dir\$AppName.lnk"
        }
    }

    Write-Host "`nSetup complete." -ForegroundColor Green
    Write-Host "Open the app any time with the '$AppName' shortcut. It keeps running in the background"
    Write-Host "until you click Quit in the app."
    if (-not $NoStart) {
        Step "Opening $AppName"
        Start-Process -FilePath $PyW -ArgumentList "`"$(Join-Path $Root 'launcher.py')`"", '--app' -WorkingDirectory $Root
    }
    exit 0
} catch {
    Fail $_.Exception.Message
}
