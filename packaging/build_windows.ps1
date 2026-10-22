$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$PackagingPython = Join-Path $RootDir ".venv-packaging-windows\Scripts\python.exe"
$ProjectPython = Join-Path $RootDir ".venv\Scripts\python.exe"
if (Test-Path $PackagingPython) {
    $PythonExe = $PackagingPython
} elseif (Test-Path $ProjectPython) {
    $PythonExe = $ProjectPython
} else {
    $PythonExe = "py"
}

$DistDir = Join-Path $RootDir "dist"
$BuildDir = Join-Path $RootDir "build"
$SpecPath = Join-Path $RootDir "packaging\swcstudio_gui_windows.spec"
$ZipPath = Join-Path $DistDir "SWC-Studio-windows.zip"
$AppDir = Join-Path $DistDir "SWC-Studio"

Write-Host "Generating Windows icon from packaging/icon.png..."
if ($PythonExe -eq "py") {
    & py -3 "$RootDir\packaging\make_windows_icon.py"
} else {
    & $PythonExe "$RootDir\packaging\make_windows_icon.py"
}

Write-Host "Building Windows executable with PyInstaller..."
if ($PythonExe -eq "py") {
    & py -3 -m PyInstaller --noconfirm --clean $SpecPath
} else {
    & $PythonExe -m PyInstaller --noconfirm --clean $SpecPath
}
# A failed build would otherwise go on to stage and zip an older dist\ folder.
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

if (-not (Test-Path $AppDir)) {
    throw "Expected build output folder was not created: $AppDir"
}

Write-Host "Staging replaceable application and model layers..."
$RuntimeRoot = Join-Path $AppDir "_internal"
if (-not (Test-Path $RuntimeRoot)) {
    throw "Expected PyInstaller runtime directory was not created: $RuntimeRoot"
}
if ($PythonExe -eq "py") {
    & py -3 "$RootDir\packaging\stage_modular_payload.py" `
        --source-root $RootDir `
        --runtime-root $RuntimeRoot
} else {
    & $PythonExe "$RootDir\packaging\stage_modular_payload.py" `
        --source-root $RootDir `
        --runtime-root $RuntimeRoot
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to stage modular application/model payloads."
}

# Brief settle delay so AV / Windows Search / PyInstaller have all
# released their handles on the freshly-written tree before we read it.
Start-Sleep -Seconds 2

Write-Host "Creating shareable zip..."
# Compress-Archive races AV / Windows Search on freshly-written
# trees — base_library.zip is the usual victim. We shell out to
# Python's zipfile (different file-handle semantics, doesn't hit
# the same lock) and retry up to 3 times with backoff.
$ZipScriptFile = Join-Path $RootDir "packaging\_zip_dist.py"

$attempt = 0
$maxAttempts = 3
while ($true) {
    $attempt += 1
    try {
        if ($PythonExe -eq "py") {
            & py -3 $ZipScriptFile $AppDir $ZipPath
        } else {
            & $PythonExe $ZipScriptFile $AppDir $ZipPath
        }
        if ($LASTEXITCODE -eq 0 -and (Test-Path $ZipPath)) {
            break
        }
        throw "zip script exit code $LASTEXITCODE"
    } catch {
        if ($attempt -ge $maxAttempts) {
            throw "Failed to create zip after $attempt attempts: $($_.Exception.Message)"
        }
        Write-Host "Zip attempt $attempt failed ($($_.Exception.Message)); retrying after 5s..."
        Start-Sleep -Seconds 5
    }
}

Write-Host ""
Write-Host "Build complete:"
Write-Host "  App folder: $AppDir"
Write-Host "  Zip file:   $ZipPath"
