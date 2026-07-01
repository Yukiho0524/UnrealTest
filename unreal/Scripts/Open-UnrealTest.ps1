$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$engineConfigPath = Join-Path $projectRoot "engine.version.json"
$engineConfig = Get-Content $engineConfigPath -Raw | ConvertFrom-Json
$editorPath = $engineConfig.windowsEditorPath
$projectPath = Join-Path $projectRoot "UnrealTest.uproject"

if (-not (Test-Path $editorPath)) {
    throw "Unreal Editor was not found at the pinned path: $editorPath"
}

if (-not (Test-Path $projectPath)) {
    throw "Unreal project was not found: $projectPath"
}

Start-Process -FilePath $editorPath -ArgumentList "`"$projectPath`""
