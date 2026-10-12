$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    Write-Host '请先按 docs/A第一轮使用与验收.md 创建 .venv 并安装页面依赖。'
    exit 1
}
& $taskPython -m streamlit run app/main.py --server.address 127.0.0.1 --browser.gatherUsageStats false
