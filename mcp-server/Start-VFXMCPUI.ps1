$ErrorActionPreference = "Stop"

$serverRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = Split-Path -Parent $serverRoot

Push-Location $workspaceRoot
try {
    py mcp-server/server.py ui --host 127.0.0.1 --port 8765
}
finally {
    Pop-Location
}
