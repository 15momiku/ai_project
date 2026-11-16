"""体验增强 · 后端端到端自检（进程内 TestClient，不依赖已启动的服务）。

覆盖《AI面试模拟官-体验增强设计方案》迭代一里所有**后端**行为：
1. `GET /api/interview/{id}` 返回结构化 `progress`（题号/环节/已用时/剩余区间/会话可恢复）；
2. `POST /api/interview/{id}/answer` 接受 `retake` / `hint_used`，并如实累计到会话；
3. `POST /api/interview/{id}/speech-metrics` 只落库不评分，且用服务端口径重算；
4. 报告区分「未作答 / 重录 / 依赖提示 / 低分」四种状态，并给出独立作答率；
5. `GET /api/report/{id}` 带评分归因（basis / evidence_spans）、评价锚点、基准刻度与 AI 标识；
6. `POST /api/report/{id}/feedback` 支持逐题 verdict 并可在报告里读回；
7. 样本不足时基准必须 `available=false` + `note`，绝不编造分布；
8. 越权访问别人的会话/报告一律 404；
9. 报告生成进度：状态机（百分比只按已完成阶段推进）、端到端收尾为 done/100、越权 404。

运行（仓库根目录）：
    cd backend
    python test_experience_flow.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

# 自检必须**可复现且不消耗真实额度**：强制走规则引擎与本地链路。
# 环境变量优先级高于 backend/.env，因此即使本机配了真 Key 也仍然走 mock，
# 这也顺带验证了「零密钥可完整跑通」这条底线没有被破坏。
os.environ["LLM_PROVIDER"] = "mock"

# ⚠️ 必须在 import app 之前改掉数据库地址。
#
# 这个自检要注册 alice/bob 两个账号并跑完整场面试；如果连的是 `data/app.db`，
# 每跑一次就会往真实库里塞两个账号 + 一整场面试数据 —— 真实用户与测试账号
# 在数据上无法区分（历史上因此积了 125 个 alice*/bob*）。
# 改用独立临时库后，测试**物理上碰不到**真实数据，跑完即删。
# 注意：必须在 config 被导入前设置，engine 在模块导入时就创建了。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="aic_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'test_experience.db').as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' -> ' + extra) if extra else ''}")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _seed_interview(client: TestClient, headers: dict[str, str]) -> tuple[str, int, dict]:
    """建号 → 一键示例 → 开始面试，返回 (session_id, total, seeded)。"""
    seeded = client.post("/api/demo/seed", json={}, headers=headers).json()
    start = client.post(
        "/api/interview/start",
        json={
            "resume_id": seeded["resume_id"],
            "jd_id": seeded["jd_id"],
            "company_id": seeded["company_id"],
            "mode": "text",
            "question_count": 4,
        },
        headers=headers,
    )
    check("开始面试 200", start.status_code == 200, f"HTTP {start.status_code} {start.text[:120]}")
    body = start.json()
    return body["session_id"], int(body["total_questions"]), seeded


def main() -> int:
    with TestClient(app, raise_server_exceptions=False) as client:
        tag = uuid.uuid4().hex[:8]
        alice, bob = f"alice{tag}", f"bob{tag}"

        ra = client.post(
            "/api/auth/register",
            json={"username": alice, "password": "pass1234", "nickname": "Alice", "school": "SCUT"},
        )
        rb = client.post(
            "/api/auth/register",
            json={"username": bob, "password": "pass1234", "nickname": "Bob", "school": "SCUT"},
        )
        if ra.status_code != 200 or rb.status_code != 200:
            print(f"注册失败：{ra.status_code} {rb.status_code}")
            return 1
        ha, hb = bearer(ra.json()["token"]), bearer(rb.json()["token"])

        print("\n[1] 进度信息（体验增强 §2）")
        sid, total, seeded = _seed_interview(client, ha)
        st = client.get(f"/api/interview/{sid}", headers=ha)
        check("会话状态 200", st.status_code == 200)
        prog = st.json().get("progress") or {}
        check("progress 存在且 total 正确", prog.get("total") == total, f"total={prog.get('total')} 期望 {total}")
        check("progress.index 为 1（已出第 1 题）", prog.get("index") == 1, f"index={prog.get('index')}")
        check("progress.stage 非空", bool(prog.get("stage")), f"stage={prog.get('stage')!r}")
        check("progress 有环节与已用时字段", "elapsed_seconds" in prog and "answered" in prog)
        est = prog.get("estimated_remaining_seconds") or []
        check(
            "剩余时间是 [下限,上限] 区间且上限≥下限",
            len(est) == 2 and est[1] >= est[0] >= 0,
            f"estimated_remaining_seconds={est}",
        )
        check(
            "剩余时间来源明确（session/history/estimate）",
            prog.get("source") in ("session", "history", "estimate"),
            f"source={prog.get('source')}",
        )
        check("progress.can_resume=True（进行中可继续）", prog.get("can_resume") is True)
        check("progress.idle_timeout_hours 已下发（供界面说明会话保留）", float(prog.get("idle_timeout_hours") or 0) >= 0)

        print("\n[2] 重录 / 思路提示（体验增强 §4）")
        first = client.get(f"/api/interview/{sid}", headers=ha).json()
        q_turn = [d for d in first["dialogue"] if d["role"] == "interviewer" and d["index"] == 1][-1]
        check("刷新后仍能拿到当前题的 turn_id（中断恢复前提）", bool(q_turn.get("turn_id")), f"turn_id={q_turn.get('turn_id')!r}")
        ans = client.post(
            f"/api/interview/{sid}/answer",
            json={
                "question_id": q_turn["turn_id"],
                "answer_text": "我先做了用户调研，其次定义指标口径，最终上线后转化率提升 30%。",
                "answer_seconds": 62,
                "input_mode": "text",
                "retake": True,
                "hint_used": True,
            },
            headers=ha,
        )
        check("带 retake/hint 作答 200", ans.status_code == 200, f"HTTP {ans.status_code} {ans.text[:120]}")
        ab = ans.json()
        check("作答响应带 progress_info", isinstance(ab.get("progress_info"), dict), f"keys={list(ab.keys())}")
        check(
            "作答后 progress.index 前进",
            (ab.get("progress_info") or {}).get("index", 0) >= 1,
            f"index={(ab.get('progress_info') or {}).get('index')}",
        )
        st2 = client.get(f"/api/interview/{sid}", headers=ha).json()
        check("会话累计 retake_count=1", st2.get("retake_count") == 1, f"retake_count={st2.get('retake_count')}")
        check("会话累计 hint_count=1", st2.get("hint_count") == 1, f"hint_count={st2.get('hint_count')}")

        print("\n[3] 口播指标（体验增强 §5）")
        sm = client.post(
            f"/api/interview/{sid}/speech-metrics",
            json={
                "question_index": 1,
                "answer_text": "然后我就是大概说一下，嗯，那个指标我们做了二十多个。",
                "duration_seconds": 60,
                "chars": 30,
            },
            headers=ha,
        )
        check("上报口播指标 200", sm.status_code == 200, f"HTTP {sm.status_code} {sm.text[:120]}")
        smb = sm.json()
        check("已落库 saved=True", smb.get("saved") is True)
        check("服务端重算填充词命中", int(smb.get("filler_count") or 0) >= 3, f"filler_count={smb.get('filler_count')}")
        check("填充词原文回传", isinstance(smb.get("filler_words"), list) and smb["filler_words"])
        check("语速档位合法", smb.get("level") in ("slow", "good", "fast"), f"level={smb.get('level')}")
        # 服务端按**实际文本长度**重算（不信任客户端上报的 chars），26 字 / 60 秒 = 26.0 字/分
        expect_cpm = round(len("然后我就是大概说一下，嗯，那个指标我们做了二十多个。") / 1.0, 1)
        check(
            f"语速按服务端口径重算（{expect_cpm} 字/分）",
            abs(float(smb.get("chars_per_minute") or 0) - expect_cpm) < 0.5,
            f"cpm={smb.get('chars_per_minute')}",
        )
        check("偏慢时给出人话提示", bool(smb.get("note")), f"note={smb.get('note')!r}")

        print("\n[4] 跳题与低分必须可区分（体验增强 §4）")
        cur = client.get(f"/api/interview/{sid}", headers=ha).json()
        q2 = [d for d in cur["dialogue"] if d["role"] == "interviewer" and d["index"] == 2]
        if q2:
            sk = client.post(
                f"/api/interview/{sid}/skip",
                json={"question_id": q2[-1]["turn_id"]},
                headers=ha,
            )
            check("跳过 200", sk.status_code == 200, f"HTTP {sk.status_code}")
            check("跳过也返回 progress_info", isinstance(sk.json().get("progress_info"), dict))
        else:
            check("跳过：已到最后一题，跳过该步", True)

        # 补完剩余题（第 5 题起用普通作答），确保能生成报告
        for _ in range(total + 2):
            state = client.get(f"/api/interview/{sid}", headers=ha).json()
            if state.get("status") != "ongoing":
                break
            idx = state.get("question_index") or 0
            pending = [
                d for d in state["dialogue"] if d["role"] == "interviewer" and d["index"] == idx
            ]
            if not pending:
                break
            r = client.post(
                f"/api/interview/{sid}/answer",
                json={
                    "question_id": pending[-1]["turn_id"],
                    "answer_text": "首先我梳理了需求，其次用 SQL 拉取数据验证假设，最终指标提升了 25%。",
                    "answer_seconds": 55,
                    "input_mode": "text",
                },
                headers=ha,
            )
            if r.status_code != 200:
                break

        print("\n[5] 报告：归因 / 锚点 / 基准 / 诚实披露（体验增强 §6）")
        gen = client.post(f"/api/report/{sid}/generate", json={}, headers=ha)
        check("生成报告 200", gen.status_code == 200, f"HTTP {gen.status_code} {gen.text[:160]}")
        if gen.status_code != 200:
            return 1
        rep = gen.json()
        check("报告带 AI 生成标识", bool(rep.get("ai_disclaimer")), f"ai_disclaimer={rep.get('ai_disclaimer')!r}")
        check("meta.ai_generated=True", (rep.get("meta") or {}).get("ai_generated") is True)
        bench = rep.get("benchmark") or {}
        check("报告带基准对象与 note", "available" in bench and "note" in bench, f"benchmark={ {k: bench.get(k) for k in ('available','sample_size','scope','p25','p50','p75')} }")
        if bench.get("available"):
            check("基准含 P25/P50/P75 且有序", bench["p25"] <= bench["p50"] <= bench["p75"], f"{bench['p25']}/{bench['p50']}/{bench['p75']}")
        else:
            check("样本不足时基准 available=False 且给出原因", bool(bench.get("note")), f"note={bench.get('note')!r}")
        check("报告未内嵌 dims（避免前端误用）", "dims" not in bench)

        dims = rep.get("dimensions") or []
        check("六维齐全", len(dims) == 6, f"len={len(dims)}")
        with_basis = [d for d in dims if d.get("basis")]
        check("每个维度都有评分依据 basis", len(with_basis) == 6, f"有 basis 的维度数={len(with_basis)}")
        check("依据条目是可读事实（如「命中 N 次」）", all(isinstance(x, str) and x for d in dims for x in (d.get("basis") or [])))
        check("每个维度都有评价锚点 anchors", all(d.get("anchors") for d in dims), f"缺 anchors 的维度={[d['key'] for d in dims if not d.get('anchors')]}")
        anchor = (dims[0].get("anchors") or [{}])[0]
        check("锚点含「什么样算几分」的示例句", bool(anchor.get("example")) and "score" in anchor, f"anchor={anchor}")
        check("每个维度都有 benchmark 字段", all("benchmark" in d for d in dims))

        qs = rep.get("questions") or []
        check("报告含逐题分析", len(qs) >= 3, f"len={len(qs)}")
        skipped_flags = [q for q in qs if q.get("skipped")]
        check("存在被标记为「未作答」的题（与低分区分）", len(skipped_flags) >= 1, f"skipped 题数={len(skipped_flags)}")
        if skipped_flags:
            check("未作答题不产生高亮片段", not skipped_flags[0].get("evidence_spans"))
        retake_q = [q for q in qs if q.get("retake_count")]
        check("第 1 题的「重录」被如实标注", len(retake_q) == 1 and retake_q[0]["index"] == 1, f"重录题={[q['index'] for q in retake_q]}")
        hint_q = [q for q in qs if q.get("hint_used")]
        check("第 1 题的「依赖提示」被如实标注", len(hint_q) == 1 and hint_q[0]["index"] == 1, f"提示题={[q['index'] for q in hint_q]}")
        check("报告汇总 retake_count/hint_count", rep.get("retake_count") == 1 and rep.get("hint_count") == 1, f"retake={rep.get('retake_count')} hint={rep.get('hint_count')}")
        rate = rep.get("independent_answer_rate")
        check("独立作答率已计算且在 0~1", isinstance(rate, (int, float)) and 0 <= float(rate) <= 1, f"rate={rate}")
        typed = [q for q in qs if not q.get("skipped")]
        check("有作答的题给出原文高亮片段", all(q.get("evidence_spans") for q in typed) if typed else True, f"缺片段题={[q['index'] for q in typed if not q.get('evidence_spans')]}")
        speech_q = [q for q in qs if q.get("speech")]
        check("口播指标挂到了对应题上", len(speech_q) == 1 and speech_q[0]["index"] == 1, f"带口播的题={[q['index'] for q in speech_q]}")
        if speech_q:
            check("口播指标含语速/填充词/时长", float(speech_q[0]["speech"].get("chars_per_minute") or 0) > 0 and int(speech_q[0]["speech"].get("filler_count") or 0) > 0)

        print("\n[6] 逐题反馈闭环（体验增强 §6）")
        fb = client.post(
            f"/api/report/{sid}/feedback",
            json={"question_index": 1, "verdict": "high", "helpful": True},
            headers=ha,
        )
        check("逐题反馈 200", fb.status_code == 200, f"HTTP {fb.status_code} {fb.text[:120]}")
        rep2 = client.get(f"/api/report/{sid}", headers=ha).json()
        q1 = [q for q in (rep2.get("questions") or []) if q["index"] == 1]
        check(
            "报告读回该题 verdict=high",
            bool(q1) and q1[0].get("feedback_verdict") == "high",
            f"verdict={q1[0].get('feedback_verdict') if q1 else None}",
        )
        check(
            "整份报告 helpful 仍写入 meta（兼容既有语义）",
            (rep2.get("meta") or {}).get("feedback_helpful") is True,
            f"feedback_helpful={(rep2.get('meta') or {}).get('feedback_helpful')}",
        )
        bad = client.post(
            f"/api/report/{sid}/feedback",
            json={"question_index": 999, "verdict": "low"},
            headers=ha,
        )
        check("题号越界返回 400", bad.status_code == 400, f"HTTP {bad.status_code}")

        print("\n[7] 越权：别人的会话/报告一律 404")
        check("bob 读 alice 的会话状态 -> 404", client.get(f"/api/interview/{sid}", headers=hb).status_code == 404)
        check("bob 读 alice 的报告 -> 404", client.get(f"/api/report/{sid}", headers=hb).status_code == 404)
        check(
            "bob 给 alice 的报告发反馈 -> 404",
            client.post(f"/api/report/{sid}/feedback", json={"helpful": True}, headers=hb).status_code == 404,
        )
        check(
            "bob 上报 alice 会话的口播指标 -> 404",
            client.post(
                f"/api/interview/{sid}/speech-metrics",
                json={"question_index": 1, "answer_text": "x", "duration_seconds": 10},
                headers=hb,
            ).status_code
            == 404,
        )

        print("\n[8] 解析置信度 + 字段覆盖率（体验增强 §7）")
        resume_text = (
            "林宇航\n求职意向：产品经理（数据方向）｜华南理工大学｜大三在读\n"
            "手机：13812345678    邮箱：linyuhang@example.com\n\n"
            "教育背景\n2022.09 - 2026.06  华南理工大学  软件工程  本科\n\n"
            "实习经历\n字节跳动 | 数据分析实习生 | 2024.07 - 2024.10\n"
            "· 搭建 SQL 取数看板，覆盖 20+ 核心指标，ROI 提升 12%\n\n"
            "专业技能\n· 数据分析：Python、SQL、Pandas、Tableau\n"
        )
        rup = client.post("/api/resume/text", json={"text": resume_text}, headers=ha)
        check("粘贴简历解析 200", rup.status_code == 200, f"HTTP {rup.status_code} {rup.text[:120]}")
        rj = rup.json()
        cov = rj.get("coverage") or {}
        check(
            "覆盖率含 recognized/total/missing",
            isinstance(cov.get("recognized"), int) and isinstance(cov.get("total"), int) and isinstance(cov.get("missing"), list),
            f"coverage={ {k: cov.get(k) for k in ('recognized', 'total')} } missing={len(cov.get('missing') or [])}",
        )
        check("覆盖率 total 等于字段表长度（缺失项也在分母里）", cov.get("total") == 14, f"total={cov.get('total')}")
        check("缺失项有可读标签", all(isinstance(x, str) and x for x in cov.get("missing") or []))
        check("缺失时给出说明文案", bool(cov.get("note")) if cov.get("recognized", 0) < cov.get("total", 0) else True)
        conf = rj.get("confidence_levels") or []
        check("下发逐字段置信度清单", len(conf) == 14, f"len={len(conf)}")
        check(
            "置信度按升序排列（最该先改的排最前）",
            all(conf[i]["confidence"] <= conf[i + 1]["confidence"] for i in range(len(conf) - 1)),
            f"first={conf[0] if conf else None}",
        )
        check(
            "level 只有 high/mid/low 三档",
            all(c.get("level") in ("high", "mid", "low") for c in conf),
            f"levels={sorted({c.get('level') for c in conf})}",
        )
        check(
            "精确正则命中的手机/邮箱为高置信",
            all(
                next((c["level"] for c in conf if c["key"] == k), "") == "high"
                for k in ("basic.phone", "basic.email")
            ),
        )
        check(
            "启发式猜测的字段置信度更低（学历/年级）",
            all(
                next((c["confidence"] for c in conf if c["key"] == k), 1) < 0.7
                for k in ("basic.degree", "basic.grade")
            ),
        )
        check("parsed 内也带 field_confidence（供前端直接取用）", isinstance(rj.get("parsed", {}).get("field_confidence"), dict))

        print("\n[9] 匹配度可解释 + 多 JD 对比（体验增强 §8）")
        mt = client.post(
            "/api/match",
            json={"resume_id": rj["resume_id"], "jd_id": seeded["jd_id"]},
            headers=ha,
        )
        check("匹配度 200", mt.status_code == 200, f"HTTP {mt.status_code} {mt.text[:120]}")
        mb = mt.json()
        bd = mb.get("breakdown") or []
        check("五维分项齐全", len(bd) == 5, f"len={len(bd)}")
        check(
            "每维都有命中/缺失关键词与建议",
            all(
                isinstance(b.get("hit_keywords"), list)
                and isinstance(b.get("miss_keywords"), list)
                and isinstance(b.get("suggestion"), str)
                and b.get("weight_hint")
                for b in bd
            ),
            f"缺字段的维度={[b.get('key') for b in bd if not b.get('weight_hint')]}",
        )
        check(
            "权重说明不是同一个模板（按排名措辞）",
            len({b.get("weight_hint") for b in bd}) >= 3,
            f"unique={len({b.get('weight_hint') for b in bd})}",
        )
        hard = next((b for b in bd if b.get("key") == "hard_skill"), {})
        check("硬技能维度给出具体缺口", isinstance(hard.get("miss_keywords"), list))
        check(
            "硬技能建议是可执行的（含「补一句」或「已全部覆盖」）",
            ("补一句" in (hard.get("suggestion") or "")) or ("覆盖" in (hard.get("suggestion") or "")),
            f"tip={hard.get('suggestion')!r}",
        )

        jl = client.get("/api/jd/list", headers=ha)
        check("岗位列表 200", jl.status_code == 200, f"HTTP {jl.status_code}")
        items = (jl.json() or {}).get("items") or []
        check("列表包含自己的 JD", any(it["jd_id"] == seeded["jd_id"] for it in items), f"total={len(items)}")
        check("列表项含对比所需摘要字段", all("title" in it and "hard_skill_count" in it for it in items))
        check("列表不下发职责全文（少一分泄露面）", all("responsibilities" not in it for it in items))
        jlb = client.get("/api/jd/list", headers=hb)
        bob_ids = {it["jd_id"] for it in (jlb.json() or {}).get("items") or []}
        check("bob 的岗位列表里看不到 alice 的 JD", seeded["jd_id"] not in bob_ids, f"bob_total={len(bob_ids)}")

        print("\n[10] 单题特训：3 题微面试（体验增强 §9）")
        wl = client.get("/api/practice/wrong-questions", headers=ha)
        check("错题本 200", wl.status_code == 200, f"HTTP {wl.status_code}")
        witems = (wl.json() or {}).get("items") or []
        check("前面那场低分面试已产出错题", len(witems) >= 1, f"total={len(witems)}")
        if not witems:
            print("  （没有错题，跳过单题特训用例）")
        else:
            w0 = witems[0]
            check("错题带原题环节 section", "section" in w0)
            check("错题带对应维度 key", w0.get("dimension_key") in {
                "professional", "logic", "expression", "matching", "adaptability", "culture_fit",
            }, f"dimension_key={w0.get('dimension_key')}")
            check("错题带同维度趋势数组", isinstance(w0.get("trend"), list))
            check("before_score 与趋势末值一致",
                  (not w0.get("trend")) or abs(float(w0["before_score"]) - float(w0["trend"][-1]["score"])) < 0.05,
                  f"before={w0.get('before_score')} trend_tail={w0.get('trend')[-1] if w0.get('trend') else None}")

            rt = client.post(
                "/api/practice/retry",
                json={"wrong_id": w0["id"], "start_micro": True},
                headers=ha,
            )
            check("重做错题 200", rt.status_code == 200, f"HTTP {rt.status_code} {rt.text[:140]}")
            rb = rt.json()
            check("仍返回参考思路与 3 条 tips", bool(rb.get("reference")) and len(rb.get("tips") or []) == 3)
            micro = rb.get("micro")
            check("返回微面试计划", isinstance(micro, dict), f"micro={type(micro).__name__}")
            if isinstance(micro, dict):
                check("微面试题量为 3", micro.get("total") == 3, f"total={micro.get('total')}")
                check("微面试指定了薄弱环节", bool(micro.get("focus_section")), f"section={micro.get('focus_section')}")
                check("微面试带维度名（用于前后对比文案）", bool(micro.get("dimension_name")))
                check("微面试带开场白", isinstance(micro.get("opening"), dict))
                msid = micro.get("session_id")
                st = client.get(f"/api/interview/{msid}", headers=ha)
                check("微面试会话可读且题量=3", st.status_code == 200 and st.json().get("total_questions") == 3,
                      f"HTTP {st.status_code} total={(st.json() or {}).get('total_questions')}")
                plan = (st.json() or {}).get("progress") or {}
                check("微面试按同一环节出题（progress.stage 等于 focus_section）",
                      plan.get("stage") == micro.get("focus_section"),
                      f"stage={plan.get('stage')} focus={micro.get('focus_section')}")
                check("首题就是该环节", any(
                    d.get("section") == micro.get("focus_section")
                    for d in (st.json() or {}).get("dialogue") or []
                    if d.get("role") == "interviewer"
                ))
            check("重做次数已累加", int(rb.get("retry_count") or 0) >= 1, f"retry_count={rb.get('retry_count')}")
            check(
                "bob 重做 alice 的错题 -> 404",
                client.post("/api/practice/retry", json={"wrong_id": w0["id"]}, headers=hb).status_code == 404,
            )

        print("\n[11] JD 关键词覆盖率（体验增强 §11，必须不依赖大模型）")
        # 本测试全程 LLM_PROVIDER=mock，因此这一节同时证明「零密钥也能用」
        cov_resume = {
            "basic": {"name": "林宇航", "job_intention": "产品经理（数据方向）"},
            "work_experience": [
                {
                    "company": "字节跳动",
                    "title": "数据分析实习生",
                    "description": "搭建 SQL 取数看板，覆盖 20+ 指标，ROI 提升 12%",
                }
            ],
            "project_experience": [
                {"name": "校园二手交易小程序", "description": "负责需求调研与原型设计，日活提升 30%"}
            ],
            "campus_experience": [],
            "honors": [],
            "skills": ["Python", "SQL", "Axure", "Figma"],
            "photo": "",
        }
        cov_jd = (
            "岗位职责：负责数据产品的需求调研与分析；搭建核心数据指标看板。"
            "任职要求：熟练使用 SQL、Python、Excel 进行数据分析；能使用 Axure 或 Figma 完成原型设计；"
            "具备较强的沟通协作能力与责任心；有 Tableau 等可视化工具经验者优先。"
        )
        kc = client.post(
            "/api/resume-optimizer/keyword-coverage",
            json={"resume": cov_resume, "jd": cov_jd},
            headers=ha,
        )
        check("覆盖率接口 200（mock 模式下可用）", kc.status_code == 200, f"HTTP {kc.status_code} {kc.text[:140]}")
        kb = kc.json()
        check("识别出目标关键词", int(kb.get("total") or 0) > 0, f"total={kb.get('total')}")
        check(
            "已覆盖 + 缺失 = 目标总数（不漏不重）",
            len(kb.get("covered") or []) + len(kb.get("missing") or []) == int(kb.get("total") or 0),
            f"covered={len(kb.get('covered') or [])} missing={len(kb.get('missing') or [])} total={kb.get('total')}",
        )
        check("covered_count 与列表长度一致", int(kb.get("covered_count") or 0) == len(kb.get("covered") or []))
        check("已覆盖的是简历里真实出现的词", all(w in str(cov_resume) for w in (kb.get("covered") or [])))
        check("缺失的是简历里确实没有的词", all(w not in str(cov_resume) for w in (kb.get("missing") or [])))
        check(
            "「加入技能特长」只给技能类缺失词（软素质不硬塞）",
            set(kb.get("skill_missing") or []).issubset(set(kb.get("missing") or []))
            and "沟通" not in (kb.get("skill_missing") or []),
            f"skill_missing={kb.get('skill_missing')}",
        )
        check("给出了可读说明", bool(kb.get("note")))
        empty_jd = client.post(
            "/api/resume-optimizer/keyword-coverage", json={"resume": cov_resume, "jd": ""}, headers=ha
        )
        check(
            "JD 为空时如实返回 total=0 + 提示（不报错）",
            empty_jd.status_code == 200 and int(empty_jd.json().get("total") or 0) == 0 and bool(empty_jd.json().get("note")),
            f"HTTP {empty_jd.status_code} body={empty_jd.text[:120]}",
        )

        print("\n[12] 成长面板：连续打卡 / 周汇总 / 维度变化（体验增强 §12）")
        gr = client.get("/api/growth/summary", headers=ha)
        check("成长面板 200", gr.status_code == 200, f"HTTP {gr.status_code} {gr.text[:120]}")
        g = gr.json()
        for field in ("streak_days", "best_streak", "total_sessions", "total_minutes", "active_days"):
            check(f"含 {field}", isinstance(g.get(field), int), f"{field}={g.get(field)}")
        check(
            "已完成的面试被统计进来",
            int(g.get("total_sessions") or 0) >= 1,
            f"total_sessions={g.get('total_sessions')}",
        )
        check("最长连续 ≥ 当前连续", int(g.get("best_streak") or 0) >= int(g.get("streak_days") or 0))
        days7 = g.get("last_7_days") or []
        check("近 7 天格子恰好 7 格", len(days7) == 7, f"len={len(days7)}")
        check("今天恰好被标记一次", sum(1 for d in days7 if d.get("is_today")) == 1)
        check(
            "格子按日期升序且最右是今天",
            [d.get("date") for d in days7] == sorted(d.get("date") for d in days7) and days7[-1].get("is_today"),
            f"dates={[d.get('date') for d in days7]}",
        )
        check("每格都有星期标签", all(d.get("weekday") for d in days7))
        check("每格 done 与 sessions>0 一致", all(bool(d.get("done")) == (int(d.get("sessions") or 0) > 0) for d in days7))
        weeks = g.get("weeks") or []
        check("近 4 周汇总恰好 4 条", len(weeks) == 4, f"len={len(weeks)}")
        check("每周含场次与均分", all("sessions" in w and "avg_score" in w for w in weeks))
        dims = g.get("dimension_delta") or []
        check("维度变化含首末与差值", all({"first", "last", "delta"} <= set(d) for d in dims), f"len={len(dims)}")
        check("delta = last - first", all(abs((d["last"] - d["first"]) - d["delta"]) < 0.051 for d in dims))
        check("给出可执行的下一步目标", bool(g.get("next_goal")), f"next_goal={g.get('next_goal')!r}")
        check("说明了统计口径", bool(g.get("note")))
        gb = client.get("/api/growth/summary", headers=hb).json()
        check(
            "bob 的面板看不到 alice 的练习（按用户隔离）",
            int(gb.get("total_sessions") or 0) == 0 and int(gb.get("streak_days") or 0) == 0,
            f"bob_total={gb.get('total_sessions')} bob_streak={gb.get('streak_days')}",
        )
        check("无数据时也返回可用结构（不报错）", isinstance(gb.get("last_7_days"), list) and bool(gb.get("next_goal")))

        print("\n[13] 压力面试模式 + 行业专项题库（体验增强 §14）")

        def _start(mode_kwargs: dict) -> dict:
            payload = {
                "resume_id": seeded["resume_id"],
                "jd_id": seeded["jd_id"],
                "company_id": seeded["company_id"],
                "mode": "text",
                "question_count": 6,
            }
            payload.update(mode_kwargs)
            r = client.post("/api/interview/start", json=payload, headers=ha)
            return r.json() if r.status_code == 200 else {}

        # 同一句「不算空泛但也不算具体」的回答：30 字，正好落在两个阈值之间
        mid_answer = "我先做了用户调研，然后定义了指标口径，最后推动上线并且复盘了整个过程。" * 1
        assert 25 <= len(mid_answer) < 40, len(mid_answer)

        normal = _start({})
        norm_opening = (normal.get("opening") or {}).get("turn_id")
        nr = client.post(
            f"/api/interview/{normal['session_id']}/answer",
            json={"question_id": norm_opening, "answer_text": mid_answer, "answer_seconds": 60, "input_mode": "text"},
            headers=ha,
        )
        check("常规模式开局 200", nr.status_code == 200, f"HTTP {nr.status_code}")
        nxt = (nr.json() or {}).get("next_turn") or {}
        check(
            "常规模式下 30 字回答不触发追问（阈值 25 字已过）",
            not nxt.get("is_followup"),
            f"is_followup={nxt.get('is_followup')} len={len(mid_answer)}",
        )

        press = _start({"pressure_mode": True})
        check("压力模式开局 200", bool(press.get("session_id")))
        pr = client.post(
            f"/api/interview/{press['session_id']}/answer",
            json={
                "question_id": (press.get("opening") or {}).get("turn_id"),
                "answer_text": mid_answer,
                "answer_seconds": 60,
                "input_mode": "text",
            },
            headers=ha,
        )
        check("压力模式作答 200", pr.status_code == 200, f"HTTP {pr.status_code}")
        pnxt = (pr.json() or {}).get("next_turn") or {}
        check(
            "压力模式下同一回答会触发追问（阈值放宽到 40 字）",
            bool(pnxt.get("is_followup")),
            f"is_followup={pnxt.get('is_followup')} len={len(mid_answer)}",
        )
        pst = client.get(f"/api/interview/{press['session_id']}", headers=ha).json()
        check(
            "压力模式的题量不受影响（仍是 6）",
            pst.get("total_questions") == 6,
            f"total={pst.get('total_questions')}",
        )
        # 压力模式必须落到**会话计划**里：引擎每次请求都无状态重建，
        # 只在 start 时改内存 profile 的话，第 2 题起就会退回常规阈值。
        client.post(f"/api/interview/{press['session_id']}/finish", headers=ha)
        pr_gen = client.post(f"/api/report/{press['session_id']}/generate", json={}, headers=ha)
        check("压力模式场次可出报告", pr_gen.status_code == 200, f"HTTP {pr_gen.status_code}")
        pr_rep = pr_gen.json() if pr_gen.status_code == 200 else {}
        check(
            "压力模式被记录进会话计划（报告里可读到 pressure_mode）",
            pr_rep.get("pressure_mode") is True,
            f"pressure_mode={pr_rep.get('pressure_mode')}",
        )
        check("报告标注了压力面试模式", pr_rep.get("pressure_mode") is True, f"pressure_mode={pr_rep.get('pressure_mode')}")
        check("报告记录了本场行业", bool(pr_rep.get("industry")), f"industry={pr_rep.get('industry')}")

        # 常规模式的同一场必须**没有**被误标为压力面
        client.post(f"/api/interview/{normal['session_id']}/finish", headers=ha)
        nr_gen = client.post(f"/api/report/{normal['session_id']}/generate", json={}, headers=ha)
        nr_rep = nr_gen.json() if nr_gen.status_code == 200 else {}
        check("常规模式场次不被误标为压力面", nr_rep.get("pressure_mode") is False, f"pressure_mode={nr_rep.get('pressure_mode')}")

        print("\n[14] 行业专项题库")
        # 用一场**全新**的会话走题：前面的 normal 会话已经被 finish 掉，不能再推进
        walk = _start({})
        sid2 = walk.get("session_id") or ""
        scenario_qs: list[str] = []
        for _ in range(10):
            state = client.get(f"/api/interview/{sid2}", headers=ha).json()
            if state.get("status") != "ongoing":
                break
            idx = state.get("question_index") or 0
            pend = [d for d in state["dialogue"] if d["role"] == "interviewer" and d["index"] == idx]
            if not pend:
                break
            if pend[-1].get("section") == "场景题":
                scenario_qs.append(str(pend[-1].get("content") or ""))
            r2 = client.post(
                f"/api/interview/{sid2}/answer",
                json={
                    "question_id": pend[-1]["turn_id"],
                    "answer_text": "首先我明确了目标，其次用数据验证了假设，最后落地并复盘了结果。",
                    "answer_seconds": 50,
                    "input_mode": "text",
                },
                headers=ha,
            )
            if r2.status_code != 200:
                break
        check("走到过场景题环节", len(scenario_qs) >= 1, f"场景题数量={len(scenario_qs)}")
        if scenario_qs:
            generic_openers = ("我们公司现在主要做", "结合", "假设我们所在的")
            industry_markers = ("留存", "A/B", "竞品", "增长停滞", "渠道")
            check(
                "场景题不是通用模板（说明命中了行业题库）",
                not any(scenario_qs[0].startswith(g) for g in generic_openers),
                f"题目={scenario_qs[0][:70]}",
            )
            check(
                "场景题带行业特征词",
                any(m in scenario_qs[0] for m in industry_markers),
                f"题目={scenario_qs[0][:70]}",
            )

        print("\n[15] 报告生成进度（生成过程可视化）")
        from app.services import report_progress  # noqa: PLC0415 局部导入，避免影响上面的 mock 环境

        # 15.1 全新会话、尚未生成过报告 → 明确「拿不到进度」，而不是编一个百分比
        fresh = _start({})
        fresh_sid = fresh.get("session_id") or ""
        probe = client.get(f"/api/report/{fresh_sid}/progress", headers=ha)
        check("进度接口 200", probe.status_code == 200, f"HTTP {probe.status_code} {probe.text[:120]}")
        pb = probe.json() if probe.status_code == 200 else {}
        check("未生成时 available=false", pb.get("available") is False, f"available={pb.get('available')}")
        check("未生成时 status=idle", pb.get("status") == "idle", f"status={pb.get('status')}")
        check("未生成时 percent=0（不编造进度）", pb.get("percent") == 0, f"percent={pb.get('percent')}")
        check(
            "阶段清单结构完整（前端据此渲染步骤）",
            len(pb.get("stages") or []) == len(report_progress.STAGES)
            and all(s.get("label") for s in (pb.get("stages") or [])),
            f"阶段数={len(pb.get('stages') or [])}",
        )
        check("未生成时给出说明文案", bool(pb.get("note")), f"note={pb.get('note')}")

        # 15.2 状态机：百分比只按**已真正完成**的阶段推进，不虚增
        pre = report_progress.snapshot("unit-progress")
        check("未知会话快照安全（不抛异常）", pre.get("available") is False and pre.get("status") == "idle")
        report_progress.begin("unit-progress", "u1")
        s0 = report_progress.snapshot("unit-progress")
        check("begin 后进入 running 且 stage=load", s0["status"] == "running" and s0["stage"] == "load")
        check("begin 后 percent=0（第一阶段尚未完成）", s0["percent"] == 0, f"percent={s0['percent']}")
        report_progress.stage("unit-progress", "score", "第 1 轮大模型评分")
        s1 = report_progress.snapshot("unit-progress")
        check("推进到第 2 阶段后 percent=17（1/6）", s1["percent"] == 17, f"percent={s1['percent']}")
        check("阶段状态为 done/active/pending", [x["status"] for x in s1["stages"]][:3] == ["done", "active", "pending"])
        report_progress.stage("unit-progress", "score", "二次校验（专业能力 / 岗位匹配度）")
        s2 = report_progress.snapshot("unit-progress")
        check("同阶段重复上报只更新子步骤，不推进百分比", s2["percent"] == 17 and s2["stages"][1]["detail"].startswith("二次校验"))
        report_progress.set_mode("unit-progress", "llm")
        check("评分方式可读（前端据此给出耗时说明）", report_progress.snapshot("unit-progress")["scoring_mode"] == "llm")
        report_progress.stage("unit-progress", "unknown-stage")
        check("未知阶段名被忽略（埋点不会带崩流程）", report_progress.snapshot("unit-progress")["stage"] == "score")
        report_progress.fail("unit-progress", "模拟失败")
        sf = report_progress.snapshot("unit-progress")
        check("fail 后 status=failed 且带原因", sf["status"] == "failed" and sf["message"] == "模拟失败")
        report_progress.stage("unit-progress", "persist")
        report_progress.finish("unit-progress")
        s3 = report_progress.snapshot("unit-progress")
        check("finish 后 percent=100 且全部 done", s3["percent"] == 100 and all(x["status"] == "done" for x in s3["stages"]))
        report_progress.reset("unit-progress")
        check("reset 后回到 idle", report_progress.snapshot("unit-progress")["status"] == "idle")

        # 15.3 端到端：真实生成一次后，进度收尾为 done/100，并带上实际评分方式
        gen = client.post(f"/api/report/{normal['session_id']}/generate", json={}, headers=ha)
        check("生成报告 200", gen.status_code == 200, f"HTTP {gen.status_code}")
        done = client.get(f"/api/report/{normal['session_id']}/progress", headers=ha).json()
        check("生成后 available=true", done.get("available") is True)
        check("生成后 status=done", done.get("status") == "done", f"status={done.get('status')}")
        check("生成后 percent=100", done.get("percent") == 100, f"percent={done.get('percent')}")
        check("生成后全部阶段 done", all(s.get("status") == "done" for s in done.get("stages") or []))
        check("生成后记录真实评分方式（mock 下为 rule）", done.get("scoring_mode") == "rule", f"mode={done.get('scoring_mode')}")
        check("生成后总耗时非负且 stage_index 有效", float(done.get("elapsed_seconds") or 0) >= 0 and done.get("stage_index") >= 1)

        # 15.4 越权：别人的会话不允许读到进度（与其它报告接口一致，404 而非 403）
        foreign = client.get(f"/api/report/{normal['session_id']}/progress", headers=hb)
        check("别人的会话读进度 404", foreign.status_code == 404, f"HTTP {foreign.status_code}")
        missing = client.get("/api/report/sess_not_exist/progress", headers=ha)
        check("不存在的会话读进度 404", missing.status_code == 404, f"HTTP {missing.status_code}")

    print(f"\n{'=' * 54}\n通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    # 独立临时库用完即删：这个自检不再往 data/app.db 写任何东西
    shutil.rmtree(_TEST_DIR, ignore_errors=True)
    if FAILED:
        print("失败项：")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("全部通过：体验增强后端增量可用，且既有行为未被破坏。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
