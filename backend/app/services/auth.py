"""账号与登录令牌（纯标准库实现，不引入任何第三方加密依赖）。

设计要点：
- **密码**：PBKDF2-HMAC-SHA256 + 每用户随机盐，240000 次迭代，存
  `pbkdf2_sha256$迭代数$salt_hex$hash_hex`；校验用 `hmac.compare_digest` 防时序侧信道。
  故意不存明文、也不做可逆加密——数据库泄露时密码不可还原。
- **令牌**：`secrets.token_urlsafe(32)` 随机串，**数据库只存其 SHA-256**。
  这样 app.db 泄露也无法直接冒充登录，同时退出登录能即时吊销（自包含 JWT 做不到）。
- 本模块只依赖 models/config，不 import deps，避免与 deps 形成循环依赖。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuthToken, User

# ---------------- 口令学参数 ----------------
_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16

# 账号规则：3-32 位，字母/数字/下划线/连字符，必须以字母或数字开头。
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
PASSWORD_MIN_LEN = 6
PASSWORD_MAX_LEN = 128

# 自检脚本建号时统一加的前缀（见 test_auth_flow.py 的 TEST_USERNAME_PREFIX）。
# 用途只有一个：让 `purge_non_registered_users()` 能把「测试造出来的账号」认出来。
# 为什么需要它：测试账号是**完整的注册用户**（有用户名、有密码哈希），
# 从数据上看与真实用户毫无区别，没有前缀就只能靠猜。
TEST_USERNAME_PREFIX = "t_"


def is_real_account(user) -> bool:
    """这个账号是不是**用户注册出来的**真实账号。

    判定与 `purge_non_registered_users()` 完全一致 —— 两者必须同源，否则会出现
    「看板显示 N 个账号，但库里能留下 N-1 个」这种自相矛盾。命中其一即视为非真实：

    1. `username` 或 `password_hash` 为空。注册流程两者必然非空
       （`register()` 先 `validate_username` 再 `hash_password`），缺任一个就一定不是注册产生的。
    2. `username` 以 `TEST_USERNAME_PREFIX`（`t_`）开头 —— 自检脚本的建号约定。

    **它认不出什么**（诚实说明）：与真实用户完全同形的历史遗留测试账号
    （如早期的 `alice*` / `bob*`，或手工验证时随手注册的 `verify_xxx`）
    在数据上无法与真实用户区分，这里也识别不出来。这类只能靠
    ① 别再往真实库建临时账号、② `python backend/prune_users.py --delete <账号名>` 清理。

    后台看板的「注册用户数」用它过滤，因此**看板上的数字 = 启动清理后会留下的账号数**。
    """
    username = (getattr(user, "username", "") or "").strip()
    password_hash = (getattr(user, "password_hash", "") or "").strip()
    if not username or not password_hash:
        return False
    return not username.lower().startswith(TEST_USERNAME_PREFIX)


class AuthError(Exception):
    """认证类错误。路由层据此返回 400/401，避免把内部异常细节透给前端。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ============================================================
# 密码
# ============================================================


def hash_password(password: str) -> str:
    """派生密码哈希。空密码直接拒绝，避免建出「可空密码登录」的账号。"""
    if not password:
        raise AuthError("密码不能为空")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。任何格式异常都返回 False（绝不抛异常，否则脏数据会变成 500）。"""
    if not password or not stored:
        return False
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


# ============================================================
# 账号校验
# ============================================================


def normalize_username(username: str) -> str:
    return (username or "").strip()


def validate_username(username: str) -> str:
    name = normalize_username(username)
    if not name:
        raise AuthError("请输入账号")
    if not USERNAME_RE.match(name):
        raise AuthError("账号需 3-32 位，只能用字母、数字、下划线或连字符，且以字母或数字开头")
    return name


def validate_password(password: str) -> str:
    pwd = password or ""
    if len(pwd) < PASSWORD_MIN_LEN:
        raise AuthError(f"密码至少 {PASSWORD_MIN_LEN} 位")
    if len(pwd) > PASSWORD_MAX_LEN:
        raise AuthError(f"密码最长 {PASSWORD_MAX_LEN} 位")
    return pwd


def find_by_username(db: Session, username: str) -> User | None:
    """按账号查用户。账号大小写不敏感，统一按小写比对与存储。"""
    name = normalize_username(username).lower()
    if not name:
        return None
    return db.execute(select(User).where(User.username == name)).scalars().first()


# ============================================================
# 注册 / 登录
# ============================================================


def register(db: Session, username: str, password: str, nickname: str = "", school: str = "") -> User:
    name = validate_username(username).lower()
    pwd = validate_password(password)
    if find_by_username(db, name) is not None:
        raise AuthError("该账号已被注册，请换一个或直接登录", status_code=409)
    user = User(
        username=name,
        password_hash=hash_password(pwd),
        nickname=(nickname or "").strip()[:64] or name,
        school=(school or "").strip()[:128],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    """登录校验。账号不存在与密码错误返回**同一句提示**，避免枚举出哪些账号已注册。"""
    user = find_by_username(db, username)
    if user is None or not verify_password(password, user.password_hash or ""):
        raise AuthError("账号或密码不正确", status_code=401)
    return user


def purge_non_registered_users(db: Session) -> int:
    """删除「不是用户注册出来的」账号行及其全部数据。

    产品要求：**数据库里只保留用户注册的账号**。命中两类：

    1. `username` 为空、或 `password_hash` 为空。
       早期版本里 `deps.ensure_user(db, "")` 会为**未登录**请求即席创建一个
       `username=""` 的公共 guest 行（`user_id="guest"`），所有游客数据都挂在它下面。
       现在游客走进程内内存库（见 `db.py`），这一行已不再被创建，但老库里可能还留着。
       注册流程两者必然非空（`register()` 先 `validate_username` 再 `hash_password`），
       所以缺任一个就一定不是注册产生的。

    2. `username` 以 `TEST_USERNAME_PREFIX`（`t_`）开头。
       自检脚本建出来的账号**是完整的注册用户**（有用户名、有密码哈希），
       从数据上看与真实用户毫无区别 —— 曾经因此把 125 个 `alice*`/`bob*` 测试账号
       留在了真实库里。自那以后测试统一用 `t_` 前缀建号，这里按前缀回收。

    按 `user_id` 逐表删除，避免留下孤儿数据。在**后端启动时**调用，
    因此即使库被别的版本重新写脏，重启即自愈。

    注意：`is_real_account()` 的规则只对**未来**的测试账号有效。与真实用户完全同形的
    历史遗留账号（早期的 `alice*`/`bob*`，或手工验证真实库时随手注册的 `verify_xxx`）
    在数据上无法与真实用户区分，这里认不出来 —— 只能靠 `python backend/prune_users.py
    --delete <账号名>` 明确点名清理。真实库出现「我没注册过的账号」时就是这种情况。
    """
    try:
        rows = db.execute(select(User)).scalars().all()
        # 判定复用 is_real_account()：与后台看板的过滤同源，
        # 保证「看板显示的账号数」与「清理后会留下的账号数」永远一致。
        stale = [u for u in rows if not is_real_account(u)]
        if not stale:
            return 0

        # 关联表：全部带 user_id（见 models.py「新增个人资源表必须带 user_id」）。
        # 这张清单要与 models.py 里所有带 user_id 的表对齐 —— 少一张就会留下孤儿行
        # （投递进度就曾漏在外面）。新增带 user_id 的表时**必须**补进来。
        from ..models import (  # 局部导入：避免模块级循环引用
            InterviewSession,
            JobDescription,
            JobProgress,
            ReportFeedback,
            Resume,
            SpeechMetric,
            WrongQuestion,
        )

        tables = (
            AuthToken,
            Resume,
            JobDescription,
            InterviewSession,
            WrongQuestion,
            SpeechMetric,
            ReportFeedback,
            JobProgress,
        )
        removed = 0
        for user in stale:
            uid = user.user_id or ""
            for model in tables:
                for row in db.execute(select(model).where(model.user_id == uid)).scalars().all():
                    db.delete(row)
            db.delete(user)
            removed += 1
            print(f"[auth] 清理非注册账号：{uid}（username={user.username or '空'}）")
        db.commit()
        return removed
    except Exception as exc:  # noqa: BLE001 清理失败绝不能阻断启动
        print(f"[auth] 非注册账号清理跳过: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


# ============================================================
# 令牌
# ============================================================


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(dt: datetime | None) -> datetime | None:
    """库里是 naive UTC，比较前必须补上时区，否则东八区会提前 8 小时判定过期。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def issue_token(db: Session, user: User) -> tuple[str, datetime]:
    """签发登录令牌。返回 (明文令牌, 到期时间)——明文只在这一刻存在，之后库里只有哈希。"""
    raw = secrets.token_urlsafe(32)
    ttl_hours = max(0.0, float(settings.auth_token_ttl_hours or 0.0))
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    row = AuthToken(
        token_hash=_hash_token(raw),
        user_id=user.user_id,
        created_at=datetime.now(timezone.utc),
        expires_at=expires.replace(tzinfo=None),  # 与其它表一致，落库为 naive UTC
    )
    db.add(row)
    db.commit()
    return raw, expires


def resolve_token(db: Session, token: str) -> User | None:
    """令牌 → 用户。无效/过期/已吊销一律返回 None。"""
    if not token:
        return None
    try:
        row = db.get(AuthToken, _hash_token(token))
    except Exception as exc:  # noqa: BLE001 令牌表异常不应变成 500，按未登录处理
        print(f"[auth] 令牌查询失败: {exc}")
        return None
    if row is None:
        return None
    expires = _as_utc(row.expires_at)
    if expires is not None and expires <= datetime.now(timezone.utc):
        # 过期即顺手清理，避免 auth_token 表只增不减
        try:
            db.delete(row)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return None
    user = db.get(User, row.user_id)
    return user if user is not None else None


def revoke_token(db: Session, token: str) -> None:
    """退出登录：删除该令牌行，立即失效（其它设备的令牌不受影响）。"""
    if not token:
        return
    try:
        row = db.get(AuthToken, _hash_token(token))
        if row is not None:
            db.delete(row)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] 令牌吊销失败: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def purge_expired_tokens(db: Session) -> int:
    """清理全部过期令牌，返回删除条数（启动自愈用）。"""
    try:
        rows = db.execute(select(AuthToken)).scalars().all()
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] 过期令牌清理跳过: {exc}")
        return 0
    now = datetime.now(timezone.utc)
    stale = [r for r in rows if (_as_utc(r.expires_at) or now) <= now]
    if not stale:
        return 0
    try:
        for row in stale:
            db.delete(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] 过期令牌清理失败: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    return len(stale)
