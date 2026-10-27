$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    throw 'This build script creates a Windows application and must run on Windows.'
}

Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $PSScriptRoot '.venv-build')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the build environment.' }
}

& $python -m pip install --disable-pip-version-check -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }

& $python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name GLPI-Agent-Installer `
    --add-data 'linux_astra:linux_astra' `
    --add-data 'linux_mint:linux_mint' `
    --distpath dist --workpath build app.py
if ($LASTEXITCODE -ne 0) { throw 'The Windows application build failed.' }

$application = Join-Path $PSScriptRoot 'dist\GLPI-Agent-Installer'
$executable = Join-Path $application 'GLPI-Agent-Installer.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    throw 'The executable was not created.'
}
if (Get-ChildItem -LiteralPath $application -Recurse -Force -File -Filter '.env') {
    throw 'The private .env file was included in the build.'
}

$archive = Join-Path $PSScriptRoot 'dist\GLPI-Agent-Installer.zip'
for ($attempt = 1; $attempt -le 4; $attempt++) {
    try {
        Compress-Archive -LiteralPath $application -DestinationPath $archive -Force -ErrorAction Stop
        break
    } catch {
        if ($attempt -eq 4) { throw }
        Start-Sleep -Seconds 2
    }
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
    $names = @($zip.Entries | ForEach-Object FullName)
    if (-not ($names | Where-Object { $_ -like '*GLPI-Agent-Installer.exe' })) {
        throw 'The archive does not contain the executable.'
    }
    if (-not ($names | Where-Object { $_ -like '*base_library.zip' })) {
        throw 'The archive does not contain the Python runtime.'
    }
    if ($names | Where-Object { $_ -like '*.env' }) {
        throw 'The private .env file was included in the archive.'
    }
} finally {
    $zip.Dispose()
}
Write-Host "Build ready: $executable"
Write-Host "Portable archive: $archive"
