"""查看数据库里的用户数与账号明细。

为什么做成脚本而不是一行命令：
  · PowerShell 里 `<>` 是重定向运算符，写 `where username <> ''` 会在传给 Python
    之前就被 shell 解释掉（报「重定向运算符后面缺少文件规范」）；
  · `data/app.db` 是相对路径，从项目根目录执行会报「unable to open database file」。
放在脚本里就都不存在了：SQL 不与 shell 打交道，路径按脚本位置推导。

运行（任意目录都可以）：
    python backend/show_users.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# 按脚本位置推导，与当前工作目录无关
DB = Path(__file__).resolve().parent / "data" / "app.db"


def main() -> int:
    if not DB.exists():
        print(f"找不到数据库：{DB}")
        print("（首次运行请先启动一次后端，它会自动建库）")
        return 1

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # 只看注册账号：早期版本会为未登录请求建 username 为空的公共 guest 行，
    # 那不是真实用户（现在启动清理已经会回收它）
    total = con.execute("select count(*) from user where username <> ''").fetchone()[0]
    today = con.execute(
        "select count(*) from user where username <> '' and date(created_at) = date('now')"
    ).fetchone()[0]
    week = con.execute(
        "select count(*) from user where username <> '' and created_at >= datetime('now','-7 day')"
    ).fetchone()[0]
    active = con.execute(
        "select count(distinct user_id) from auth_token where expires_at > datetime('now')"
    ).fetchone()[0]

    print("=" * 62)
    print(f"  数据库：{DB}")
    print(f"  大小  ：{DB.stat().st_size / 1024:.0f} KB")
    print("=" * 62)
    print(f"  注册用户数        : {total}")
    print(f"  今日新增          : {today}")
    print(f"  近 7 天新增       : {week}")
    print(f"  持有效登录令牌的人 : {active}")
    print()

    rows = con.execute(
        """
        select u.username, u.nickname, u.school, u.created_at,
               (select count(*) from interview_session s where s.user_id = u.user_id) as sessions,
               (select count(*) from resume r where r.user_id = u.user_id) as resumes
        from user u
        where u.username <> ''
        order by u.created_at
        """
    ).fetchall()

    if not rows:
        print("  还没有注册用户。")
    else:
        print(f"  {'账号':<16}{'昵称':<14}{'学校':<14}{'简历':>4}{'场次':>5}  注册时间")
        print("  " + "-" * 74)
        for r in rows:
            print(
                f"  {r['username']:<16}{(r['nickname'] or '-'):<14}{(r['school'] or '-'):<14}"
                f"{r['resumes']:>4}{r['sessions']:>5}  {str(r['created_at'])[:19]}"
            )

    con.close()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
