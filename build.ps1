# Build More Simple FTP on Windows: PyInstaller executables + Inno Setup installer.
# Requires Python 3.9+ and Inno Setup 6 (iscc.exe) on PATH or in its default folder.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt

$version = (python -c "import ftpclient; print(ftpclient.__version__)").Trim()
Write-Host "Building More Simple FTP $version"

python -m PyInstaller --noconfirm --clean --onefile --windowed --name MoreSimpleFTP --icon assets\icon.ico --add-data "assets;assets" ftpgui.py
python -m PyInstaller --noconfirm --clean --onefile --console  --name ftpclient     --icon assets\icon.ico ftpclient.py

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$version" installer\more-simple-ftp.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Write-Host "Installer: dist\installer\MoreSimpleFTP-$version-setup.exe"
