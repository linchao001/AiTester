#Requires -Version 5.1
<#
    AiTester 一键启动（Windows PowerShell）
    用法：在仓库根执行  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1
    同时启动后端(uvicorn :8000)与前端(vite)，Ctrl+C 或按任意键停止，退出时清理整棵进程树。
    端口上若残留本项目上一次启动的服务进程，会先结束其进程树再启动；
    被非本项目进程占用时不自动结束（避免误杀第三方程序），此时用 -FrontendPort 换端口：
    powershell ... -File scripts\dev.ps1 -FrontendPort 5175
#>

param(
    [int]$FrontendPort = 5173
)

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$backendPort = 8000
$frontendPort = $FrontendPort
# 只认本项目「服务」的路径：backend/ 与 frontend/ 下的进程、以及 dev.ps1 自身派生的进程树。
# 故意不含仓库根，避免把 prototype\serve.js（8899 走查用原型服务）等本项目其它进程误杀。
$ownMarkers = @(
    $backendDir,
    $frontendDir,
    (Join-Path (Join-Path $root 'scripts') 'dev.ps1')
) | ForEach-Object { [string]$_ } | Where-Object { $_ } |
    ForEach-Object { $_.ToLower().Replace('/', '\') }

function Get-PortOwner([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { [int]$_ }
}

function Get-ProcSnapshot {
    $map = @{}
    # CIM 的 ProcessId/ParentProcessId 是 UInt32，必须归一为 Int32 才能用 int 键查到。
    foreach ($p in @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue)) {
        $key = [int]$p.ProcessId
        if (-not $map.ContainsKey($key)) {
            $map[$key] = [pscustomobject]@{ ProcessId = $key; ParentProcessId = [int]$p.ParentProcessId; Name = $p.Name; CommandLine = $p.CommandLine }
        }
    }
    return $map
}

# 端口持有者可能是 uvicorn --reload 派生的子 python（其命令行不含项目路径），故向上追溯祖先进程。
function Test-OurService([int]$ProcId, $ByPid) {
    $cur = $ByPid[$ProcId]
    for ($depth = 0; $depth -lt 8 -and $null -ne $cur; $depth++) {
        $cl = [string]$cur.CommandLine
        $cl = $cl.ToLower().Replace('/', '\')
        foreach ($marker in $ownMarkers) {
            if ($cl.Contains($marker)) { return $true }
        }
        $cur = $ByPid[$cur.ParentProcessId]
    }
    return $false
}

# PID 会被 Windows 复用，退出清理时不能按记录 PID 直接杀（可能已是新实例的进程）。
# 以「本 dev.ps1 实例的后代」为准，从当前进程快照的父子关系向下遍历。
function Get-MyProcessIds {
    $kids = @{}
    foreach ($p in @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue)) {
        $parentKey = [int]$p.ParentProcessId
        if (-not $kids.ContainsKey($parentKey)) { $kids[$parentKey] = New-Object System.Collections.ArrayList }
        [void]$kids[$parentKey].Add([int]$p.ProcessId)
    }
    $mine = New-Object System.Collections.ArrayList
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue($PID)
    while ($queue.Count -gt 0) {
        foreach ($kid in $kids[[int]$queue.Dequeue()]) {
            [void]$mine.Add($kid)
            $queue.Enqueue($kid)
        }
    }
    return $mine.ToArray()
}
# 循环判定，直到端口空闲。上一轮 dev.ps1 的看门狗可能正在并发拆树，
# 会出现「套接字仍 Listen 但属主 PID 已退出」的瞬时状态，故每轮重取快照。
function Clear-Port([int]$Port, [string]$Label) {
    $deadline = (Get-Date).AddSeconds(30)
    $killed = $false
    while ($true) {
        $owners = @(Get-PortOwner -Port $Port)
        if ($owners.Count -eq 0) {
            if ($killed) { Write-Host "[OK] $Label 端口 $Port 残留进程已清理" -ForegroundColor Green }
            return
        }
        $byPid = Get-ProcSnapshot
        foreach ($ownerId in $owners) {
            $proc = $byPid[$ownerId]
            if ($null -eq $proc -or -not (Get-Process -Id $ownerId -ErrorAction SilentlyContinue)) { continue }
            if (Test-OurService -ProcId $ownerId -ByPid $byPid) {
                Write-Host "[..] 端口 $Port 残留 $Label 服务，结束进程树：$($proc.Name) (PID: $ownerId)" -ForegroundColor Yellow
                Stop-ProcessTree $ownerId
                $killed = $true
            } else {
                Write-Host "[FAIL] $Label 需要的端口 $Port 被非本项目进程占用（$($proc.Name), PID: $ownerId）" -ForegroundColor Red
                if ($Label -eq '前端') {
                    Write-Host '       为避免误杀第三方程序不会自动结束它，可用 -FrontendPort 指定其他端口。' -ForegroundColor Yellow
                } else {
                    Write-Host '       为避免误杀第三方程序不会自动结束它，请先手工停止该进程。' -ForegroundColor Yellow
                }
                exit 1
            }
        }
        if ((Get-Date) -ge $deadline) {
            Write-Host "[FAIL] $Label 端口 $Port 占用进程消失后 30 秒内仍未释放" -ForegroundColor Red
            exit 1
        }
        Start-Sleep -Milliseconds 500
    }
}

function Stop-ProcessTree([int]$ProcId) {
    if ($ProcId -and (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) {
        & taskkill.exe /PID $ProcId /T /F 2>$null | Out-Null
    }
}

# 退出清理：只结束本实例派生的进程。记录的包装进程若已被复用则跳过，
# 改由「仍由本实例持有的端口属主」兜底（taskkill /T 会带走整棵子树）。
function Stop-OwnServices {
    $mine = @(Get-MyProcessIds)
    $targets = @()
    foreach ($rec in @($backend, $frontend)) {
        if ($null -ne $rec) {
            $recId = [int]$rec.Id
            if ($mine -contains $recId) { $targets += $recId }
        }
    }
    foreach ($port in @($backendPort, $frontendPort)) {
        foreach ($owner in @(Get-PortOwner -Port $port)) {
            if ($mine -contains $owner) { $targets += $owner }
        }
    }
    foreach ($target in ($targets | Select-Object -Unique)) { Stop-ProcessTree $target }
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

Clear-Port -Port $backendPort -Label '后端'
Clear-Port -Port $frontendPort -Label '前端'

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
    Stop-OwnServices
    Write-Host '前后端进程树已清理，脚本退出' -ForegroundColor Cyan
}
