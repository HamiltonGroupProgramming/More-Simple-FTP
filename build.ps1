# Build More Simple FTP on Windows: PyInstaller executables + Inno Setup installer.
# Requires Python 3.10+ and Inno Setup 6 (iscc.exe) on PATH or in its default folder.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt

$version = (python -c "import ftpclient; print(ftpclient.__version__)").Trim()
Write-Host "Building More Simple FTP $version"

# The app is built as a folder (starts faster and trips antivirus less than --onefile).
python -m PyInstaller --noconfirm --clean --onedir --windowed --name MoreSimpleFTP `
    --icon assets\more_simple_ftp.ico --add-data "assets;assets" `
    --hidden-import keyring.backends.Windows more_simple_ftp.py
python -m PyInstaller --noconfirm --clean --onefile --console  --name ftpclient     ftpclient.py

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$version" installer\more-simple-ftp.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Write-Host "Installer: dist\installer\MoreSimpleFTP-$version-setup.exe"
