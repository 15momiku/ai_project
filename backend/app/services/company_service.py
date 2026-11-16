"""公司画像服务（对应文档 §3.4.3，API_CONTRACT §1.1 固定签名）。

职责：
1. 内置 50 家校招热门公司库（`app/data/companies.json`）的加载与缓存；
2. 公司名匹配（精确 -> 去后缀 -> 包含度 -> 编辑距离模糊），
   并严格区分「中国建设银行」与「中国银行」这类子串陷阱；
3. `resolve_company_profile`：DB 缓存 -> 内置库 -> LLM 生成（LLM 不可用则规则生成），
   最终写回 DB，保证前端永远拿得到一份可用画像。
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Company
from ..prompts.templates import company_prompt
from ..schemas import CompanyBrief, CompanyProfile
from .llm import get_llm

# 公司库文件位置：app/data/companies.json
_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "companies.json"

# 需要剥离的公司名后缀（含「中国」这类常见前缀式修饰）
_SUFFIXES = [
    "有限责任公司", "股份有限公司", "有限公司", "公司", "集团", "控股", "股份",
    "科技", "技术", "网络", "信息技术", "国际", "中国", "（中国）", "(中国)",
]

# 常见口语别名 -> 内置库标准名
_NAME_ALIASES: dict[str, str] = {
    "字节": "字节跳动",
    "头条": "字节跳动",
    "b站": "哔哩哔哩",
    "bilibili": "哔哩哔哩",
    "哔哩": "哔哩哔哩",
    "阿里": "阿里巴巴",
    "腾讯科技": "腾讯",
    "去哪儿": "携程集团",
    "建行": "中国建设银行",
    "工行": "中国工商银行",
    "农行": "中国农业银行",
    "招行": "招商银行",
    "移动": "中国移动",
    "电信": "中国电信",
    "石化": "中国石化",
    "中石油": "中国石化",
}


# ============================================================
# 加载与缓存
# ============================================================


@lru_cache(maxsize=1)
def load_companies() -> list[dict]:
    """读取内置公司库（进程内缓存；API_CONTRACT §1.1）。"""
    try:
        raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 文件缺失/损坏时不阻断启动
        print(f"[company] 公司库读取失败，返回空列表：{exc}")
        return []
    items = raw.get("companies") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [c for c in items if isinstance(c, dict) and c.get("name")]


def company_id_for(name: str) -> str:
    """由公司名生成稳定 id（内置库没有 id 字段）。"""
    return hashlib.md5(("company:" + (name or "")).encode("utf-8")).hexdigest()[:12]


# ============================================================
# 名称归一化与模糊匹配
# ============================================================


def _normalize_name(name: str) -> str:
    """归一化：去空白/标点、ASCII 转小写。"""
    s = (name or "").strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = s.replace("（", "(").replace("）", ")")
    return s


def _strip_suffix(name: str) -> str:
    """循环剥离公司名修饰后缀，得到「核心名」。"""
    s = name
    changed = True
    while changed and len(s) > 2:
        changed = False
        for suf in _SUFFIXES:
            if len(s) > len(suf) and s.endswith(suf):
                s = s[: -len(suf)]
                changed = True
                break
            if len(s) > len(suf) + 1 and s.startswith(suf):
                s = s[len(suf):]
                changed = True
                break
    return s


def _levenshtein(a: str, b: str) -> int:
    """编辑距离（DP），公司名很短，开销可忽略。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_ratio(a: str, b: str) -> float:
    """归一化相似度：1 - 编辑距离 / 较长串长度。"""
    if not a or not b:
        return 0.0
    return 1.0 - _levenshtein(a, b) / max(len(a), len(b))


# 阈值刻意收紧：宁可判不出，也不要把「中国银行」误判成「中国建设银行」
_CONTAIN_RATIO = 0.6  # 包含匹配：短串/长串长度比下限
_FUZZY_RATIO = 0.75  # 编辑距离相似度下限
_FUZZY_LEN_DIFF = 2  # 编辑距离模糊匹配允许的最大长度差


def find_company(name: str) -> dict | None:
    """在内置公司库中匹配公司（API_CONTRACT §1.1）。匹配不到返回 None。

    匹配顺序：精确 -> 别名 -> 去后缀精确 -> 包含度 -> 编辑距离模糊。
    """
    query = _normalize_name(name)
    if not query:
        return None
    companies = load_companies()
    if not companies:
        return None

    # 1) 精确匹配
    for c in companies:
        if _normalize_name(c["name"]) == query:
            return c

    # 2) 别名
    alias = _NAME_ALIASES.get(query)
    if alias:
        for c in companies:
            if _normalize_name(c["name"]) == _normalize_name(alias):
                return c

    # 3) 去后缀精确匹配
    q_base = _strip_suffix(query)
    for c in companies:
        if _strip_suffix(_normalize_name(c["name"])) == q_base:
            return c

    # 4) 包含度匹配（长度比需达标，避免「中国」命中所有「中国xx」）
    best: dict | None = None
    best_score = 0.0
    if len(q_base) >= 2:
        for c in companies:
            c_base = _strip_suffix(_normalize_name(c["name"]))
            if not c_base:
                continue
            shorter = min(len(q_base), len(c_base))
            longer = max(len(q_base), len(c_base))
            if (q_base in c_base or c_base in q_base) and shorter / longer >= _CONTAIN_RATIO:
                if shorter / longer > best_score:
                    best, best_score = c, shorter / longer
        if best is not None:
            return best

    # 5) 编辑距离模糊匹配
    for c in companies:
        c_base = _strip_suffix(_normalize_name(c["name"]))
        if not c_base or abs(len(c_base) - len(q_base)) > _FUZZY_LEN_DIFF:
            continue
        score = _fuzzy_ratio(q_base, c_base)
        if score >= _FUZZY_RATIO and score > best_score:
            best, best_score = c, score
    return best


def search_companies(q: str, limit: int = 20) -> list[dict]:
    """按关键词搜索内置公司库（API_CONTRACT §2.2 `GET /api/company/search`）。

    q 为空 -> 返回前 limit 家；命中名称优先，其次命中行业/业务描述。
    """
    companies = load_companies()
    if limit <= 0:
        return []
    query = _normalize_name(q)
    if not query:
        return companies[:limit]

    name_hits: list[dict] = []
    desc_hits: list[dict] = []
    for c in companies:
        cname = _normalize_name(c.get("name", ""))
        if query in cname or cname in query:
            name_hits.append(c)
            continue
        blob = _normalize_name(
            f"{c.get('industry', '')}{c.get('business', '')}{''.join(c.get('culture_tags', []) or [])}"
        )
        if query in blob:
            desc_hits.append(c)
    return (name_hits + desc_hits)[:limit]


def hot_companies(limit: int = 50) -> list[CompanyBrief]:
    """热门公司列表（API_CONTRACT §1.1，供 `/api/meta/options` 使用）。"""
    result: list[CompanyBrief] = []
    for c in load_companies()[: max(0, limit)]:
        result.append(
            CompanyBrief(
                company_id=company_id_for(c.get("name", "")),
                name=c.get("name", ""),
                industry=c.get("industry", ""),
                scale=c.get("scale", ""),
                interview_style=c.get("interview_style", ""),
            )
        )
    return result


# ============================================================
# 画像转换与持久化
# ============================================================


def _to_profile(raw: dict) -> dict:
    """把 companies.json 的原始 dict 转成 CompanyProfile 字段（文档 §3.4.3）。"""
    raw = raw or {}
    data = {
        "name": raw.get("name", ""),
        "industry": raw.get("industry", ""),
        "scale": raw.get("scale", ""),
        "business": raw.get("business", ""),
        "culture_tags": list(raw.get("culture_tags") or []),
        "interview_style": raw.get("interview_style", "") or "结构化面",
        "style_desc": raw.get("style_desc", ""),
        "common_questions": list(raw.get("common_questions") or []),
        "headquarters": raw.get("headquarters", ""),
        "note": raw.get("note") or "信息仅供参考",
    }
    return CompanyProfile(**data).model_dump()


def _rule_company(name: str) -> dict:
    """LLM 不可用时的公司规则兜底（文档 §4.7 降级总原则）。"""
    data = {
        "name": name or "未知公司",
        "industry": "待确认",
        "scale": "",
        "business": "",
        "culture_tags": [],
        "interview_style": "结构化面",
        "style_desc": "暂未收录该公司的面试风格，按通用结构化面试节奏进行。",
        "common_questions": [],
        "headquarters": "",
        "note": "该公司画像为系统自动生成（未收录、且当前 LLM 不可用），信息待确认。",
    }
    return CompanyProfile(**data).model_dump()


def _save_company(db: Session, profile: dict, source: str) -> None:
    """把画像写回 DB（按公司名 upsert）；失败不抛异常。"""
    name = profile.get("name") or ""
    if not name:
        return
    try:
        row = db.query(Company).filter(Company.name == name).first()
        if row:
            row.profile_json = json.dumps(profile, ensure_ascii=False)
            row.source = source
            db.commit()
            db.refresh(row)
            return
        row = Company(
            name=name,
            profile_json=json.dumps(profile, ensure_ascii=False),
            source=source,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    except Exception as exc:  # noqa: BLE001 落库失败不影响本次返回
        print(f"[company] 公司画像写库失败（已忽略）：{exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


async def resolve_company_profile(db: Session, name: str) -> tuple[dict, str]:
    """解析公司画像（API_CONTRACT §1.1）。

    优先级：DB 缓存 -> 内置库 -> LLM 生成（失败则规则兜底）；最后写回 DB。
    返回 (CompanyProfile dict, "auto" | "manual")。
    """
    clean_name = (name or "").strip()

    # 1) DB 缓存（精确匹配名称）
    if clean_name:
        try:
            row = db.query(Company).filter(Company.name == clean_name).first()
            if row and row.profile_json:
                cached = json.loads(row.profile_json or "{}")
                if isinstance(cached, dict) and cached.get("name"):
                    cached.setdefault("note", "信息仅供参考")
                    if not cached.get("interview_style"):
                        cached["interview_style"] = "结构化面"
                    return CompanyProfile(**cached).model_dump(), (row.source or "auto")
        except Exception as exc:  # noqa: BLE001 缓存读失败继续往下走
            print(f"[company] 公司画像读库失败（继续匹配）：{exc}")

    # 2) 内置库
    raw = find_company(clean_name)
    if raw:
        profile = _to_profile(raw)
        _save_company(db, profile, "auto")
        return profile, "auto"

    # 3) LLM 生成
    display_name = clean_name or "未知公司"
    profile_dict: dict | None = None
    try:
        data = await get_llm().chat_json(company_prompt(display_name))
        if isinstance(data, dict) and data:
            data.setdefault("name", display_name)
            profile_dict = CompanyProfile(**data).model_dump()
    except Exception as exc:  # noqa: BLE001 LLM 失败走规则画像
        print(f"[company] 公司画像 LLM 生成失败，改用规则兜底：{exc}")

    # 4) 规则兜底
    if profile_dict is None:
        profile_dict = _rule_company(display_name)

    _save_company(db, profile_dict, "auto")
    return profile_dict, "auto"
