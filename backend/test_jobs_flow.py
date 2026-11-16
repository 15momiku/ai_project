"""岗位信息汇总端到端自检（进程内 TestClient，不依赖已启动的服务）。

覆盖这个模块最容易被改坏的六件事：

1. **权限**：游客一律 401（本模块不向游客开放）；岗位库是一份**全员共享的台账**，
   任何登录用户都能新增 / 批量导入 / 载入示例 / 修改 / 删除 / 清空 ——
   没有管理员角色、也没有白名单；
2. **进度属于个人**：别的登录用户也看不到你的进度；跨账号读取一律读不到；
3. **导入语义**：只读取 8 个岗位信息字段（不含公告链接与备注）—— 请求里带上
   「投递进度 / 更新时间」也会被忽略（进度一律「未投递」、录入时间由服务端记当天）；
   **默认不去重**（同一家企业多行都会入库），显式 `dedupe=true` 才按企业名合并；
   两种模式下「企业名为空」的行都跳过；
4. **状态校验**：非法进度 400；不存在的岗位 404；「未投递」= 删除记录；
5. **统计口径**：只统计已投递；漏斗按阶段递进推算；已挂按环节归因；
6. **连带清理**：删除岗位 / 清空岗位库会清除所有人的相关进度，不留孤儿数据。

岗位库「按天有效期」已经整体下线，因此本套自检不再断言
`is_admin` / `jobs_days_left` / `jobs_expires_at` 这类已删除的字段，
也不再构造「到期 → 403」的场景：任何登录用户随时都能维护与查看岗位库。

运行（仓库根目录）：
    cd backend
    python test_jobs_flow.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

# ⚠️ 必须在 import app 之前改掉数据库地址，否则自检会往真实库 data/app.db 里写数据。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="aic_jobs_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'test_jobs.db').as_posix()}"
os.environ["LLM_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from app.constants import JOB_STATUS_LIST  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.deps import _LOCAL_TZ  # noqa: E402
from app.main import app  # noqa: E402
from app.models import JobPosting, JobProgress, now_utc  # noqa: E402
from app.services import job_store  # noqa: E402
from app.services.auth import TEST_USERNAME_PREFIX  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' -> ' + extra) if extra else ''}")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def main() -> int:
    try:
        return _run()
    finally:
        shutil.rmtree(_TEST_DIR, ignore_errors=True)


def _run() -> int:
    with TestClient(app, raise_server_exceptions=False) as client:
        tag = uuid.uuid4().hex[:8]
        alice_name = f"{TEST_USERNAME_PREFIX}jalice{tag}"
        bob_name = f"{TEST_USERNAME_PREFIX}jbob{tag}"
        carol_name = f"{TEST_USERNAME_PREFIX}jcarol{tag}"

        # 三个普通登录账号：alice 维护岗位库与投递进度、bob 与 carol 用来验
        # 「进度各自独立」和「岗位库对所有人开放维护」。
        alice = client.post(
            "/api/auth/register",
            json={"username": alice_name, "password": "pw123456"},
        ).json()
        alice_token = alice["token"]
        alice_id = alice["user"]["user_id"]
        bob_token = client.post(
            "/api/auth/register",
            json={"username": bob_name, "password": "pw123456"},
        ).json()["token"]
        carol_token = client.post(
            "/api/auth/register",
            json={"username": carol_name, "password": "pw123456"},
        ).json()["token"]

        print("\n[1] 游客一律 401（本模块不向游客开放）")
        check("游客读岗位库 -> 401", client.get("/api/jobs").status_code == 401)
        check("游客读统计 -> 401", client.get("/api/jobs/stats").status_code == 401)
        check("游客改进度 -> 401",
              client.put("/api/jobs/whatever/progress", json={"status": "已测评"}).status_code == 401)

        print("\n[2] 空库与列表下发内容")
        r = client.get("/api/jobs", headers=bearer(alice_token))
        body = r.json()
        check("登录用户可读岗位库", r.status_code == 200, f"HTTP {r.status_code}")
        check("新库初始为空", body["total"] == 0 and body["items"] == [], str(body["total"]))
        check("状态字典随列表下发（与 constants 一致）", body["statuses"] == JOB_STATUS_LIST, str(len(body["statuses"])))
        check("列表带共享/独立的说明文案", "投递进度各自独立" in (body.get("note") or ""))
        r = client.get("/api/jobs", headers=bearer(carol_token))
        check("另一个登录用户看到的是同一份（空）共享岗位库",
              r.status_code == 200 and r.json()["total"] == 0, f"HTTP {r.status_code}")

        print("\n[3] 岗位库维护对**任何登录用户**开放（已无管理员专属权限），游客仍然 401")
        job_payload = {"company": f"测试公司-{tag}", "nature": "民企", "audience": "2027届本科及以上",
                       "locations": ["北京", "上海"], "majors": ["计算机科学与技术"],
                       "positions": ["后端开发工程师"], "note": "自检数据"}
        r = client.post("/api/jobs", json=job_payload, headers=bearer(alice_token))
        check("普通登录用户新增岗位 -> 200", r.status_code == 200, f"HTTP {r.status_code}")
        created_id = (r.json() or {}).get("job_id") or ""
        r = client.post(
            "/api/jobs/bulk",
            json={"jobs": [{**job_payload, "company": f"批量公司-{tag}"}]},
            headers=bearer(alice_token),
        )
        check("普通登录用户批量导入 -> 200（新增 1 条）",
              r.status_code == 200 and r.json()["added"] == 1, f"HTTP {r.status_code} {r.text[:60]}")
        r = client.delete(f"/api/jobs/{created_id}", headers=bearer(bob_token))
        check("另一个登录用户可以删除他人录入的岗位（共享台账）", r.status_code == 200, f"HTTP {r.status_code}")
        # 清掉批量导入那条，让岗位库回到 0 —— 后面的精确计数断言依赖这个起点
        for it in client.get("/api/jobs", headers=bearer(bob_token)).json()["items"]:
            client.delete(f"/api/jobs/{it['job_id']}", headers=bearer(bob_token))
        check("前面的增删都清干净了（岗位库回到 0 条）",
              client.get("/api/jobs", headers=bearer(alice_token)).json()["total"] == 0)
        r = client.post("/api/jobs/seed", headers=bearer(alice_token))
        check("普通登录用户载入 20 条示例岗位 -> 200",
              r.status_code == 200 and r.json()["added"] == 20, str(r.json()))
        r = client.post("/api/jobs/seed", headers=bearer(alice_token))
        check("重复载入示例不会重复入库（按企业名去重）",
              r.json()["added"] == 0 and r.json()["skipped"] == 20, str(r.json()))
        check("游客新增岗位 -> 401", client.post("/api/jobs", json=job_payload).status_code == 401)
        check("游客批量导入 -> 401",
              client.post("/api/jobs/bulk", json={"jobs": [job_payload]}).status_code == 401)
        check("游客载入示例 -> 401", client.post("/api/jobs/seed").status_code == 401)
        check("游客改岗位 -> 401", client.put("/api/jobs/whatever", json=job_payload).status_code == 401)
        check("游客删除岗位 -> 401", client.delete("/api/jobs/whatever").status_code == 401)
        check("游客清空岗位库 -> 401", client.delete("/api/jobs").status_code == 401)

        print("\n[4] 示例数据与批量导入")
        r = client.get("/api/jobs", headers=bearer(alice_token))
        check("上一步载入的 20 条示例岗位都在库里", r.json()["total"] == 20, str(r.json()["total"]))

        # 导入**只认岗位信息字段**：这里故意带上 progress / posted_at / updated_at
        bulk = {
            "jobs": [
                {"company": f"导入公司A-{tag}", "nature": "央企", "industry": "能源电力",
                 "locations": "北京、上海、深圳",
                 "positions": "数据分析师、算法工程师", "progress": "已Offer", "updated_at": "2000-01-01",
                 "posted_at": "2000-01-01", "note": "含多值分隔符"},
                {"company": f"导入公司A-{tag}", "nature": "央企"},  # 同一批内重复：应被跳过
                {"company": "", "nature": "外企"},                 # 没有企业名：应被跳过
                {"company": f"导入公司B-{tag}", "major": "不该出现的字段"},
            ],
            "dedupe": True,
        }
        r = client.post("/api/jobs/bulk", json=bulk, headers=bearer(alice_token))
        res = r.json()
        check("批量导入：新增 2 条、跳过 2 条", res["added"] == 2 and res["skipped"] == 2, str(res))

        items = client.get("/api/jobs", headers=bearer(alice_token)).json()["items"]
        imported = next(it for it in items if it["company"] == f"导入公司A-{tag}")
        check("多值字段按分隔符拆成数组", imported["locations"] == ["北京", "上海", "深圳"], str(imported["locations"]))
        check("多值岗位字段同样拆分", imported["positions"] == ["数据分析师", "算法工程师"], str(imported["positions"]))
        check("「行业」作为独立字段入库（与「企业性质」分开，不互相覆盖）",
              imported["industry"] == "能源电力" and imported["nature"] == "央企",
              f"industry={imported['industry']!r} nature={imported['nature']!r}")
        check("导入时的进度一律为「未投递」（忽略请求里的已Offer）", imported["progress"] == "未投递", imported["progress"])
        check("导入时的更新时间由服务端记当天（忽略请求里的 2000-01-01）",
              imported["posted_at"] not in ("", "2000-01-01"), imported["posted_at"])
        check("未在白名单里的字段被丢弃（没有 major 列）", "major" not in imported)

        print("\n[4b] 列表上方的「昨日更新 N 家」标注")
        listing = client.get("/api/jobs", headers=bearer(alice_token)).json()
        check("列表响应带 posted_yesterday 字段（已不再叫 posted_today）",
              "posted_yesterday" in listing and "posted_today" not in listing,
              str(list(listing.keys())))

        # 精确验证口径：直接往库里插两条**指定时间**的岗位 ——
        # 一条落在「本地昨天 10:00」、一条落在「本地今天 10:00」——
        # 然后断言计数**只 +1**（只认昨天那条，今天那条不算）。
        #
        # 刻意不拿 seed 的 days_ago 去推算期望值：seed 按 **UTC 零点**落库，
        # 它究竟落在本地的哪一天取决于跑测试的时刻，会让断言变成
        # 「白天跑过、晚上跑挂」的时间相关失败。这里自己构造两个稳稳落在
        # 区间内 / 区间外的时刻（各离日界 10 小时以上）。
        def _local_at(day, hour):
            local = datetime.combine(day, time(hour, 0), tzinfo=_LOCAL_TZ)
            return local.astimezone(timezone.utc).replace(tzinfo=None)

        today_local = datetime.now(_LOCAL_TZ).date()
        yesterday_local = today_local - timedelta(days=1)
        # 这两条是**探针数据**，验完立刻删掉：后面 [7] 的统计断言写死了
        # 「岗位总数 22 条」、「清空 21 条」，留着会把这些精确计数顶掉。
        probe_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
        with SessionLocal() as db:
            before = job_store.count_posted_yesterday(db)
            db.add(
                JobPosting(
                    job_id=probe_ids[0],
                    company=f"昨日岗位-{tag}",
                    posted_at=_local_at(yesterday_local, 10),
                    created_at=now_utc(),
                )
            )
            db.add(
                JobPosting(
                    job_id=probe_ids[1],
                    company=f"今日岗位-{tag}",
                    posted_at=_local_at(today_local, 10),
                    created_at=now_utc(),
                )
            )
            db.commit()
            after = job_store.count_posted_yesterday(db)
        check("插入「本地昨天」与「本地今天」各一条后，昨日计数只 +1（今天的不计入）",
              after == before + 1, f"{before} -> {after}")

        listing2 = client.get("/api/jobs", headers=bearer(alice_token)).json()
        check("接口返回的 posted_yesterday 与服务端口径一致",
              listing2["posted_yesterday"] == after,
              f"api={listing2['posted_yesterday']} service={after}")
        check("posted_yesterday 不超过岗位库总数",
              listing2["posted_yesterday"] <= listing2["total"],
              f"posted_yesterday={listing2['posted_yesterday']} total={listing2['total']}")

        # 撤掉探针数据，恢复「岗位库 = 20 条示例 + 2 条导入」的状态
        with SessionLocal() as db:
            db.query(JobPosting).filter(JobPosting.job_id.in_(probe_ids)).delete(
                synchronize_session=False
            )
            db.commit()
        check("探针数据已清理（岗位库总数回到 22，不影响后续精确计数断言）",
              client.get("/api/jobs", headers=bearer(alice_token)).json()["total"] == 22,
              str(client.get("/api/jobs", headers=bearer(alice_token)).json()["total"]))

        print("\n[4c] 导入岗位默认【不去重】")
        dup_company = f"重复公司-{tag}"
        # 刻意**不带 dedupe 字段**：验证服务端默认值就是「不去重」。
        # 一份校招汇总表里同一家企业因不同批次 / 岗位方向重复出现是常态，
        # 默认去重会把后面那些行静默吃掉，用户只看到「跳过 N 条」很难判断丢了什么。
        payload = {
            "jobs": [
                {"company": dup_company, "nature": "民企", "positions": "研发岗"},
                {"company": dup_company, "nature": "民企", "positions": "销售岗"},  # 同一批内重复
                {"company": "", "nature": "外企"},  # 空企业名：两种模式下都该跳过
            ]
        }
        r = client.post("/api/jobs/bulk", json=payload, headers=bearer(alice_token))
        res = r.json()
        check("省略 dedupe 时默认不去重：同一企业 2 条都入库、空企业名 1 条跳过",
              res["added"] == 2 and res["skipped"] == 1, str(res))

        rows = [
            it
            for it in client.get("/api/jobs", headers=bearer(alice_token)).json()["items"]
            if it["company"] == dup_company
        ]
        check("重复企业在列表里确实是 2 条（没有被合并）", len(rows) == 2, f"len={len(rows)}")
        check("两条重复岗位各自保留了不同的岗位方向",
              sorted(p for it in rows for p in it["positions"]) == sorted(["研发岗", "销售岗"]),
              str([it["positions"] for it in rows]))

        # 显式 true 才去重：已存在的企业应被跳过
        r2 = client.post(
            "/api/jobs/bulk",
            json={"jobs": [{"company": dup_company}], "dedupe": True},
            headers=bearer(alice_token),
        )
        check("显式 dedupe=True 时已存在的企业被跳过",
              r2.json()["added"] == 0 and r2.json()["skipped"] == 1, str(r2.json()))

        # 清理这 2 条，恢复「20 条示例 + 2 条导入 = 22」的状态
        for it in rows:
            client.delete(f"/api/jobs/{it['job_id']}", headers=bearer(alice_token))
        check("清理后岗位库总数回到 22",
              client.get("/api/jobs", headers=bearer(alice_token)).json()["total"] == 22,
              str(client.get("/api/jobs", headers=bearer(alice_token)).json()["total"]))

        # 示例数据：nature 与 industry 必须**都被填上**且不是同一个值
        # （历史 seed 把「银行 / 互联网」错放在 nature 里，这里防止回退）
        huawei = next(it for it in items if it["company"].startswith("华为"))
        check("示例数据同时带 nature 与 industry",
              bool(huawei["nature"]) and bool(huawei["industry"]),
              f"nature={huawei['nature']!r} industry={huawei['industry']!r}")
        check("示例数据的 nature 是「性质」而非「行业」（银行/互联网不该出现在 nature 里）",
              huawei["nature"] not in ("银行", "互联网", "通信运营", "快消"),
              huawei["nature"])

        target = imported["job_id"]
        other = next(it for it in items if it["company"].startswith("华为"))["job_id"]

        print("\n[5] 岗位信息校验")
        check("企业名称为空 -> 400",
              client.post("/api/jobs", json={"company": "   "}, headers=bearer(alice_token)).status_code == 400)
        check("修改不存在的岗位 -> 404",
              client.put("/api/jobs/nope", json=job_payload, headers=bearer(alice_token)).status_code == 404)
        r = client.put(f"/api/jobs/{target}", json={**job_payload, "company": f"改名公司-{tag}"}, headers=bearer(alice_token))
        check("登录用户可改岗位信息", r.status_code == 200 and r.json()["company"] == f"改名公司-{tag}")
        check("编辑岗位**不改**录入时间（posted_at 是只读的）",
              r.json()["posted_at"] == imported["posted_at"], f"{imported['posted_at']} -> {r.json()['posted_at']}")

        print("\n[6] 投递进度：只能改自己的，且状态受服务端校验")
        check("非法进度 -> 400",
              client.put(f"/api/jobs/{target}/progress", json={"status": "已上岸"}, headers=bearer(alice_token)).status_code == 400)
        check("不存在的岗位 -> 404",
              client.put("/api/jobs/nope/progress", json={"status": "已测评"}, headers=bearer(alice_token)).status_code == 404)
        r = client.put(f"/api/jobs/{target}/progress", json={"status": "已二面"}, headers=bearer(alice_token))
        check("合法进度写入成功并回带进度更新时间", r.status_code == 200 and r.json()["status"] == "已二面" and r.json()["progress_at"],
              str(r.json()))
        client.put(f"/api/jobs/{other}/progress", json={"status": "已Offer"}, headers=bearer(alice_token))
        client.put(f"/api/jobs/{target}/progress", json={"status": "简历初筛"}, headers=bearer(bob_token))

        alice_view = client.get("/api/jobs", headers=bearer(alice_token)).json()["items"]
        bob_view = client.get("/api/jobs", headers=bearer(bob_token)).json()["items"]
        carol_view = client.get("/api/jobs", headers=bearer(carol_token)).json()["items"]
        pick = lambda view, key: next(it for it in view if it["job_id"] == key)["progress"]  # noqa: E731
        check("alice 看到自己的进度", pick(alice_view, target) == "已二面", pick(alice_view, target))
        check("bob 看到的是自己的进度（不会串到 alice 的）", pick(bob_view, target) == "简历初筛", pick(bob_view, target))
        check("**别的登录用户也看不到你的进度**（carol 那份仍是未投递）",
              pick(carol_view, target) == "未投递", pick(carol_view, target))

        r = client.put(f"/api/jobs/{target}/progress", json={"status": "未投递"}, headers=bearer(bob_token))
        bob_view = client.get("/api/jobs", headers=bearer(bob_token)).json()["items"]
        check("选「未投递」= 清除记录（不是存一个未投递状态）",
              r.json()["status"] == "未投递" and pick(bob_view, target) == "未投递")
        with SessionLocal() as db:
            rows = db.query(JobProgress).filter(JobProgress.status == "未投递").count()
            check("存储里不存在「未投递」的进度行", rows == 0, str(rows))

        print("\n[7] 投递统计（只统计已投递；漏斗按阶段递进推算）")
        # alice：改名公司=已二面（stage 5）、华为=已Offer（pass）→ 再挂一条初筛挂
        late = next(it for it in alice_view if it["company"].startswith("字节"))["job_id"]
        client.put(f"/api/jobs/{late}/progress", json={"status": "初筛挂"}, headers=bearer(alice_token))
        s = client.get("/api/jobs/stats", headers=bearer(alice_token)).json()
        check("已投递 3 条 / 岗位总数 22 条", s["applied"] == 3 and s["total"] == 22, f"applied={s['applied']} total={s['total']}")
        check("分组计数：进行中 1 / 已挂 1 / Offer 1",
              (s["active"], s["fail"], s["passed"]) == (1, 1, 1), str((s["active"], s["fail"], s["passed"])))
        funnel = {row["name"]: row["value"] for row in s["funnel"]}
        check("漏斗首级 = 已投递数", funnel["投递"] == 3, str(funnel))
        check("「过初筛」= 2（递进推算：初筛挂不算，另外两条都算过了）", funnel["过初筛"] == 2, str(funnel))
        check("「过初面」= 2（已二面与已Offer 都视为已过初面）", funnel["过初面"] == 2, str(funnel))
        check("「过二面」只算已Offer 的那 1 条", funnel["过二面"] == 1, str(funnel))
        check("「过终面」只算已Offer 的那 1 条", funnel["过终面"] == 1, str(funnel))
        rejected = {row["name"]: row["value"] for row in s["rejected"]}
        check("已挂按环节归因（初筛挂 → 简历初筛挂）", rejected.get("简历初筛挂") == 1 and s["reject_total"] == 1, str(rejected))
        dist = {row["name"]: row["value"] for row in s["status_dist"]}
        check("进度分布按状态计数", dist.get("已二面") == 1 and dist.get("已Offer") == 1, str(dist))
        check("企业性质分布存在且合计 = 已投递数", sum(row["value"] for row in s["nature_dist"]) == 3, str(s["nature_dist"]))

        bob_stats = client.get("/api/jobs/stats", headers=bearer(bob_token)).json()
        check("统计同样按人隔离（bob 已投递 0 条）", bob_stats["applied"] == 0, str(bob_stats["applied"]))
        with SessionLocal() as db:
            rows = db.query(JobProgress).filter(JobProgress.user_id == alice_id).count()
            check("存储层：alice 名下正好 3 条进度行（与统计口径一致）", rows == 3, str(rows))

        print("\n[8] 账号投递概览已随「管理员体系」下线")
        check("账号概览接口不再存在 -> 404",
              client.get("/api/jobs/users", headers=bearer(alice_token)).status_code == 404)
        check("游客访问同样 404（路由整体不存在，不是权限问题）",
              client.get("/api/jobs/users").status_code == 404)
        check("进度隔离由「按登录身份查自己的进度」保证，不再需要看板级接口",
              pick(client.get("/api/jobs", headers=bearer(carol_token)).json()["items"], target) == "未投递")

        print("\n[9] 连带清理：删除岗位 / 清空岗位库不留孤儿进度")
        r = client.delete(f"/api/jobs/{target}", headers=bearer(alice_token))
        check("删除岗位成功并说明连带清除进度", r.status_code == 200 and "投递进度" in r.json()["message"], str(r.json()))
        with SessionLocal() as db:
            left = db.query(JobProgress).filter(JobProgress.job_id == target).count()
            check("该岗位的进度行已被连带删除", left == 0, str(left))
            check("岗位行也已删除", db.get(JobPosting, target) is None)
        check("删除不存在的岗位 -> 404", client.delete("/api/jobs/nope", headers=bearer(alice_token)).status_code == 404)

        r = client.delete("/api/jobs", headers=bearer(bob_token))
        # 22 - 1（上一步删掉的那条）= 21
        check("清空岗位库返回被清掉的条数", r.status_code == 200 and "已清空 21 条" in r.json()["message"], str(r.json()))
        with SessionLocal() as db:
            check("岗位库清空后一条不剩", db.query(JobPosting).count() == 0)
            check("所有人的进度行也一条不剩（不留孤儿数据）", db.query(JobProgress).count() == 0)
        after = client.get("/api/jobs", headers=bearer(alice_token)).json()
        check("清空后列表为空、统计归零",
              after["total"] == 0 and client.get("/api/jobs/stats", headers=bearer(alice_token)).json()["applied"] == 0)

    print("\n" + "=" * 62)
    print(f"  通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("  失败项：")
        for name in FAILED:
            print(f"    - {name}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
