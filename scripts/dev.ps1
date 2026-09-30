#Requires -Version 5.1
<#
    AiTester 一键启动（Windows PowerShell）
    用法：在仓库根执行  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1
    5173 被其他程序（如 QwenPaw）占用时：powershell ... -File scripts\dev.ps1 -FrontendPort 5175
    同时启动后端(uvicorn :8000)与前端(vite)，Ctrl+C 或按任意键停止，退出时清理整棵进程树。
#>

param(
    [int]$FrontendPort = 5173
)

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$backendPort = 8000
$frontendPort = $FrontendPort

function Get-PortOwner([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Assert-PortFree([int]$Port, [string]$Label) {
    $owner = Get-PortOwner -Port $Port
    if ($null -ne $owner) {
        $procName = (Get-Process -Id $owner.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Write-Host "[FAIL] $Label 需要的端口 $Port 已被占用（进程: $procName, PID: $($owner.OwningProcess)）" -ForegroundColor Red
        if ($Label -eq '前端') {
            Write-Host "       可用 -FrontendPort 指定其他端口，或停止占用进程。" -ForegroundColor Yellow
        } else {
            Write-Host "       请先停止该进程。" -ForegroundColor Yellow
        }
        exit 1
    }
}

function Stop-ProcessTree([int]$ProcId) {
    if ($ProcId -and (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) {
        & taskkill.exe /PID $ProcId /T /F 2>$null | Out-Null
    }
}

function Wait-Http([string]$Url, [int]$TimeoutSec, [string]$Label) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 | Out-Null
            Write-Host "[OK] $Label 已就绪: $Url" -ForegroundColor Green
            return $true
        } catch { Start-Sleep -Milliseconds 500 }
    }
    Write-Host "[FAIL] $Label 启动超时（$TimeoutSec 秒）: $Url" -ForegroundColor Red
    return $false
}

foreach ($cmd in 'uv', 'npm') {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "[FAIL] 未找到命令 $cmd，请先安装并加入 PATH" -ForegroundColor Red
        exit 1
    }
}

Assert-PortFree -Port $backendPort -Label '后端'
Assert-PortFree -Port $frontendPort -Label '前端'

Write-Host '== 安装依赖 ==' -ForegroundColor Cyan
Push-Location $backendDir
try {
    & uv sync
    if ($LASTEXITCODE -ne 0) { Write-Host '[FAIL] uv sync 失败' -ForegroundColor Red; exit 1 }
} finally {
    Pop-Location
}
if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
    Push-Location $frontendDir
    try {
        & npm install
        if ($LASTEXITCODE -ne 0) { Write-Host '[FAIL] npm install 失败' -ForegroundColor Red; exit 1 }
    } finally {
        Pop-Location
    }
}

Write-Host '== 启动前后端 ==' -ForegroundColor Cyan
$uvPath = (Get-Command uv).Source
$backend = Start-Process -FilePath $uvPath `
    -ArgumentList 'run', 'uvicorn', 'aitester.main:app', '--host', '127.0.0.1', '--port', $backendPort, '--reload' `
    -WorkingDirectory $backendDir -NoNewWindow -PassThru
$frontend = Start-Process -FilePath (Get-Command npm.cmd).Source `
    -ArgumentList 'run', 'dev', '--', '--port', $frontendPort `
    -WorkingDirectory $frontendDir -NoNewWindow -PassThru

try {
    $okBackend = Wait-Http -Url "http://127.0.0.1:$backendPort/api/health" -TimeoutSec 60 -Label '后端'
    $okFrontend = Wait-Http -Url "http://localhost:$frontendPort/" -TimeoutSec 120 -Label '前端'
    if (-not ($okBackend -and $okFrontend)) { throw '服务未就绪' }

    Write-Host ''
    Write-Host "AiTester 已就绪：打开 http://localhost:$frontendPort （Ctrl+C 或按任意键停止）" -ForegroundColor Green
    $keyPolling = $true
    while ($true) {
        if ($keyPolling) {
            try {
                if ([Console]::KeyAvailable) { [Console]::ReadKey($true) | Out-Null; break }
            } catch { $keyPolling = $false }
        }
        $alive = @(Get-Process -Id $backend.Id, $frontend.Id -ErrorAction SilentlyContinue)
        if ($alive.Count -lt 2) {
            Write-Host '检测到子进程已退出，进行清理...' -ForegroundColor Yellow
            break
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Stop-ProcessTree $backend.Id
    Stop-ProcessTree $frontend.Id
    Write-Host '前后端进程树已清理，脚本退出' -ForegroundColor Cyan
}
