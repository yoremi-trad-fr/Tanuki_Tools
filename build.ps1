$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$BuildDeps = Join-Path $env:TEMP "tanuki-tools-build-deps"
New-Item -ItemType Directory -Force $BuildDeps | Out-Null

python -m pip install --upgrade --target $BuildDeps pyinstaller pycryptodome Pillow
$env:PYTHONPATH = $BuildDeps

Push-Location $Root
try {
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name TanukiTools `
        --exclude-module numpy `
        --paths $Root `
        --add-data "tanuki_tools/resources/tanuki.lst;resources" `
        main.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller a echoue avec le code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

Write-Host "Executable cree : $Root\dist\TanukiTools.exe"
