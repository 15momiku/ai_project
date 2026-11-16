#!/usr/bin/env bash
# ============================================================
# AI面试模拟官 · 服务器端部署 / 更新脚本
# ============================================================
#
# 在服务器上、仓库根目录执行：
#   bash deploy/deploy.sh             构建 + 启动（幂等，可反复执行）
#   bash deploy/deploy.sh --pull      先 git pull 再构建启动（日常更新用这个）
#   bash deploy/deploy.sh --no-build  只重启容器，不重新构建镜像
#
# 脚本依次完成：
#   1. 检查 docker / docker compose
#   2. 检查 backend/.env 是否存在 —— 不存在就直接退出，绝不带着空密钥上线
#   3. 准备持久化目录并修正属主（容器内以 UID 10001 非 root 运行）
#   4. 构建镜像、启动容器
#   5. 等健康检查通过并打印访问地址；失败则自动打出最后 60 行日志
#
# 完整部署流程见仓库根目录 DEPLOY.md。

set -euo pipefail

DO_PULL=0
DO_BUILD=1

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --pull)     DO_PULL=1 ;;
    --no-build) DO_BUILD=0 ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "未知参数：$arg（可用：--pull / --no-build / --help）" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DATA_DIR="$ROOT/backend/data"
ENV_FILE="$ROOT/backend/.env"
APP_UID=10001

log()  { printf '\033[32m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[deploy]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 1. 环境检查 ----------
command -v docker >/dev/null 2>&1 \
  || die "未安装 docker。安装命令：curl -fsSL https://get.docker.com | sh"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  die "没有 docker compose 插件。用官方脚本重装 docker 会一并装上：curl -fsSL https://get.docker.com | sh"
fi
log "编排命令：${DC[*]}"

# ---------- 2. 生产配置检查 ----------
if [ ! -f "$ENV_FILE" ]; then
  cat >&2 <<EOF
[deploy] 找不到 $ENV_FILE
         脚本不会用空配置启动服务 —— 线上带空密钥跑起来，排查起来比现在直接失败麻烦得多。

  最快的方式（把本机那套已经跑通的配置传上来）：
      scp backend/.env root@<服务器IP>:$ENV_FILE

  或者按模板逐项填：
      cp backend/.env.production.example backend/.env
      vi backend/.env
EOF
  exit 1
fi
log "生产配置：$ENV_FILE"

# 只检查「有没有」，绝不打印密钥内容
if grep -Eq '^(DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|ZHIPU_API_KEY|MOONSHOT_API_KEY|OPENAI_API_KEY)=.+' "$ENV_FILE"; then
  log "已检测到大模型密钥"
else
  warn "未检测到任何大模型密钥 —— 线上会退化成规则引擎演示模式（能跑通，但不是真 AI 出题与点评）"
fi

# ---------- 3. 持久化目录与属主 ----------
mkdir -p "$DATA_DIR/uploads" "$DATA_DIR/tts_cache"
if ! chown -R "$APP_UID:$APP_UID" "$DATA_DIR" 2>/dev/null; then
  warn "chown 失败（当前不是 root？）—— 容器可能写不进 $DATA_DIR，上传与数据库会报错"
  warn "请用 root 执行本脚本，或手动执行：chown -R $APP_UID:$APP_UID $DATA_DIR"
fi
log "持久化目录就绪：$DATA_DIR"

# ---------- 4. 可选：拉取最新代码 ----------
if [ "$DO_PULL" = 1 ]; then
  if [ -d "$ROOT/.git" ]; then
    log "拉取最新代码：git pull --ff-only"
    git pull --ff-only
  else
    warn "当前目录不是 git 仓库，跳过 --pull"
  fi
fi

# ---------- 5. 构建 + 启动 ----------
if [ "$DO_BUILD" = 1 ]; then
  log "构建镜像（首次约 3–8 分钟，取决于服务器带宽与配置）..."
  "${DC[@]}" build
else
  log "跳过镜像构建（--no-build）"
fi

log "启动容器..."
"${DC[@]}" up -d --remove-orphans

# ---------- 6. 等健康检查 ----------
log "等待后端健康检查（最多 90 秒）..."
CID="$("${DC[@]}" ps -q app 2>/dev/null | head -1 || true)"
[ -n "${CID:-}" ] || die "拿不到 app 容器 ID。请执行：${DC[*]} logs --tail=100 app 查看原因"

STATUS="unknown"
for _ in $(seq 1 45); do
  STATUS="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID" 2>/dev/null || echo unknown)"
  [ "$STATUS" = "healthy" ] && break
  # 容器已经退出就不用再等了
  if [ "$(docker inspect --format '{{.State.Running}}' "$CID" 2>/dev/null || echo false)" != "true" ]; then
    STATUS="exited"
    break
  fi
  sleep 2
done

if [ "$STATUS" != "healthy" ]; then
  warn "健康检查未通过（当前状态：$STATUS）"
  echo "---------------- app 日志（最后 60 行）----------------"
  "${DC[@]}" logs --tail=60 app || true
  echo "-------------------------------------------------------"
  die "部署失败。上面的日志通常能直接指出原因；对照 DEPLOY.md 的「排错对照表」。"
fi

log "后端健康检查通过"
echo
"${DC[@]}" ps
echo

# ---------- 7. 打印访问地址 ----------
SITE="$(grep -E '^SITE_ADDRESS=' "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
SITE="$(printf '%s' "${SITE:-}" | tr -d '\r' | xargs || true)"

if [ -n "${SITE:-}" ] && [ "$SITE" != ":80" ]; then
  log "访问地址：https://$SITE/"
  log "首次访问时 Caddy 会去申请证书，可能要等几秒到十几秒；没出来就看：${DC[*]} logs caddy"
else
  IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  log "访问地址：http://${IP:-<服务器公网IP>}/"
  warn "当前是 HTTP 模式：页面、登录、简历解析都能用，但浏览器不会授予麦克风权限，"
  warn "语音作答与录音回放不可用（这是浏览器的安全策略，不是程序问题）。"
  warn "正式对外请在仓库根目录 .env 里写 SITE_ADDRESS=你的域名，然后执行：${DC[*]} up -d caddy"
fi

echo
log "看日志：${DC[*]} logs -f app"
log "备份数据：bash deploy/backup.sh"
