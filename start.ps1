# 校招全流程助手 · 一键启动脚本
# 用法：在 ai-interview-coach 目录下执行  .\start.ps1
#
# 如果提示「在此系统上禁止运行脚本」，二选一即可：
#   1) 推荐：在项目目录下改用  .\start.cmd   （它已内置绕过执行策略，不改系统设置）
#   2) 或者：执行一次  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#      然后【新开一个 PowerShell 窗口】再运行（已打开的旧窗口仍会用旧策略，必须新开）
#
# 注意：本文件必须以 UTF-8 with BOM 保存。
# Windows PowerShell 5.1 对「无 BOM 的 UTF-8」会按系统 ANSI 码页解析，
# 中文会被截断并吃掉引号，导致「字符串缺少结束符」这类语法错误。

param(
    # 跳过依赖检查（依赖已确认就绪时可加快启动）
    [switch]$SkipDeps,
    # 后端端口。被别的项目占用时可换成其它端口并行运行，例如：
    #   .\start.cmd -BackendPort 8010 -FrontendPort 5183
    [int]$BackendPort = 8000,
    # 前端 dev（Vite）端口
    [int]$FrontendPort = 5173,
    # 不自动打开浏览器（远程桌面 / 服务器 / 自动化验证时使用）
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$backendUrl = "http://127.0.0.1:$BackendPort"
$frontendUrl = "http://127.0.0.1:$FrontendPort"

# 「这个端口上跑的确实是本项目」的判定标记（纯 ASCII）。
# 后端 /api/health 的 app_id 字段、前端 index.html 的 application-name meta 都是它。
# 为什么必须校验身份：同机并行的另一个 FastAPI 项目很可能同样默认监听
# 127.0.0.1:8000。只看「端口有人监听」或「HTTP 有响应」就会把它的页面
# 当成本项目的页面 —— 表现就是「启动后打开的却是另一个项目」。
# 为什么坚持 ASCII 而不是中文应用名：JSON 与 HTML 的响应头都不保证带 charset，
# Windows PowerShell 5.1 会按 Latin-1 解码 UTF-8 字节，中文比对必然失败（实测如此），
# 那会让「自己的后端反而认不出来」——比原问题更糟。ASCII 没有解码歧义。
$appId = 'ai_project'

# 自愈：Node.js 可能已安装、但当前窗口的 PATH 尚未刷新
# （Windows 改用户 PATH 后，从已打开的资源管理器/IDE 派生的新窗口仍用旧环境快照）。
# 主动把常见安装位置临时加入本会话 PATH，确保 npm/node 可被找到，无需重启资源管理器或注销。
foreach ($nd in @(
        'D:\app\nodejs',
        "$env:ProgramFiles\nodejs",
        "${env:ProgramFiles(x86)}\nodejs",
        "$env:LOCALAPPDATA\Programs\nodejs"
    )) {
    $npmAt = Join-Path $nd 'npm.cmd'
    if ((Test-Path $npmAt) -and ($env:PATH -notlike "*$nd*")) {
        $env:PATH = "$nd;$env:PATH"
    }
}

# ==== 启动脚本内部工具函数（可单独提取做回归测试，见 .regress/test_launcher.ps1）====

# 检查某个端口是否已被监听。
# 不用 Get-NetTCPConnection：在部分环境（CIM/WMI 受限）下它会**静默返回空**，
# 于是端口被判成空闲 → uvicorn 起不来 → 脚本却照样打开浏览器 → 看到别的项目。
# TcpClient 直连不依赖任何系统模块，结果只取决于内核。
function Test-PortBusy([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $port)
        $client.Close()
        return $true
    } catch {
        $client.Close()
        return $false
    }
}

# 查出占用端口的进程，便于用户判断「是我的服务」还是「别的程序」
function Get-PortOwner([int]$port) {
    $owner = 0
    $conn = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($conn.Count -gt 0) { $owner = $conn[0].OwningProcess }
    if (-not $owner) {
        # Get-NetTCPConnection 拿不到时退回 netstat 解析（同上，保证报错里有真正的 pid）
        $line = netstat -ano | Select-String -Pattern (":{0}\s+.*LISTENING" -f $port) | Select-Object -First 1
        if ($line) {
            $parts = @(($line.Line -split '\s+') | Where-Object { $_ })
            if ($parts.Count -ge 5) { $owner = [int]$parts[$parts.Count - 1] }
        }
    }
    if (-not $owner) { return '未知进程' }
    $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
    if (-not $proc) { return ('pid {0}' -f $owner) }
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue).CommandLine
    if ($cmd) { return ('{0}（pid {1}）：{2}' -f $proc.ProcessName, $owner, $cmd) }
    return ('{0}（pid {1}）' -f $proc.ProcessName, $owner)
}

# 这个端口上是不是「本项目的后端」：要求 /api/health 自报家门，而不只是「有响应」。
# PowerShell 5.1 里 Invoke-RestMethod 对非 2xx 会抛异常 → 被 catch 吃掉 → 返回 false，
# 因此「别的项目返回 404 / 405」不会被误判成本项目已就绪。
# 比对 app_id（ASCII）而不是 app（中文）：见文件顶部 $appId 的说明。
function Test-OurBackend([string]$url) {
    try {
        $h = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 3
        return ($h.app_id -eq $appId)
    } catch {
        return $false
    }
}

# 这个端口上是不是「本项目的前端页面」。
# dev 服务器与后端托管的 dist 都来自同一份 index.html，其中的 application-name 是 ASCII，
# 因此同一个判定通用，且不受响应头 charset 影响。
function Test-OurFrontend([string]$url) {
    try {
        $resp = Invoke-WebRequest -Uri $url -TimeoutSec 4 -UseBasicParsing
        return ($resp.Content -like "*$appId*")
    } catch {
        return $false
    }
}

# ==== 工具函数结束 ====

function Write-Step([string]$text) { Write-Host $text -ForegroundColor Yellow }
function Write-Ok([string]$text) { Write-Host $text -ForegroundColor Green }
function Write-Warn2([string]$text) { Write-Host $text -ForegroundColor DarkYellow }
function Write-Err([string]$text) { Write-Host $text -ForegroundColor Red }

Write-Host ''
Write-Host '=========================================' -ForegroundColor Cyan
Write-Host '  校招全流程助手 · 启动中' -ForegroundColor Cyan
Write-Host '=========================================' -ForegroundColor Cyan

# ---------- 依赖检查 ----------
if (-not $SkipDeps) {
    Write-Host ''
    Write-Step '[1/3] 检查 Python 依赖…'

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Err '      未找到 python。请先安装 Python 3.10+ 并勾选「Add to PATH」。'
        Write-Host '      下载：https://www.python.org/downloads/' -ForegroundColor DarkGray
        exit 1
    }

    Push-Location $backend
    try {
        # 直接导入应用入口：比逐个 import 更能覆盖真实依赖，缺包会立刻暴露。
        # 注意：这里必须临时把 ErrorActionPreference 降到 Continue，
        # 否则 PowerShell 5.1 会把 python 的 stderr 包装成 NativeCommandError，
        # 配合全局 Stop 会直接中断脚本，连「缺依赖→自动安装」都走不到。
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $importOut = & python -c "import app.main" 2>&1 | Out-String
        $pyOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEAP

        if ($pyOk) {
            Write-Ok '      Python 依赖就绪'
        } else {
            Write-Warn2 '      缺少依赖，正在安装 requirements.txt …'
            $lastLine = ($importOut -split "`n" | Where-Object { $_ -match '\S' } | Select-Object -Last 1).Trim()
            if ($lastLine) { Write-Host ("        原因：{0}" -f $lastLine) -ForegroundColor DarkGray }

            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & python -m pip install -r requirements.txt 2>&1 | Out-Null
            $pyOk = ($LASTEXITCODE -eq 0)
            $ErrorActionPreference = $prevEAP

            if (-not $pyOk) {
                Write-Err '      依赖安装失败，请手动执行： cd backend ; python -m pip install -r requirements.txt'
                exit 1
            }
            Write-Ok '      依赖安装完成'
        }
    } finally {
        Pop-Location
    }

    Write-Step '[2/3] 检查前端…'
    $distHtml = Join-Path $frontend 'dist\index.html'
    $hasDist = Test-Path $distHtml

    if ($hasDist) {
        # 已打包：后端单进程即可托管前端，无需 npm / node
        Write-Ok '      前端产物已就绪，使用单进程模式（无需 npm）'
    } else {
        # 未打包：必须靠 npm 先构建，否则后端没有页面可托管
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $npm) {
            Write-Err '      未找到 npm，且前端尚未构建。'
            Write-Host '      请安装 Node.js 18+（https://nodejs.org/），然后执行：' -ForegroundColor DarkGray
            Write-Host '        cd frontend ; npm install ; npm run build' -ForegroundColor DarkGray
            exit 1
        }
        if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
            Write-Warn2 '      首次运行，正在 npm install（可能需要 1-2 分钟）…'
            Push-Location $frontend
            try {
                $prevEAP = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                & npm.cmd install 2>&1 | Out-Null
                $npmOk = ($LASTEXITCODE -eq 0)
                $ErrorActionPreference = $prevEAP
                if (-not $npmOk) {
                    Write-Err '      npm install 失败，请手动执行： cd frontend ; npm install'
                    exit 1
                }
            } finally { Pop-Location }
        }
        Write-Warn2 '      正在构建前端产物（npm run build）…'
        Push-Location $frontend
        try {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & npm.cmd run build 2>&1 | Out-Null
            $buildOk = ($LASTEXITCODE -eq 0)
            $ErrorActionPreference = $prevEAP
            if (-not $buildOk) {
                Write-Err '      前端构建失败，请手动执行： cd frontend ; npm run build'
                exit 1
            }
            Write-Ok '      前端产物构建完成'
        } finally { Pop-Location }
    }
}

# ---------- 启动 ----------
Write-Step '[3/3] 启动服务…'

# 子窗口统一带 -ExecutionPolicy Bypass：npm 之类的 .ps1 包装脚本在受限策略下会失败
$suggestBackend = $BackendPort + 10
$suggestFrontend = $FrontendPort + 10

if (Test-PortBusy $BackendPort) {
    # 端口被占 + 身份校验通过 = 本项目后端已在运行，直接复用即可（重复运行脚本是安全的）
    if (Test-OurBackend $backendUrl) {
        Write-Ok '      后端已在运行，直接复用'
    } else {
        Write-Err ("      端口 {0} 已被占用，且它响应的不是本项目：{1}" -f $BackendPort, (Get-PortOwner $BackendPort))
        Write-Host '      最常见的来源：同机并行的另一个 FastAPI 项目（默认同样监听 8000）。' -ForegroundColor DarkGray
        Write-Host '      直接打开该端口只会看到那个项目，所以这里不会继续启动。请二选一：' -ForegroundColor DarkGray
        Write-Host '        1) 关闭那个程序（或结束上面列出的 pid）后重新运行本脚本；' -ForegroundColor DarkGray
        Write-Host ("        2) 给本项目换端口并行运行：  .\start.cmd -BackendPort {0} -FrontendPort {1}" -f $suggestBackend, $suggestFrontend) -ForegroundColor DarkGray
        exit 1
    }
} else {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command',
        "`$host.UI.RawUI.WindowTitle='校招全流程助手 · 后端 ($BackendPort)'; cd '$backend'; python -m uvicorn app.main:app --reload --port $BackendPort"
    )
}

# 前端 dev 服务器（热更新开发模式）：仅在 npm 可用时启动；否则靠后端单进程托管
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$devUrl = $null
if ($npm) {
    if (Test-PortBusy $FrontendPort) {
        if (Test-OurFrontend $frontendUrl) {
            Write-Ok ("      前端 dev（{0}）已在运行，直接复用" -f $FrontendPort)
            $devUrl = $frontendUrl
        } else {
            Write-Err ("      端口 {0} 被其它程序占用：{1}" -f $FrontendPort, (Get-PortOwner $FrontendPort))
            Write-Host '      它不是本项目的前端，因此不会复用（否则会打开别人的页面）。' -ForegroundColor DarkGray
            Write-Host ("      换端口：  .\start.cmd -FrontendPort {0}" -f ($FrontendPort + 1)) -ForegroundColor DarkGray
            Write-Host '      本次仍可用后端单进程页面（功能完整，只是没有热更新）。' -ForegroundColor DarkGray
        }
    } else {
        # 端口与代理目标都随参数走；strictPort 让 Vite 端口被占时直接报错，
        # 而不是悄悄改用 5174（那样脚本打印的地址和实际打开的页面会对不上）。
        Start-Process powershell -ArgumentList @(
            '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command',
            "`$host.UI.RawUI.WindowTitle='校招全流程助手 · 前端 ($FrontendPort)'; cd '$frontend'; `$env:VITE_DEV_PORT='$FrontendPort'; `$env:VITE_BACKEND_URL='$backendUrl'; npm.cmd run dev"
        )
        $devUrl = $frontendUrl
    }
} else {
    Write-Host '      未检测到 npm：跳过前端 dev，页面由后端单进程托管。' -ForegroundColor DarkGray
}

# ---------- 等待后端就绪并报告大模型状态 ----------
Write-Host ''
Write-Host '等待后端就绪…' -ForegroundColor DarkGray
$health = $null
$llm = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 1000
    try {
        $health = Invoke-RestMethod -Uri "$backendUrl/api/health" -TimeoutSec 3
        # 只认「自报家门」的响应：等待期间万一端口被别人抢走，也不会拿它的响应当本项目的就绪
        if ($health -and $health.app_id -ne $appId) { $health = $null }
        if ($health) { break }
    } catch {
        $health = $null
    }
}
if ($health) {
    try { $llm = Invoke-RestMethod -Uri "$backendUrl/api/health/llm" -TimeoutSec 5 } catch { $llm = $null }
}

Write-Host ''
Write-Host '=========================================' -ForegroundColor Green
Write-Host '  启动完成' -ForegroundColor Green
Write-Host '=========================================' -ForegroundColor Green
Write-Host ("  访问页面： {0}/" -f $backendUrl) -ForegroundColor White
if ($devUrl) {
    Write-Host ("  开发热更新： {0}" -f $devUrl) -ForegroundColor White
}
Write-Host ("  后端接口： {0}/docs" -f $backendUrl) -ForegroundColor White
Write-Host ''

if ($health) {
    if ($llm -and $llm.mode -eq 'live') {
        Write-Ok ("  大模型：已启用（{0} / {1}）" -f $llm.provider, $llm.model)
    } else {
        Write-Warn2 '  大模型：未启用，当前使用规则引擎（功能完整，但出题与评分不如大模型）'
        if ($llm -and $llm.hint) {
            Write-Host ("          原因：{0}" -f $llm.hint) -ForegroundColor DarkGray
        }
        Write-Host '          详见 http://127.0.0.1:8000/api/health/llm' -ForegroundColor DarkGray
    }
    Write-Host ("          语音：TTS {0} / ASR {1}" -f $health.tts_provider, $health.asr_provider) -ForegroundColor DarkGray
} else {
    Write-Warn2 '  后端未在 30 秒内就绪，请查看「校招全流程助手 · 后端」窗口里的报错信息。'
    # 不盲目打开浏览器：若该端口上现在是别人的服务，打开它只会看到另一个项目，
    # 这正是「启动后运行的却是别的项目」这个困惑的来源。
    if (Test-PortBusy $BackendPort) {
        Write-Err ("      注意：{0} 上响应的不是本项目：{1}" -f $BackendPort, (Get-PortOwner $BackendPort))
        Write-Host '      已跳过自动打开浏览器（打开也只会看到那个项目）。' -ForegroundColor DarkGray
        Write-Host ("      换端口重试：  .\start.cmd -BackendPort {0} -FrontendPort {1}" -f $suggestBackend, $suggestFrontend) -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '  首次使用建议：打开首页点击「一键体验示例简历」' -ForegroundColor DarkGray
Write-Host '  注意：关闭服务子窗口即可停止服务。' -ForegroundColor DarkGray
Write-Host ''

# 只有确认「本项目的后端」就绪时才自动打开页面
if ($health -and -not $NoBrowser) {
    Start-Process "$backendUrl/"
}
