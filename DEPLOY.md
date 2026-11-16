# 云端部署手册 · 阿里云 / 腾讯云 轻量应用服务器 + Docker

> 目标：把这套「校招全流程助手」挂到公网，别人用手机/电脑打开就能用。
> 全程约 40–60 分钟（不含域名 ICP 备案，备案通常要 1–2 周且必须提前开始）。
> 本文所有命令都可以直接复制执行；`<尖括号>` 里的内容需要替换成你自己的值。

---

## 0. 先看这三条硬约束（它们决定了后面每一步为什么要这么做）

| 约束 | 原因（代码事实） | 违反后的症状 |
| --- | --- | --- |
| **必须挂持久化磁盘** | SQLite 数据库 `backend/data/app.db`、用户上传的简历原件 `backend/data/uploads/` 都写在本地磁盘 | 用 Vercel / 云函数这类无盘平台：一重启用户数据全丢 |
| **必须单实例 + 单进程（1 worker）** | 游客数据走**进程内**内存库（`app/db.py`）；SQLite 也是单写者 | 多副本或多 worker：游客「刚答的题不见了」、数据库偶发写入锁 |
| **正式对外必须有 HTTPS** | 语音作答用浏览器麦克风（`getUserMedia` / `MediaRecorder`），浏览器只在 HTTPS 或 localhost 下给权限 | 页面能打开、登录也正常，但一录音就提示「拿不到麦克风接口」 |

结论：**一台带磁盘的小服务器 + Docker + Caddy 自动 HTTPS**，就是为这三条约束量身定做的方案。
（Caddy 是唯一多出来的组件，它专门解决第 3 条：自动申请并续期 Let's Encrypt 证书，不用你手动管证书。）

---

## 1. 开工前的准备清单

| 项目 | 建议 | 必需 |
| --- | --- | --- |
| 服务器 | 轻量应用服务器 / 2 核 2G / 系统盘 ≥ 40GB / Ubuntu 22.04（或直接选 Docker 应用镜像） | ✅ |
| 地域 | 有已备案域名 → 内地（杭州/北京/上海，国内访问最快、调用 DeepSeek/百炼延迟最低）<br>没备案或不想备案 → 香港 / 新加坡 | ✅ |
| 域名 | 正式对外必需（因为 HTTPS 需要域名；仅有 IP 无法申请受信任证书） | ✅ |
| 大模型 Key | DeepSeek 或通义千问（百炼）任一，**服务器在阿里云就优先选百炼**，同地域延迟最低 | ✅ |
| 出入方向端口 | 22（SSH）、80、443 | ✅ |
| 成本 | 轻量 2 核 2G 新用户约 ¥99–¥150/年；域名 ¥30–60/年；HTTPS 证书 ¥0 | — |

> ⚠️ **备案提前量**：内地地域的服务器，域名必须先完成 ICP 备案，否则用域名访问 80/443 会被拦截（用公网 IP 直连一般可用，但没 HTTPS 就没麦克风）。备案走云厂商的备案系统，通常 1–2 周。**如果 2 周内就要演示，直接把服务器买在香港/新加坡，免备案，代价是国内访问稍慢。**

---

## 2. 第一步：买服务器 + 放行端口

**阿里云**：控制台 → 轻量应用服务器 → 创建
- 镜像：选「应用镜像」里的 **Docker**；或「系统镜像」→ Ubuntu 22.04
- 套餐：2 核 2G / 40GB 起步
- 创建后在实例详情页的 **「防火墙」** 标签确认入方向放行：`22`、`80`、`443`
  （⚠️ 轻量服务器的入方向规则在「防火墙」里，不是 ECS 的「安全组」，两个地方别找错）

**腾讯云**：控制台 → 轻量应用服务器 → 新建，镜像/套餐同上；放行端口在实例的 **「防火墙」** 标签。

---

## 3. 第二步：连上服务器 + 装 Docker

```bash
# 在你的电脑上（PowerShell / 终端）连接服务器
ssh root@<服务器公网IP>
```

```bash
# 装 Docker（官方脚本，会自动带上 docker compose 插件）
curl -fsSL https://get.docker.com | sh

# 如果上面这条下载很慢或超时，改用官方脚本内置的国内镜像参数
curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun

# 验证
docker --version && docker compose version
systemctl enable --now docker
```

### 3.1 关键一步：配置 Docker Hub 镜像加速（国内服务器必做）

国内网络基本无法直连 Docker Hub，`docker compose build / pull` 会卡在拉取 `python:3.11-slim-bookworm`、`node:20-bookworm-slim`、`caddy:2-alpine` 这些基础镜像上。

```bash
# 1) 去阿里云控制台搜索「容器镜像服务」→ 个人版（免费）→ 左侧「镜像加速器」
#    复制属于你的加速地址，形如 https://xxxxxxx.mirror.aliyuncs.com

# 2) 写进 Docker 配置
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://<你的专属加速地址>.mirror.aliyuncs.com"]
}
EOF

# 3) 生效
systemctl daemon-reload && systemctl restart docker

# 4) 验证（能打印出镜像信息就通了）
docker pull hello-world
```

---

## 4. 第三步：把代码放到服务器上

两种方式选一个。**方式 A 更省事，方式 B 更可靠**（绕开 GitHub 在国内服务器上时通时不通的问题）。

### 方式 A：服务器直接从 GitHub 拉

```bash
mkdir -p /opt && cd /opt
git clone https://github.com/lucasan66/ai-interview-coach.git
cd ai-interview-coach
```

> 如果 `git clone` 卡住或失败，别在这儿耗时间，直接用方式 B。
> 若仓库是私有的，需要先在服务器上配好 GitHub 的 SSH key 或用带 token 的地址。

### 方式 B：从你的 Windows 电脑打包上传（推荐）

你本机的代码已经和 `origin/main` 完全同步，所以传上去的内容和方式 A 一致。

```powershell
# 在【你的电脑】上执行（Windows 自带 tar 与 scp）
cd D:\an\dsh_project\project\ai-interview-coach

# 打包，排除依赖、构建产物、运行期数据和密钥
tar --exclude=frontend/node_modules --exclude=frontend/dist --exclude=backend/data `
    --exclude=.git --exclude=.regress --exclude=backend/.env --exclude=.deploy-verify `
    -czf ai-interview-coach.tar.gz .

# 上传（会提示输入服务器 root 密码）
scp ai-interview-coach.tar.gz root@<服务器公网IP>:/opt/
```

```bash
# 回到【服务器】上解包
mkdir -p /opt/ai-interview-coach
tar -xzf /opt/ai-interview-coach.tar.gz -C /opt/ai-interview-coach
cd /opt/ai-interview-coach
ls    # 应能看到 Dockerfile、docker-compose.yml、backend/、frontend/、deploy/
```

> 注意：压缩包里**故意不含** `backend/.env`（密钥）和 `backend/data/`（你本机的测试数据库）。
> 前者下一步单独传；后者建议线上从空库开始，别把本机的测试账号带上线。

---

## 5. 第四步：写生产配置（密钥 + 域名）

### 5.1 后端配置：`backend/.env`

```bash
cd /opt/ai-interview-coach
cp backend/.env.production.example backend/.env
vi backend/.env        # 不熟 vi 的话用 nano backend/.env
```

必改两项（模板里都有【必改】标注）：

| 变量 | 填什么 | 不填的后果 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` 或 `DASHSCOPE_API_KEY` | 你的大模型 Key（任填一个） | 线上退化成规则引擎演示模式，不是真 AI 出题 |
| `CORS_ORIGINS` | `https://你的域名`（还没有域名时先写 `http://<公网IP>`） | 保持 `*` 能用，但没必要放开 |

**最快的填法**：直接把本机那套已经跑通的配置传上去，再对照上表核对一遍。

```powershell
# 在【你的电脑】上执行
scp backend\.env root@<服务器公网IP>:/opt/ai-interview-coach/backend/.env
```

> 你本机的 `.env` 里 `TTS_PROVIDER=aliyun` / `ASR_PROVIDER=aliyun`、路径都是相对路径 `./data/...`，
> 这些在容器里都成立，可以直接用。但要**改掉**：`CORS_ORIGINS`（必须是正式域名）。
> 另外：阿里云 ASR 需要额外装 SDK（镜像默认没装，会自动降级成浏览器识别），要用就在构建时加参数，见 6.2。

### 5.2 域名配置：仓库根目录 `.env`

```bash
cat > /opt/ai-interview-coach/.env <<'EOF'
# 还没有域名时先写 :80（HTTP 模式，麦克风不可用，只用于验证链路）
SITE_ADDRESS=:80
# 有域名并解析好之后改成：
# SITE_ADDRESS=interview.example.com
EOF
```

> **没有别的「运维面板」类开关要配了。** 后端的 `backend/.env` 里与身份相关的配置只有
> `ALLOW_GUEST`（是否允许游客浏览首页）与 `AUTH_TOKEN_TTL_HOURS`（令牌有效期）；
> 项目里没有角色白名单，也没有运维后台，所以不存在「先注册某个账号名占位」这类上线前动作。
> 新增一个功能的代价就是常规的代码 + `.env`，不会再多出一个必须记住的开关。

---

## 6. 第五步：构建并启动

```bash
cd /opt/ai-interview-coach
bash deploy/deploy.sh
```

脚本会自动完成：检查 Docker → 检查 `backend/.env` → 准备数据目录并修正属主（容器内以 UID 10001 非 root 运行）→ 构建镜像 → 启动容器 → 等健康检查通过 → 打印访问地址。失败时会自动打出最后 60 行日志。

首次构建约 3–8 分钟（取决于服务器带宽）。

### 6.1 启动日志里必须确认的几行

健康检查通过后，后端会打印一份自检报告。**这几行决定了线上功能是不是真的可用**：

```bash
docker compose logs app | head -40
```

真实输出长这样（下面是本地生产模式实跑的一段：版本号、容器内路径与提供方按你的代码版本与 `.env`
取值，其余格式完全一致）：

```text
[boot] 已挂载前端产物：单进程同域提供（对外走 Caddy 的 HTTPS；容器内 8000）
       产物目录：/app/frontend/dist
[boot] 校招全流程助手 启动完成
[boot] 无超时会话（空闲阈值 6 小时）
[boot] 游客模式开启：未登录请求走进程内内存库（不落盘、重启即消失），「我的面试 / 简历优化」对游客隐藏
[boot] LLM: {'mode': 'live', 'provider': 'deepseek', ...}  TTS: aliyun  ASR: browser
[boot] 简历优化 PDF 导出：可用
[boot] LLM 已启用：deepseek / deepseek-chat
```

逐行对照：

| 日志行 | 期望值 | 不对时怎么办 |
| --- | --- | --- |
| `LLM 已启用：deepseek / deepseek-chat` | 出现「已启用」 | 显示「LLM 未启用」→ Key 没生效，看同一行给的 hint，或访问 `/api/health/llm` |
| `TTS: aliyun` / `TTS: edge` / `TTS: browser` | 与 `.env` 一致 | 面试官没声音的根因基本都在这行 |
| `ASR: browser`（未装 SDK 时） | 正常降级 | 一定要阿里云识别就加 `--build-arg INSTALL_NLS_SDK=1` 重建 |
| `简历优化 PDF 导出：可用` | 出现「可用」 | 显示「不可用」→ 见排错表 |
| `已挂载前端产物` | 出现 | 这行说的是「后端自己也在托管前端」，不是对外地址（对外一律走 Caddy 的域名）。没出现说明 `frontend/dist` 没进镜像，前端会 404 |

### 6.2 构建参数速查（国内网络相关）

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `NPM_REGISTRY` | `https://registry.npmmirror.com` | npm 源 |
| `PIP_INDEX_URL` | `https://pypi.tuna.tsinghua.edu.cn/simple` | pip 源 |
| `DEBIAN_MIRROR` | `deb.debian.org` | apt 源，卡住时换成 `mirrors.aliyun.com` |
| `INSTALL_PDF_TOOLCHAIN` | `1` | 简历 PDF 导出工具链（wkhtmltopdf + 中文字体，约 400MB） |
| `INSTALL_NLS_SDK` | `0` | 阿里云语音识别 SDK（会连带装 oss2 / matplotlib） |

```bash
# 例：apt 卡住时换国内源重建
docker compose build --build-arg DEBIAN_MIRROR=mirrors.aliyun.com
```

---

## 7. 第六步：验证（先用 IP + HTTP）

```bash
# 1) 端到端健康检查（走 Caddy → app，和浏览器的路径完全一致）
curl -fsS http://127.0.0.1/api/health && echo

# 2) 直接问容器（排查 Caddy 层是否有问题时用）
docker compose exec app curl -fsS http://127.0.0.1:8000/api/health && echo

# 3) 容器状态：两个都应是 Up，app 是 (healthy)
docker compose ps
```

浏览器打开 `http://<服务器公网IP>/`，逐项确认：

- [ ] 首页能打开（说明前端产物 + SPA 路由正常）
- [ ] 能注册 / 登录
- [ ] 「简历优化」里拖一份 PDF 进去能解析出内容（说明 pdfplumber 正常）
- [ ] 能跑完一次文字作答的面试并拿到报告（说明大模型调用正常）
- [ ] 「设备自检」页提示**拿不到麦克风** ← **此时这是正常的**，因为还没有 HTTPS

> 到了这一步，除了语音相关功能，其余都应该可用。语音必须等下一步的域名 + HTTPS。

---

## 8. 第七步：绑域名 + 自动 HTTPS（语音功能在这一步才活）

### 8.1 解析域名

在域名服务商（阿里云/腾讯云 DNS）加一条 A 记录：

```
主机记录：interview      （或 @ 表示根域名）
记录类型：A
记录值：  <服务器公网IP>
```

验证解析已生效（应返回你的服务器 IP）：

```bash
dig +short interview.example.com
```

> ⚠️ 内地地域的服务器：域名要先完成 ICP 备案，否则 80/443 会被拦截、证书也申请不下来。

### 8.2 切换 Caddy 到 HTTPS

```bash
cd /opt/ai-interview-coach
sed -i 's|^SITE_ADDRESS=.*|SITE_ADDRESS=interview.example.com|' .env
docker compose up -d caddy
docker compose logs -f caddy     # 看到 certificate obtained 就成功了，Ctrl+C 退出
```

Caddy 会自动申请证书、自动续期、并把 HTTP 请求 301 跳到 HTTPS。**证书不需要你买，也不需要手动续期。**

同时把后端跨域收窄（可选但推荐）：

```bash
sed -i 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://interview.example.com|' backend/.env
docker compose up -d app
```

### 8.3 最终验收

浏览器打开 `https://interview.example.com/`：

- [ ] 地址栏是小锁（证书有效）
- [ ] 「设备自检」里麦克风、录音、语音识别全部通过
- [ ] 完整跑一次**语音作答**的面试并拿到报告
- [ ] 手机上打开也正常

---

## 9. 日常运维速查

| 要做的事 | 命令（在 `/opt/ai-interview-coach` 下） |
| --- | --- |
| 更新代码并重新部署 | `bash deploy/deploy.sh --pull` |
| 只改了 `backend/.env`，让它生效 | `docker compose up -d app` |
| 只改了根目录 `.env`（域名） | `docker compose up -d caddy` |
| 看后端日志 | `docker compose logs -f app` |
| 看最近的报错 | `docker compose logs --tail=100 app \| grep -i error` |
| 备份数据 | `bash deploy/backup.sh` |
| 重启全部 | `docker compose restart` |
| 停止（数据保留） | `docker compose down` |
| 看磁盘占用 | `du -sh backend/data/*` |

### 备份与恢复

```bash
bash deploy/backup.sh                     # 备份到 deploy/backups/
bash deploy/backup.sh /mnt/oss-backups    # 或指定目录（建议挂对象存储）
```

备份用的是 SQLite **在线备份 API**，服务不用停、也不会拿到损坏的快照（直接 `tar` 一个正在写入的 `app.db` 是有风险的）。
恢复步骤脚本末尾会打印出来。

> **只把备份放在同一台服务器上等于没备份。** 请定期同步到阿里云 OSS / 腾讯云 COS，或至少拉到本地。
> 另外建议在云控制台开启「自动快照」，成本很低。

---

## 10. 排错对照表

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 浏览器 502 Bad Gateway | `app` 容器没起来或没通过健康检查 | `docker compose ps` 看状态；`docker compose logs --tail=80 app` |
| `deploy.sh` 报找不到 `backend/.env` | 配置还没放 | 按脚本提示 `cp` 模板或 `scp` 上传 |
| 容器反复 Restarting | 配置有硬错（如 `DATABASE_URL` 被改成绝对路径、`.env` 语法错误） | `docker compose logs --tail=50 app` |
| `unable to open database file` / `Permission denied` | `backend/data` 属主不是容器内的 UID 10001 | `chown -R 10001:10001 backend/data` 后 `docker compose up -d app` |
| 上传简历报 500 | 同上的权限问题（`uploads/` 写不进去） | 同上 |
| 页面能开但拿不到麦克风 | 不是 HTTPS 环境 | 按第 8 步配域名 + Caddy 自动证书 |
| 面试官没有声音 | 国内服务器调用 edge-tts（微软服务）慢或不稳 | `TTS_PROVIDER=aliyun`（有阿里云语音密钥时最佳）或 `browser`；对照启动日志的 `TTS:` 行 |
| 语音识别不可用 | `ASR_PROVIDER=aliyun` 但没装 SDK，已降级浏览器识别；或浏览器不是 Chrome/Edge | 加 `--build-arg INSTALL_NLS_SDK=1` 重建，或改用 Chrome/Edge |
| `/api/resume-optimizer/render-pdf` 返回 503 | 镜像里没有 wkhtmltopdf | 确认启动日志「PDF 导出：可用」；若是用 `INSTALL_PDF_TOOLCHAIN=0` 构建的，去掉该参数重建 |
| 导出的 PDF 里中文是方块 | 缺中文字体 | 只会发生在自建镜像时；本方案已装 `fonts-noto-cjk`，别删 |
| 证书申请失败 | 域名没解析到本机 / 80 端口没放行 / 撞 Let's Encrypt 限流（同域名每周 5 次） | `dig +short 域名` 核对 IP；检查安全组；限流就先用 `SITE_ADDRESS=:80` 等一周 |
| `docker compose build` 卡在 apt / npm / pip | 国内网络拉境外源慢 | 用 6.2 的构建参数换国内源 |
| `docker pull` 超时 / 连接被重置 | Docker Hub 不可达 | 按 3.1 配镜像加速器 |
| `bash: $'\r': command not found` | 脚本被 Windows 编辑器存成了 CRLF | `sed -i 's/\r$//' deploy/*.sh` |
| 重启后用户数据不见了 | `data` 目录没挂上，或 `.env` 里把路径改成了容器内绝对路径 | 确认 `docker-compose.yml` 的 `volumes` 行存在，且 `.env` 里是 `./data/...` 相对路径 |
| 游客刚答的题刷新后没了 | **设计如此**：`ALLOW_GUEST=true` 时游客数据在进程内存里，不落盘 | 想保留数据就登录；或 `ALLOW_GUEST=false` 强制登录 |
| 磁盘满了 | `tts_cache` 语音缓存累积 | `du -sh backend/data/*` 看占用；`tts_cache` 里的文件可以安全删空，会自动重新合成 |
| 明明填了 Key，日志还是「LLM 未启用」 | 密钥里含 `$`，被 docker compose 的变量插值吃掉了 | 把 `$` 写成 `$$`；或改成在 `docker-compose.yml` 里用 `environment:` 直接列（不走 `env_file`） |
| 改了 `backend/.env` 但行为没变 | 容器不会自动重载配置 | `docker compose up -d app`（改 `.env` 必须重建容器，`restart` 不生效） |

---

## 11. 上线后安全清单

- [ ] `backend/.env` 从未提交到 Git（`.gitignore` 已忽略；`.dockerignore` 也确保它不会被打进镜像）
- [ ] `CORS_ORIGINS` 已收窄为正式域名，不是 `*`
- [ ] 服务器：改用密钥登录、禁用 root 密码登录、改掉默认 SSH 端口（可选但强烈建议）
- [ ] 已配置定期备份 + 异地留存（OSS/COS 或云自动快照）
- [ ] `docker compose` 里 `app` 服务用的是 `expose` 而非 `ports`（不要绕过 HTTPS 暴露 8000）
- [ ] 定期 `docker compose build --pull` 重建，拿基础镜像的安全补丁（重建前先备份）

---

## 12. 已知限制与后续演进

**当前架构的天花板**：单台服务器 + 单进程 + SQLite。它非常适合「课程项目 / 小范围内测 / 演示」，但不是无限扩展的架构。用户量上来后按顺序做这三件事：

1. **换数据库**：把 `DATABASE_URL` 从 `sqlite:///./data/app.db` 换成 `postgresql+psycopg://...` 或 `mysql+pymysql://...` 即可（`app/db.py` 的注释明确支持这条路径），记得装对应驱动并重建镜像。
2. **上传文件挪到对象存储**：`backend/data/uploads/` 改存阿里云 OSS / 腾讯云 COS，否则多台服务器之间看不到对方的简历原件。
3. **才能加副本**：在完成 1、2 之前不要加第二个实例——游客数据存在**进程内存**里，多实例会让同一游客的会话在实例间「跳库」。这一步做完再上负载均衡。

**其它值得知道的点**：

- 国内服务器调用 edge-tts（微软语音）延迟偏高，本方案靠「3.5 秒预算 + 浏览器语音兜底 + 后台缓存」保证不卡顿，但线上更稳的选择是 `TTS_PROVIDER=aliyun` 或 `browser`。
- 前端几处网络错误提示文案里写死过「请确认后端服务已启动（127.0.0.1:8000）」（`frontend/src/api/client.js`、`pages/Home.jsx`）。**已修**：提示按 `import.meta.env.DEV` 分岔 —— 开发模式仍给「后端没起 / 代理指向 8000」这条最有用的线索，生产模式只说「请检查网络后重试」，线上用户不会再看到本机地址。
- Caddy 的访问日志在 `caddy_data` 卷里的 `access.log`，用 `docker compose exec caddy cat /data/access.log` 查看。

---

## 附录 A：部署产物清单

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 多阶段构建：Node 构建前端 → Python 运行时 + 前端产物 + PDF 工具链 |
| `.dockerignore` | 确保**密钥与运行期数据绝不进镜像**，并让构建上下文从数百 MB 降到几 MB |
| `docker-compose.yml` | 编排 `app` + `caddy`，定义唯一的持久化目录 |
| `deploy/Caddyfile` | 反向代理 + 自动 HTTPS 配置 |
| `deploy/deploy.sh` | 服务器端一键部署/更新（含权限修正与健康检查） |
| `deploy/backup.sh` | 一致性备份 SQLite + 用户上传原件 |
| `backend/.env.production.example` | 生产环境变量模板（含每项的生产口径说明） |
| `DEPLOY.md` | 本文档 |

## 附录 B：在 Windows 本机先自测生产模式

不想等服务器就能验证「单进程生产模式」是否正常：

```powershell
cd D:\an\dsh_project\project\ai-interview-coach\frontend
npm.cmd run build

cd ..\backend
python -m uvicorn app.main:app --port 8000
# 打开 http://127.0.0.1:8000/ —— 后端会自动托管前端产物
```

> 本机是 `localhost`，浏览器同样会给麦克风权限，所以语音相关功能在本地也能测；
> 这也是为什么「本地一切正常、上线后录不了音」会让人困惑——差别就在 HTTPS。
