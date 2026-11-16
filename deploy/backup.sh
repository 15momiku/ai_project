#!/usr/bin/env bash
# ============================================================
# AI面试模拟官 · 运行期数据备份脚本
# ============================================================
#
# 用法：
#   bash deploy/backup.sh                 备份到 deploy/backups/
#   bash deploy/backup.sh /mnt/backups    指定输出目录（建议指向另一块盘或对象存储挂载点）
#
# 备份内容：SQLite 全量业务数据 + 用户上传的简历原件。
#
# 为什么不直接 tar 打包 app.db：
#   服务在跑的时候 SQLite 正在写入，直接复制文件可能拿到**损坏的快照**
#   （这类备份在恢复时才发现坏，等于没有备份）。
#   本脚本优先用容器内的 sqlite3 在线备份 API 导出一致快照；
#   容器没在运行时（没有写入方）退化为普通复制，同样安全。
#
# backend/data/tts_cache（语音缓存）故意不备份：它只是缓存，
# 删掉会自动重新合成，备它只会让备份体积翻好几倍。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DATA_DIR="$ROOT/backend/data"
DB_FILE="$DATA_DIR/app.db"
OUT_DIR="${1:-$ROOT/deploy/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAP=".backup-$STAMP.db"

log() { printf '\033[32m[backup]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[backup]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$DB_FILE" ] || die "找不到数据库 $DB_FILE（还没部署过，或 DATABASE_URL 被改过？）"

if docker compose version >/dev/null 2>&1; then DC=(docker compose); else DC=(docker-compose); fi

mkdir -p "$OUT_DIR" "$DATA_DIR/uploads"

CID="$("${DC[@]}" ps -q app 2>/dev/null | head -1 || true)"
APP_RUNNING=0
if [ -n "${CID:-}" ] && [ "$(docker inspect --format '{{.State.Running}}' "$CID" 2>/dev/null || echo false)" = "true" ]; then
  APP_RUNNING=1
fi

if [ "$APP_RUNNING" = 1 ]; then
  log "服务在运行：用 SQLite 在线备份 API 导出一致快照（不中断服务）"
  "${DC[@]}" exec -T app python - "$SNAP" <<'PY'
import pathlib
import sqlite3
import sys

snap = pathlib.Path("/app/backend/data") / sys.argv[1]
src = sqlite3.connect("/app/backend/data/app.db")
dst = sqlite3.connect(str(snap))
with dst:
    src.backup(dst)
dst.close()
src.close()
print(f"[backup] sqlite 快照完成：{snap}")
PY
else
  log "服务未在运行：没有写入方，直接复制数据库文件同样安全"
  cp -p "$DB_FILE" "$DATA_DIR/$SNAP"
fi

ARCHIVE="$OUT_DIR/ai-interview-coach-$STAMP.tar.gz"
log "打包快照与用户上传原件 → $ARCHIVE"
tar -czf "$ARCHIVE" -C "$DATA_DIR" "$SNAP" uploads
rm -f "$DATA_DIR/$SNAP"

log "完成：$ARCHIVE（$(du -h "$ARCHIVE" | cut -f1)）"
cat <<EOF

恢复方法（会覆盖当前数据，先停服再操作）：
  ${DC[*]} down
  tar -xzf "$ARCHIVE" -C backend/data
  mv backend/data/$SNAP backend/data/app.db
  bash deploy/deploy.sh --no-build

提醒：只把备份留在本机等于没备份 —— 请定期同步到对象存储（阿里云 OSS / 腾讯云 COS），
      或者至少换一台机器存。服务器整机故障时，同机备份没有任何意义。
EOF
