"""账号维护：列出全部账号 / 按账号名删除错误创建的测试账号（含级联清理）。

为什么需要它
------------
库里曾出现「我没注册过的账号」。根因是**验证过程直接对真实库注册了临时账号**：
项目自检脚本（`test_*_flow.py`）都会在 import app 之前把 `DATABASE_URL` 指向临时库，
所以它们不会弄脏真实库；但手工验证（curl / Invoke-RestMethod 打真实端口）没有这层保护，
建出来的 `verify_xxx` / `final_xxx` 会永久留在库里，并且看起来与真实用户完全一样
（有用户名、有密码哈希，从数据上无法区分）。

这类账号会写进所有带 user_id 的表（简历 / JD / 面试会话 / 报告反馈 / 投递进度…），
因此删除时必须**连带清理所有带 user_id 的表**，否则会留下查不到主人的孤儿行。

用法（任意目录都可以）
----------------------
    python backend/prune_users.py                          # 只列出，不改动
    python backend/prune_users.py --delete verify_1,final_2 # 删除指定账号（会先打印再删）
    python backend/prune_users.py --delete verify_1 --dry-run

安全约定
--------
· **必须显式给出账号名**才会删；没有「按前缀批量删」这种一步到位的入口，
  避免一次手滑清掉真实用户。
· 删除前打印每个账号的关联数据条数，并在删除后复核为 0。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "app.db"

# 所有带 user_id 的表（与 models.py 一一对应）。
# 加新表时**必须**补进这里，否则删账号会留下孤儿行。
USER_TABLES: tuple[str, ...] = (
    "auth_token",
    "resume",
    "job_description",
    "interview_session",
    "wrong_question",
    "speech_metric",
    "report_feedback",
    "job_progress",
)


def _tables_present(con: sqlite3.Connection) -> list[str]:
    have = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    return [t for t in USER_TABLES if t in have]


def list_accounts(con: sqlite3.Connection) -> list[sqlite3.Row]:
    tables = _tables_present(con)
    # 每个账号的关联数据条数（一眼看出哪些是空账号）
    subs = ", ".join(
        f"(select count(*) from {t} x where x.user_id = u.user_id) as n_{t}" for t in tables
    )
    return con.execute(
        f"select u.user_id, u.username, u.nickname, u.created_at, u.password_hash, {subs} "
        "from user u order by u.created_at"
    ).fetchall()


def print_accounts(con: sqlite3.Connection) -> None:
    rows = list_accounts(con)
    tables = _tables_present(con)
    print("=" * 78)
    print(f"  数据库：{DB}")
    print(f"  账号总数：{len(rows)}")
    print("=" * 78)
    for r in rows:
        counts = {t: r[f"n_{t}"] for t in tables}
        nonzero = ", ".join(f"{t}={n}" for t, n in counts.items() if n)
        registered = "是" if (r["username"] or "").strip() and (r["password_hash"] or "").strip() else "否"
        print(f"\n  {r['username'] or '（空账号名）'}")
        print(f"    user_id   : {r['user_id']}")
        print(f"    注册时间  : {str(r['created_at'])[:19]}   完整注册: {registered}")
        print(f"    关联数据  : {nonzero or '无'}")
    print()


def delete_accounts(con: sqlite3.Connection, names: list[str], *, dry_run: bool) -> int:
    tables = _tables_present(con)
    lowered = [n.strip().lower() for n in names if n.strip()]
    found = con.execute(
        "select user_id, username from user where lower(username) in (%s)"
        % ",".join("?" * len(lowered)),
        lowered,
    ).fetchall()

    if not found:
        print("没有匹配到任何账号，未做任何改动。")
        return 1

    missing = set(lowered) - {str(r["username"]).lower() for r in found}
    if missing:
        print(f"⚠ 以下账号不存在，会被忽略：{', '.join(sorted(missing))}")

    print("将要删除以下账号及其全部关联数据：")
    total_rows = 0
    plan: list[tuple[str, str, list[str]]] = []
    for r in found:
        uid, name = r["user_id"], r["username"]
        stmts = list(tables)
        counts = {}
        for t in stmts:
            counts[t] = con.execute(
                f"select count(*) from {t} where user_id = ?", (uid,)
            ).fetchone()[0]
        total_rows += sum(counts.values()) + 1
        print(f"  · {name}  ({uid[:12]}…)")
        for t, n in counts.items():
            if n:
                print(f"      {t}: {n} 行")
        plan.append((uid, name, stmts))

    print(f"\n  合计将删除约 {total_rows} 行。")

    if dry_run:
        print("\n  --dry-run：未做任何改动。")
        return 0

    for uid, name, stmts in plan:
        for t in stmts:
            con.execute(f"delete from {t} where user_id = ?", (uid,))
        con.execute("delete from user where user_id = ?", (uid,))
        print(f"  已删除：{name}")

        # 复核：不留孤儿
        left = {
            t: con.execute(f"select count(*) from {t} where user_id = ?", (uid,)).fetchone()[0]
            for t in stmts
        }
        orphan = {t: n for t, n in left.items() if n}
        if orphan:
            print(f"    ⚠ 仍有残留：{orphan}")
    con.commit()

    remaining = con.execute("select count(*) from user").fetchone()[0]
    print(f"\n完成。当前账号总数：{remaining}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="账号维护：列出 / 删除测试账号")
    ap.add_argument("--delete", default="", help="要删除的账号名，逗号分隔")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要删除的内容")
    args = ap.parse_args()

    if not DB.exists():
        print(f"找不到数据库：{DB}")
        print("（首次运行请先启动一次后端，它会自动建库）")
        return 1

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        if args.delete.strip():
            return delete_accounts(
                con,
                args.delete.split(","),
                dry_run=args.dry_run,
            )
        print_accounts(con)
        print("提示：删除账号用 --delete <账号名>[,<账号名>]（可加 --dry-run 先看一遍）。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
