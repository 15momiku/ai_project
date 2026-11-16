# ============================================================
# 校招全流程助手 · 生产镜像（多阶段构建）
# ============================================================
#
# 构建：docker build -t ai-interview-coach:latest .
# 推荐用仓库根目录的 docker-compose.yml 构建并运行（见 DEPLOY.md）。
#
# 为什么只需要一个容器：
#   后端 app/main.py 在启动时若发现 frontend/dist/index.html 存在，会自动把 SPA
#   挂到 catch-all 路由上（单进程模式）。所以生产环境 = 「构建好的前端产物
#   + FastAPI 进程」，不需要额外的 Nginx 托管静态文件。
#
# 两个阶段：
#   ① frontend-build：Node 里跑 vite build，产出纯静态文件
#   ② runtime        ：Python 3.11 + 依赖 + 前端产物，uvicorn 单 worker 起服务
#
# 注意：没有写 `# syntax=docker/dockerfile:1`，避免构建时额外去 Docker Hub
# 拉 frontend 镜像（国内服务器上这一步容易卡住）。本文件用到的多阶段 / ARG /
# COPY --from 都是经典构建器与 BuildKit 都支持的语法。

# ============================================================
# 阶段 ①：构建前端静态产物（Vite + React）
# ============================================================
FROM node:20-bookworm-slim AS frontend-build

WORKDIR /build/frontend

# 国内服务器从 npmjs.org 拉包很慢，默认走 npmmirror。
# 海外服务器可覆盖：--build-arg NPM_REGISTRY=https://registry.npmjs.org
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry "${NPM_REGISTRY}" \
 && npm config set fund false \
 && npm config set audit false

# 先只拷依赖清单：之后改业务代码不会让「装依赖」这一层缓存失效
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 拷源码构建（node_modules / dist 由 .dockerignore 排除，不会覆盖上面装好的目录）
COPY frontend/ ./
RUN npm run build

# ============================================================
# 阶段 ②：运行时（Python + FastAPI + 前端产物）
# ============================================================
FROM python:3.11-slim-bookworm AS runtime

# 是否安装「简历 PDF 导出」工具链（wkhtmltopdf + 中文字体 + xvfb，约 400MB）。
#   1（默认）= 功能完整，/api/resume/export/pdf 可用
#   0        = 镜像小很多，但该接口返回 503，其余功能不受影响
# 覆盖方式：docker compose build --build-arg INSTALL_PDF_TOOLCHAIN=0
ARG INSTALL_PDF_TOOLCHAIN=1

# 是否额外安装阿里云语音 SDK（仅 ASR_PROVIDER=aliyun 的 WebSocket 链路需要，
# TTS 不需要——它走 RESTful HTTP）。默认 0，与 backend/requirements.txt 的
# 默认口径一致：这个 SDK 会连带装 oss2 / matplotlib，镜像明显变大。
# 覆盖方式：docker compose build --build-arg INSTALL_NLS_SDK=1
ARG INSTALL_NLS_SDK=0

# 国内服务器走清华 PyPI 镜像；海外服务器可覆盖：
#   --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Debian apt 源。默认用官方源（海外服务器最快）。
# 国内服务器如果卡在 apt-get update，换成国内镜像重试即可：
#   --build-arg DEBIAN_MIRROR=mirrors.aliyun.com
ARG DEBIAN_MIRROR=deb.debian.org

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    WKHTMLTOPDF_PATH=/usr/local/bin/wkhtmltopdf

# 系统依赖说明：
#   wkhtmltopdf / xvfb / xauth —— PDF 导出（详见下方包装脚本）
#   fonts-noto-cjk            —— 中文字体，缺了导出的简历 PDF 里中文会变方块
#   libjpeg62-turbo / libxrender1 / libxext6 / libfontconfig1 —— wkhtmltopdf 运行库
#   curl                      —— 容器 HEALTHCHECK 用
RUN set -eux; \
    if [ "$DEBIAN_MIRROR" != "deb.debian.org" ]; then \
        if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
            sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
        fi; \
        if [ -f /etc/apt/sources.list ]; then \
            sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list; \
        fi; \
        echo "已切换 Debian apt 源为：${DEBIAN_MIRROR}"; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        curl; \
    if [ "$INSTALL_PDF_TOOLCHAIN" = "1" ]; then \
        apt-get install -y --no-install-recommends \
            wkhtmltopdf \
            xvfb \
            xauth \
            fonts-noto-cjk \
            libjpeg62-turbo \
            libxrender1 \
            libxext6 \
            libfontconfig1; \
    fi; \
    rm -rf /var/lib/apt/lists/*

# Debian 的 wkhtmltopdf 是「非 patched Qt」构建，没有 X 显示时会直接报
#   QXcbConnection: Could not connect to display
# 而生成不出 PDF。app/services/resume_doc.py 的查找顺序是
#   环境变量 WKHTMLTOPDF_PATH → /usr/local/bin/wkhtmltopdf → /usr/bin/wkhtmltopdf → PATH
# 因此这里在 /usr/local/bin 放一层 xvfb-run 包装脚本即可无头运行，
# **不需要改动任何业务代码**（xvfb-run 需要 xauth，上面已装）。
RUN if [ "$INSTALL_PDF_TOOLCHAIN" = "1" ]; then \
        printf '%s\n' '#!/bin/sh' 'exec xvfb-run -a /usr/bin/wkhtmltopdf "$@"' \
            > /usr/local/bin/wkhtmltopdf; \
        chmod 0755 /usr/local/bin/wkhtmltopdf; \
        if /usr/local/bin/wkhtmltopdf --version >/dev/null 2>&1; then \
            echo "[docker build] PDF 工具链自检通过（wkhtmltopdf 可在无头环境运行）"; \
        else \
            echo "[docker build] 提示：wkhtmltopdf 自检未通过，PDF 导出可能不可用（其余功能不受影响）"; \
        fi; \
    else \
        echo "[docker build] 已跳过 PDF 工具链（INSTALL_PDF_TOOLCHAIN=0）"; \
    fi

WORKDIR /app

# ---- Python 依赖（单列一层，改业务代码不会触发重装）----
COPY backend/requirements.txt /app/backend/requirements.txt
RUN set -eux; \
    pip install --index-url "${PIP_INDEX_URL}" -r /app/backend/requirements.txt; \
    if [ "$INSTALL_NLS_SDK" = "1" ]; then \
        pip install --index-url "${PIP_INDEX_URL}" 'alibabacloud-nls-python-sdk>=1.0.2'; \
    fi

# ---- 业务代码 ----
# backend/.env 与 backend/data/ 由 .dockerignore 排除：
# 密钥绝不能进镜像，运行期数据必须留在宿主机的持久化目录里。
COPY backend/ /app/backend/

# ---- 前端产物：后端会自动挂载 /app/frontend/dist ----
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

# 非 root 运行。数据目录属主在构建期就设好（UID 10001）：
# docker-compose.yml 会把宿主机的 backend/data 绑定挂载到这里，
# 所以宿主机目录的属主也必须是 10001 —— deploy/deploy.sh 每次都会自动 chown，
# 手动执行 docker compose 的话请先跑一次：
#   mkdir -p backend/data && chown -R 10001:10001 backend/data
RUN set -eux; \
    groupadd --gid 10001 app; \
    useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home --shell /usr/sbin/nologin app; \
    mkdir -p /app/backend/data/uploads /app/backend/data/tts_cache; \
    chown -R 10001:10001 /app

USER 10001:10001

WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# --workers 1 是硬性要求，不是性能取舍：
#   · 游客数据存在**进程内**内存库（app/db.py），多 worker 会让同一游客落到不同库，
#     表现为「刚答的题不见了」；
#   · SQLite 单文件写入也不适合多进程并发。
# --proxy-headers / --forwarded-allow-ips * ：容器前面是 Caddy，
#   不信任转发头的话后端日志与统计里全是 Caddy 的内网 IP。
# 这样做是安全的：app 服务只在 compose 内网暴露，公网无法直连 8000。
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
