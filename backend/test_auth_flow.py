"""登录 / 数据私有 端到端自检（进程内 TestClient，不依赖已启动的服务）。

覆盖：
1. 游客身份与注册 / 登录 / 退出；
2. 账号枚举、弱口令、重复账号、错误密码、伪造令牌的拒绝路径；
3. **跨用户越权**：别人拿着资源 id 也读不到简历 / JD / 面试会话 / 报告 / 错题；
4. 请求体里伪造 `user_id` 不生效（归属只认令牌）；
5. **游客模式数据隔离**：游客可完整跑通，但数据只落在进程内存库（不落盘）、
   对登录用户不可见，登录用户的数据游客也读不到。

运行（仓库根目录）：
    cd backend
    python test_auth_flow.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

# ⚠️ 必须在 import app 之前改掉数据库地址。
#
# 为什么：这个自检要反复注册账号，如果连的是 `data/app.db`，每跑一次就会往真实库里
# 塞两个账号（历史上因此积了 125 个 alice*/bob*，与真实用户无法区分）。
# 改用独立的临时库后，测试**物理上碰不到**真实数据，跑完即删。
# 注意：必须在 config 被导入前设置，因为 engine 在模块导入时就创建了。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="aic_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'test_auth.db').as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, guest_db_session  # noqa: E402
from app.deps import dump_json  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    InterviewSession,
    JobDescription,
    Resume,
    User,
    WrongQuestion,
    now_utc,
)
from app.services.auth import TEST_USERNAME_PREFIX  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' -> ' + extra) if extra else ''}")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def main() -> int:
    # raise_server_exceptions=False：把 500 也当普通响应看待，便于断言"不该 500"
    with TestClient(app, raise_server_exceptions=False) as client:
        tag = uuid.uuid4().hex[:8]
        # 账号统一加 TEST_USERNAME_PREFIX（t_）：这是后端「只保留用户注册的账号」那条规则
        # 唯一的识别依据。早先这里用 alice*/bob*，而它们是**完整注册用户**，
        # 与真实账号在数据上无法区分，结果在真实库里积了 125 个测试账号。
        alice, bob = f"{TEST_USERNAME_PREFIX}alice{tag}", f"{TEST_USERNAME_PREFIX}bob{tag}"

        print("\n[1] 游客与注册")
        r = client.get("/api/auth/me")
        check("未登录 /auth/me 返回游客", r.status_code == 200 and r.json()["is_guest"] is True)
        # 关键回归：未登录访问 /auth/me 不得在**落盘库**里留下任何账号行。
        # 历史上这里会 db.add 一行 username 为空的 guest 账号，于是每个访客都会
        # 在真实库里留一条记录 —— 「数据库里只保留用户注册的账号」就永远做不到。
        with SessionLocal() as db:
            stale_guests = db.query(User).filter(User.username == "").count()
        check("未登录访问 /auth/me 不写库（无 guest 账号行）", stale_guests == 0, f"空账号行={stale_guests}")

        r = client.post(
            "/api/auth/register",
            json={"username": alice, "password": "pass1234", "nickname": "Alice", "school": "SCUT"},
        )
        check("注册成功(200)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        if r.status_code != 200:
            return 1
        tok_a = r.json()["token"]
        user_a = r.json()["user"]["user_id"]
        check("注册后非游客", r.json()["user"]["is_guest"] is False)
        check("响应不含口令字段", "password" not in r.text and "password_hash" not in r.text)
        # 注册就是普通登录用户，不再有「先到先得的一次性免费体验」这回事：
        # 全部功能本来就对登录用户开放，注册响应里出现额度文案就是残留。
        check("注册响应不含免费体验额度", "免费体验" not in r.text and "quota" not in r.text)

        r = client.post("/api/auth/register", json={"username": bob, "password": "pass1234"})
        tok_b = r.json()["token"]
        check("第二个用户注册成功", r.status_code == 200)

        print("\n[2] 拒绝路径")
        r = client.post("/api/auth/register", json={"username": alice, "password": "pass1234"})
        check("重复账号 409", r.status_code == 409, f"HTTP {r.status_code}")
        r = client.post("/api/auth/register", json={"username": f"w{tag}", "password": "123"})
        check("弱口令 400", r.status_code == 400, f"HTTP {r.status_code}")
        r = client.post("/api/auth/register", json={"username": "a b", "password": "pass1234"})
        check("非法账号 400", r.status_code == 400, f"HTTP {r.status_code}")
        r = client.post("/api/auth/login", json={"username": alice, "password": "WRONG"})
        check("错误密码 401", r.status_code == 401, f"HTTP {r.status_code}")
        r = client.post("/api/auth/login", json={"username": f"ghost{tag}", "password": "pass1234"})
        check("不存在的账号也是 401（不可枚举）", r.status_code == 401, f"HTTP {r.status_code}")
        r = client.get("/api/auth/me", headers=bearer("forged-token"))
        check("伪造令牌 401", r.status_code == 401, f"HTTP {r.status_code}")

        print("\n[3] 登录 / 退出")
        r = client.post("/api/auth/login", json={"username": alice, "password": "pass1234"})
        check("正确密码登录成功", r.status_code == 200)
        r = client.get("/api/auth/me", headers=bearer(tok_a))
        check("带令牌 /auth/me 是本人", r.status_code == 200 and r.json()["user_id"] == user_a)

        # 全部功能对登录用户免费：身份载荷里不该再出现任何配额/次数字段，
        # 已下线的 /api/quota 与用户反馈接口也必须真的 404 ——
        # 否则前端会以为这些能力还在，出错时只会表现成「点了没反应」。
        check("/auth/me 不含 quota 字段", "quota" not in r.json())
        check("已下线的 /api/quota -> 404", client.get("/api/quota", headers=bearer(tok_a)).status_code == 404)
        check("已下线的 /api/feedback/meta -> 404", client.get("/api/feedback/meta").status_code == 404)

        print("\n[4] 数据归属：令牌决定 owner，请求体 user_id 被忽略")
        r = client.post("/api/demo/seed", json={}, headers=bearer(tok_a))
        check("alice 一键体验 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        seeded = r.json()
        resume_a, jd_a = seeded["resume_id"], seeded["jd_id"]
        check("示例数据归属 alice", seeded["user_id"] == user_a, seeded["user_id"])

        # 伪造：alice 的令牌 + 请求体里塞 bob 的 user_id
        r = client.post("/api/demo/seed", json={"user_id": seeded["user_id"]}, headers=bearer(tok_b))
        check("bob 的令牌签发的是 bob 的数据", r.status_code == 200 and r.json()["user_id"] != user_a)
        jd_b = r.json()["jd_id"]

        print("\n[5] 跨用户越权一律 404")
        check("bob 读 alice 的简历 -> 404", client.get(f"/api/resume/{resume_a}", headers=bearer(tok_b)).status_code == 404)
        check("alice 读自己的简历 -> 200", client.get(f"/api/resume/{resume_a}", headers=bearer(tok_a)).status_code == 200)
        check("bob 读 alice 的 JD -> 404", client.get(f"/api/jd/{jd_a}", headers=bearer(tok_b)).status_code == 404)
        check(
            "bob 用 alice 的简历做匹配 -> 404",
            client.post("/api/match", json={"resume_id": resume_a, "jd_id": jd_a}, headers=bearer(tok_b)).status_code == 404,
        )
        check(
            "bob 改 alice 的简历 -> 404",
            client.put(f"/api/resume/{resume_a}", json={"parsed": {"skills": ["x"]}}, headers=bearer(tok_b)).status_code == 404,
        )
        check(
            "bob 用 alice 的简历开面试 -> 404",
            client.post(
                "/api/interview/start",
                json={"resume_id": resume_a, "jd_id": jd_b, "mode": "text"},
                headers=bearer(tok_b),
            ).status_code
            == 404,
        )
        check("游客读 alice 的简历 -> 401（登录门槛在归属校验前）", client.get(f"/api/resume/{resume_a}").status_code == 401)

        print("\n[6] 会话 / 报告 / 错题本越权")
        # 直接落一条 alice 的会话与错题，避免依赖 LLM 出题（让自检稳定、快速）
        with SessionLocal() as db:
            sess = InterviewSession(
                session_id=uuid.uuid4().hex,
                user_id=user_a,
                job_title="数据分析实习",
                dialogue_json="[]",
                # 必须带 overview，否则 /report/history 会按「没有报告」跳过它
                # （该接口只统计已有真实报告的 finished 会话）
                report_json=dump_json({"overview": {"score": 7.5, "percent": 75}}),
                status="finished",
                score=7.5,
                finished_at=now_utc(),
            )
            wrong = WrongQuestion(
                id=uuid.uuid4().hex,
                user_id=user_a,
                session_id=sess.session_id,
                question="请介绍一个你做过的分析项目",
                answer="...",
                score=4.0,
            )
            db.add_all([sess, wrong])
            db.commit()
            sid, wid = sess.session_id, wrong.id

        check("bob 读 alice 的会话 -> 404", client.get(f"/api/interview/{sid}", headers=bearer(tok_b)).status_code == 404)
        check("alice 读自己的会话 -> 200", client.get(f"/api/interview/{sid}", headers=bearer(tok_a)).status_code == 200)
        check("bob 读 alice 的逐字稿 -> 404", client.get(f"/api/interview/{sid}/transcript", headers=bearer(tok_b)).status_code == 404)
        check("bob 结束 alice 的面试 -> 404", client.post(f"/api/interview/{sid}/finish", headers=bearer(tok_b)).status_code == 404)
        check("bob 重试 alice 的错题 -> 404", client.post("/api/practice/retry", json={"wrong_id": wid}, headers=bearer(tok_b)).status_code == 404)
        check("bob 删 alice 的错题 -> 404", client.delete(f"/api/practice/wrong-questions/{wid}", headers=bearer(tok_b)).status_code == 404)

        # 错题本列表按令牌隔离
        wa = client.get("/api/practice/wrong-questions", headers=bearer(tok_a)).json()
        wb = client.get("/api/practice/wrong-questions", headers=bearer(tok_b)).json()
        check("alice 错题本能看到自己的题", wa["total"] >= 1, f"total={wa['total']}")
        check("bob 错题本看不到 alice 的题", all(i["id"] != wid for i in wb["items"]), f"total={wb['total']}")

        # 历史曲线按令牌隔离（且接口不再接受 user_id 入参）
        ha = client.get("/api/report/history", headers=bearer(tok_a)).json()["items"]
        hb = client.get("/api/report/history", headers=bearer(tok_b)).json()["items"]
        check("alice 历史含自己的会话", any(i["session_id"] == sid for i in ha))
        check("bob 历史不含 alice 的会话", all(i["session_id"] != sid for i in hb))

        print("\n[7] 退出登录即时吊销")
        check("alice 退出 200", client.post("/api/auth/logout", headers=bearer(tok_a)).status_code == 200)
        check("退出后原令牌 401", client.get("/api/auth/me", headers=bearer(tok_a)).status_code == 401)
        check("退出后仍可用令牌读数据被拒", client.get(f"/api/resume/{resume_a}", headers=bearer(tok_a)).status_code == 401)
        check("bob 的令牌不受影响", client.get("/api/auth/me", headers=bearer(tok_b)).status_code == 200)

        print("\n[8] 游客模式：个人功能一律 401，公开只读仍可用，且不落盘")

        # 先给落盘库拍个快照：游客被拒后这些数字必须**一个都不涨**
        with SessionLocal() as db:
            disk_before = (
                db.query(InterviewSession).count(),
                db.query(Resume).count(),
                db.query(JobDescription).count(),
            )

        # 8.1 会产生个人数据 / 消耗模型额度的接口：游客（无令牌）必须 401。
        #     产品要求「模拟面试 / 我的面试 / 简历优化 仅登录用户可用」——
        #     只在页面上挡是纸糊的门，接口也必须拒。
        guest_calls = [
            ("模拟面试 start", "post", "/api/interview/start", {"resume_id": "x", "jd_id": "y", "mode": "text"}),
            ("简历解析 resume/text", "post", "/api/resume/text", {"text": "张三 " * 20}),
            ("JD 解析 jd/parse", "post", "/api/jd/parse", {"text": "岗位职责：负责数据分析" * 3}),
            ("匹配度 match", "post", "/api/match", {"resume_id": "x", "jd_id": "y"}),
            ("一键示例 demo/seed", "post", "/api/demo/seed", {}),
            ("报告历史 report/history", "get", "/api/report/history", None),
            ("错题本 wrong-questions", "get", "/api/practice/wrong-questions", None),
            ("成长面板 growth/summary", "get", "/api/growth/summary", None),
        ]
        for label, method, path, payload in guest_calls:
            resp = client.post(path, json=payload) if method == "post" else client.get(path)
            check(f"游客 {label} -> 401", resp.status_code == 401, f"HTTP {resp.status_code}")

        # 8.2 拿着别人的资源 id 也一样：登录门槛在归属校验之前，且 401 不泄露存在性
        check("游客凭 id 读 alice 的简历 -> 401", client.get(f"/api/resume/{resume_a}").status_code == 401)
        check("游客凭 id 读 alice 的报告 -> 401", client.get(f"/api/report/{sid}").status_code == 401)

        # 8.3 公开只读接口不受影响：游客仍能看首页所需的元信息、公司库、榜单与简历优化元信息
        #     （这些既不产生也不暴露个人数据，是产品对外的展示面）
        for label, path in [
            ("health", "/api/health"),
            ("meta/options", "/api/meta/options"),
            ("company/search", "/api/company/search?q=字节"),
            ("leaderboard", "/api/leaderboard?type=job"),
            ("resume-optimizer/meta", "/api/resume-optimizer/meta"),
        ]:
            resp = client.get(path)
            check(f"游客仍可读 {label} -> 200", resp.status_code == 200, f"HTTP {resp.status_code}")

        # 8.4 「不保存」的硬证据：被拒的游客调用没有往落盘库写任何一行
        with SessionLocal() as db:
            disk_after = (
                db.query(InterviewSession).count(),
                db.query(Resume).count(),
                db.query(JobDescription).count(),
            )
            check(
                "被拒的游客调用没有写进落盘库",
                disk_before == disk_after,
                f"session/resume/jd 前={disk_before} 后={disk_after}",
            )
            check(
                "落盘库里没有 guest 名下的数据",
                db.query(Resume).filter(Resume.user_id == "guest").count() == 0
                and db.query(InterviewSession).filter(InterviewSession.user_id == "guest").count() == 0,
            )

        # 8.5 防御纵深：游客库与落盘库是两个物理上分开的库
        with guest_db_session() as gdb:
            guest_bind = str(gdb.get_bind().url)
        with SessionLocal() as db:
            disk_bind = str(db.get_bind().url)
        check(
            "游客库与落盘库物理隔离",
            guest_bind != disk_bind and guest_bind == "sqlite://",
            f"guest={guest_bind} disk=…{disk_bind[-18:]}",
        )

    print(f"\n{'='*54}\n通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    # 独立临时库用完即删：这个自检不再往 data/app.db 写任何东西
    shutil.rmtree(_TEST_DIR, ignore_errors=True)
    if FAILED:
        print("失败项：")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("全部通过：登录可用，且数据已按用户隔离。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
