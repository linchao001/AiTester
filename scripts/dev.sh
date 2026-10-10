#!/usr/bin/env bash
# AiTester 一键安装并启动（macOS / Linux / WSL / Git Bash）
# 用法（仓库根或任意目录均可）：
#   bash scripts/dev.sh
#   FRONTEND_PORT=5175 bash scripts/dev.sh
# 若本机缺少 uv / Node.js，会先自动安装再拉起前后端。
# Ctrl+C 停止并清理本脚本拉起的进程树。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
BACKEND_PORT=8000
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

BACKEND_PID=""
FRONTEND_PID=""

log()  { printf '%s\n' "$*"; }
ok()   { printf '[OK] %s\n' "$*"; }
info() { printf '[..] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

refresh_path() {
  # 官方 uv 安装目录 + 常见 Node 路径
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
  if [[ -d /opt/homebrew/bin ]]; then
    export PATH="/opt/homebrew/bin:$PATH"
  fi
  # nvm / fnm（若已装）
  if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    . "$HOME/.nvm/nvm.sh"
  fi
  if command -v fnm >/dev/null 2>&1; then
    eval "$(fnm env)"
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

ensure_uv() {
  refresh_path
  if have uv; then
    ok "已找到 uv: $(command -v uv)"
    return
  fi
  info "未找到 uv，正在安装（官方安装脚本）..."
  if ! have curl; then
    fail "需要 curl 才能安装 uv，请先安装 curl"
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  refresh_path
  if ! have uv; then
    fail "uv 已安装但当前会话仍找不到命令，请重新打开终端后再试（确认 ~/.local/bin 在 PATH）"
  fi
  ok "uv 安装完成: $(command -v uv)"
}

ensure_node() {
  refresh_path
  if have node && have npm; then
    ok "已找到 Node.js $(node -v) / npm $(npm -v)"
    return
  fi
  info "未找到 Node.js / npm，尝试自动安装..."

  if have brew; then
    info "使用 Homebrew 安装 node..."
    brew install node
  elif have apt-get; then
    info "使用 apt 安装 nodejs npm..."
    if have sudo; then
      sudo apt-get update -y
      sudo apt-get install -y nodejs npm
    else
      fail "需要 sudo 才能用 apt 安装 Node.js，请先安装 Node.js LTS：https://nodejs.org/"
    fi
  elif have dnf; then
    info "使用 dnf 安装 nodejs npm..."
    if have sudo; then
      sudo dnf install -y nodejs npm
    else
      fail "需要 sudo 才能用 dnf 安装 Node.js，请先安装 Node.js LTS：https://nodejs.org/"
    fi
  elif have yum; then
    info "使用 yum 安装 nodejs npm..."
    if have sudo; then
      sudo yum install -y nodejs npm
    else
      fail "需要 sudo 才能用 yum 安装 Node.js，请先安装 Node.js LTS：https://nodejs.org/"
    fi
  elif have pacman; then
    info "使用 pacman 安装 nodejs npm..."
    if have sudo; then
      sudo pacman -Sy --noconfirm nodejs npm
    else
      fail "需要 sudo 才能用 pacman 安装 Node.js，请先安装 Node.js LTS：https://nodejs.org/"
    fi
  else
    fail "无法自动安装 Node.js（未找到 brew/apt/dnf/yum/pacman）。请安装 Node.js LTS：https://nodejs.org/"
  fi

  refresh_path
  if ! have node || ! have npm; then
    fail "Node.js 已安装但当前会话仍找不到 node/npm，请重新打开终端后再试"
  fi
  ok "Node.js 安装完成: $(node -v) / npm $(npm -v)"
}

ensure_env_file() {
  local example="$BACKEND_DIR/.env.example"
  local envfile="$BACKEND_DIR/.env"
  if [[ -f "$example" && ! -f "$envfile" ]]; then
    cp "$example" "$envfile"
    ok "已从 backend/.env.example 生成 backend/.env（可按需填写种子 Key）"
  fi
}

# 返回监听某端口的 PID 列表（空则无）
port_owners() {
  local port="$1"
  if have lsof; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true
  elif have ss; then
    ss -ltnp "sport = :$port" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u || true
  elif have fuser; then
    fuser -n tcp "$port" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
  else
    return 0
  fi
}

# 判断 PID 是否属于本项目服务（命令行含 backend/frontend 路径）
is_our_service() {
  local pid="$1"
  local cmd
  cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ -z "$cmd" ]] && return 1
  case "$cmd" in
    *"$BACKEND_DIR"*|*"$FRONTEND_DIR"*|*"scripts/dev.sh"*) return 0 ;;
  esac
  # 向上追溯父进程（uvicorn --reload 子进程可能不含路径）
  local ppid cur="$pid" depth=0
  while [[ $depth -lt 8 ]]; do
    ppid="$(ps -p "$cur" -o ppid= 2>/dev/null | tr -d ' ' || true)"
    [[ -z "$ppid" || "$ppid" == "0" || "$ppid" == "1" ]] && break
    cmd="$(ps -p "$ppid" -o args= 2>/dev/null || true)"
    case "$cmd" in
      *"$BACKEND_DIR"*|*"$FRONTEND_DIR"*|*"scripts/dev.sh"*) return 0 ;;
    esac
    cur="$ppid"
    depth=$((depth + 1))
  done
  return 1
}

kill_tree() {
  local pid="$1"
  [[ -z "$pid" ]] && return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  # 先杀子进程再杀自身
  local kids
  kids="$(ps -o pid= --ppid "$pid" 2>/dev/null || true)"
  local kid
  for kid in $kids; do
    kill_tree "$kid"
  done
  kill -TERM "$pid" 2>/dev/null || true
  sleep 0.2
  kill -KILL "$pid" 2>/dev/null || true
}

clear_port() {
  local port="$1" label="$2"
  local deadline=$((SECONDS + 30))
  local killed=0
  while true; do
    local owners
    owners="$(port_owners "$port")"
    if [[ -z "${owners//[$'\t\r\n ']/}" ]]; then
      if [[ $killed -eq 1 ]]; then
        ok "$label 端口 $port 残留进程已清理"
      fi
      return 0
    fi
    local oid
    for oid in $owners; do
      if ! kill -0 "$oid" 2>/dev/null; then
        continue
      fi
      local name
      name="$(ps -p "$oid" -o comm= 2>/dev/null || echo unknown)"
      if is_our_service "$oid"; then
        info "端口 $port 残留 $label 服务，结束进程树：$name (PID: $oid)"
        kill_tree "$oid"
        killed=1
      else
        fail "$label 需要的端口 $port 被非本项目进程占用（$name, PID: $oid）。为避免误杀不会自动结束；前端可用 FRONTEND_PORT=xxxx 换端口。"
      fi
    done
    if [[ $SECONDS -ge $deadline ]]; then
      fail "$label 端口 $port 占用进程消失后 30 秒内仍未释放"
    fi
    sleep 0.5
  done
}

wait_http() {
  local url="$1" timeout_sec="$2" label="$3"
  local deadline=$((SECONDS + timeout_sec))
  while [[ $SECONDS -lt $deadline ]]; do
    if have curl; then
      if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
        ok "$label 已就绪: $url"
        return 0
      fi
    elif have wget; then
      if wget -q -T 2 -O /dev/null "$url" 2>/dev/null; then
        ok "$label 已就绪: $url"
        return 0
      fi
    else
      # 无 curl/wget 时退化为端口监听检测
      local port_guess
      port_guess="$(printf '%s' "$url" | sed -n 's/.*:\([0-9][0-9]*\).*/\1/p')"
      if [[ -n "$port_guess" && -n "$(port_owners "$port_guess" | head -n1)" ]]; then
        ok "$label 端口已监听（未检测到 curl/wget，跳过 HTTP 校验）: $url"
        return 0
      fi
    fi
    sleep 0.5
  done
  log "[FAIL] $label 启动超时（${timeout_sec} 秒）: $url"
  return 1
}

cleanup() {
  info "正在清理前后端进程树..."
  [[ -n "$BACKEND_PID" ]] && kill_tree "$BACKEND_PID"
  [[ -n "$FRONTEND_PID" ]] && kill_tree "$FRONTEND_PID"
  # 兜底：清理仍占端口且属于本项目的进程
  local p oid
  for p in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    for oid in $(port_owners "$p"); do
      if is_our_service "$oid"; then
        kill_tree "$oid"
      fi
    done
  done
  ok "前后端进程树已清理，脚本退出"
}

trap cleanup EXIT INT TERM

log "== 检查 / 安装运行环境 =="
ensure_uv
ensure_node
ensure_env_file

clear_port "$BACKEND_PORT" "后端"
clear_port "$FRONTEND_PORT" "前端"

log "== 安装项目依赖 =="
(
  cd "$BACKEND_DIR"
  # uv 可按需拉取 Python>=3.11，无需本机预装
  uv sync
)
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  (
    cd "$FRONTEND_DIR"
    npm install
  )
else
  ok "前端 node_modules 已存在，跳过 npm install"
fi

log "== 启动前后端 =="
(
  cd "$BACKEND_DIR"
  uv run uvicorn aitester.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload
) &
BACKEND_PID=$!

(
  cd "$FRONTEND_DIR"
  npm run dev -- --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

ok_backend=0
ok_frontend=0
if wait_http "http://127.0.0.1:${BACKEND_PORT}/api/health" 60 "后端"; then
  ok_backend=1
fi
if wait_http "http://localhost:${FRONTEND_PORT}/" 120 "前端"; then
  ok_frontend=1
fi
if [[ $ok_backend -ne 1 || $ok_frontend -ne 1 ]]; then
  fail "服务未就绪"
fi

log ""
ok "AiTester 已就绪：打开 http://localhost:${FRONTEND_PORT} （Ctrl+C 停止）"

# 等待任一子进程退出
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 0.5
done
info "检测到子进程已退出，进行清理..."
