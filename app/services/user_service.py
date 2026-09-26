import base64
import datetime as dt
import hashlib
import hmac
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
from sqlalchemy import func, or_

from app.config import settings
from app.db.models import UserDB
from app.db.session import engine, get_db_session
from app.models.schemas import User

DEFAULT_AVATARS = [
    "https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=200&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?q=80&w=200&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1517841905240-472988babdf9?q=80&w=200&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1539571696357-5a69c17a67c6?q=80&w=200&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1544005313-94ddf0286df2?q=80&w=200&auto=format&fit=crop",
]


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def make_auth_token(user_id: str, role: str, expires_in_sec: int = 86400 * 7) -> str:
    """Tạo signed token xác thực kèm thời gian hết hạn (mặc định 7 ngày)."""
    exp = int(time.time()) + expires_in_sec
    data = f"{user_id}:{role}:{exp}"
    sig = hmac.new(settings.SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
    raw = f"{data}:{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_auth_token(token: str) -> Optional[Tuple[str, str]]:
    """Giải mã và kiểm tra chữ ký token. Trả về (user_id, role) nếu hợp lệ."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        parts = raw.split(":")
        if len(parts) != 4:
            return None
        user_id, role, exp_str, sig = parts
        if int(exp_str) < time.time():
            return None
        data = f"{user_id}:{role}:{exp_str}"
        expected_sig = hmac.new(settings.SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected_sig):
            return user_id, role
    except Exception:
        return None
    return None


def to_user_model(u: Any) -> User:
    """Chuyển đổi UserDB (SQLAlchemy ORM) hoặc dict sang Pydantic User model."""
    if isinstance(u, dict):
        status = u.get("status", "active")
        return User(
            id=u["id"],
            name=u.get("name") or u.get("full_name") or "",
            full_name=u.get("full_name") or u.get("name") or "",
            username=u["username"],
            email=u["email"],
            phone=u.get("phone"),
            address=u.get("address"),
            role=u.get("role", "user"),
            status=status,
            is_active=(status != "disabled"),
            avatar=u.get("avatar"),
            points_balance=u.get("points_balance", 0),
            total_spent=u.get("total_spent", 0),
            tier=u.get("tier", "Silver"),
            created_at=u.get("created_at", ""),
        )

    status = getattr(u, "status", "active") or "active"
    return User(
        id=u.id,
        name=u.name or u.full_name or "",
        full_name=u.full_name or u.name or "",
        username=u.username,
        email=u.email,
        phone=u.phone,
        address=u.address,
        role=u.role or "user",
        status=status,
        is_active=(status != "disabled"),
        avatar=u.avatar,
        points_balance=u.points_balance if u.points_balance is not None else 0,
        total_spent=u.total_spent if u.total_spent is not None else 0,
        tier=u.tier or "Silver",
        created_at=u.created_at or "",
    )


class UserService:
    """Quản lý người dùng tập trung bằng SQLAlchemy ORM, hỗ trợ giao dịch ACID chống Race Condition."""

    def __init__(self):
        # Đảm bảo migration tự động nếu bảng users trống
        self._ensure_initialized()

    def _ensure_initialized(self):
        try:
            with get_db_session() as session:
                count = session.query(func.count(UserDB.id)).scalar()
                if count == 0:
                    from scripts.migrate_to_db import run_migration
                    run_migration()
        except Exception as e:
            # Sẽ thử lại khi query đầu tiên được gọi
            pass

    # ---------- Token Session ----------
    def create_session_token(self, user: User) -> str:
        return make_auth_token(user.id, user.role)

    def verify_session_token(self, token: str) -> Optional[User]:
        res = verify_auth_token(token)
        if not res:
            return None
        user_id, _ = res
        return self.get_by_id(user_id)

    # ---------- Tìm kiếm & Xác thực ----------
    def get_by_id(self, user_id: str) -> Optional[User]:
        with get_db_session() as session:
            u = session.query(UserDB).filter(UserDB.id == user_id).first()
            return to_user_model(u) if u else None

    def get_by_username(self, username: str) -> Optional[User]:
        with get_db_session() as session:
            clean = username.strip().lower()
            u = session.query(UserDB).filter(func.lower(UserDB.username) == clean).first()
            return to_user_model(u) if u else None

    def get_by_email(self, email: str) -> Optional[User]:
        with get_db_session() as session:
            clean = email.strip().lower()
            u = session.query(UserDB).filter(func.lower(UserDB.email) == clean).first()
            return to_user_model(u) if u else None

    def authenticate(self, username_or_email: str, plain_password: str) -> Optional[User]:
        key = username_or_email.strip().lower()
        with get_db_session() as session:
            u = session.query(UserDB).filter(
                or_(func.lower(UserDB.username) == key, func.lower(UserDB.email) == key)
            ).first()
            if not u:
                return None
            if u.status == "disabled":
                raise ValueError("Tài khoản của bạn đã bị khóa. Vui lòng liên hệ Admin.")
            if not verify_password(plain_password, u.password_hash):
                return None
            return to_user_model(u)

    def register_user(self, name: str, email: str, username: str, password: str, phone: Optional[str] = None) -> User:
        u_clean = username.strip().lower()
        e_clean = email.strip().lower()

        with get_db_session() as session:
            # 1. Kiểm tra username đã tồn tại chưa
            if session.query(UserDB).filter(func.lower(UserDB.username) == u_clean).first():
                raise ValueError("Tên đăng nhập đã được sử dụng")

            # 2. Kiểm tra email đã tồn tại chưa
            if session.query(UserDB).filter(func.lower(UserDB.email) == e_clean).first():
                raise ValueError("Email này đã được đăng ký tài khoản")

            count = session.query(func.count(UserDB.id)).scalar() or 0
            user_id = f"usr_{count + 1:03d}"
            while session.query(UserDB).filter(UserDB.id == user_id).first():
                count += 1
                user_id = f"usr_{count + 1:03d}"

            avatar = DEFAULT_AVATARS[count % len(DEFAULT_AVATARS)]
            now_str = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

            new_user = UserDB(
                id=user_id,
                name=name.strip(),
                full_name=name.strip(),
                username=username.strip(),
                email=e_clean,
                phone=phone.strip() if phone else None,
                address=None,
                password_hash=hash_password(password),
                role="user",
                status="active",
                avatar=avatar,
                points_balance=0,
                total_spent=0,
                tier="Silver",
                created_at=now_str,
            )
            session.add(new_user)
            session.flush()
            session.refresh(new_user)
            return to_user_model(new_user)

    def register(self, req) -> User:
        full_name = getattr(req, "full_name", None) or getattr(req, "name", None) or req.username
        phone = getattr(req, "phone", None)
        return self.register_user(
            name=full_name,
            email=req.email,
            username=req.username,
            password=req.password,
            phone=phone,
        )

    def update_profile(
        self,
        user_id: str,
        name: Optional[str] = None,
        full_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        address: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> Optional[User]:
        with get_db_session() as session:
            u = session.query(UserDB).filter(UserDB.id == user_id).first()
            if not u:
                return None

            disp_name = full_name or name
            if disp_name:
                u.name = disp_name.strip()
                u.full_name = disp_name.strip()

            if email:
                e_clean = email.strip().lower()
                if e_clean != u.email.lower():
                    exist = session.query(UserDB).filter(
                        func.lower(UserDB.email) == e_clean,
                        UserDB.id != user_id
                    ).first()
                    if exist:
                        raise ValueError("Email này đã được tài khoản khác sử dụng")
                    u.email = e_clean

            if phone is not None:
                u.phone = phone.strip() if phone else None

            if address is not None:
                u.address = address.strip() if address else None

            if avatar:
                u.avatar = avatar

            session.flush()
            session.refresh(u)
            return to_user_model(u)

    def change_password(self, user_id: str, old_password: str, new_password: str) -> Tuple[bool, str]:
        with get_db_session() as session:
            u = session.query(UserDB).filter(UserDB.id == user_id).first()
            if not u:
                return False, "Không tìm thấy người dùng"
            if not verify_password(old_password, u.password_hash):
                return False, "Mật khẩu hiện tại không chính xác"
            u.password_hash = hash_password(new_password)
            return True, "Đổi mật khẩu thành công"

    # ---------- Admin Quản lý User ----------
    def get_all_users(self) -> List[User]:
        with get_db_session() as session:
            users = session.query(UserDB).order_by(UserDB.id.asc()).all()
            return [to_user_model(u) for u in users]

    def update_role(self, user_id: str, new_role: str) -> User:
        with get_db_session() as session:
            u = session.query(UserDB).filter(UserDB.id == user_id).first()
            if not u:
                raise ValueError("Không tìm thấy người dùng")
            if u.username.lower() == "admin":
                raise ValueError("Không thể thay đổi quyền của tài khoản Admin mặc định")
            if new_role not in ("admin", "user"):
                raise ValueError("Vai trò không hợp lệ (admin hoặc user)")
            u.role = new_role
            session.flush()
            session.refresh(u)
            return to_user_model(u)

    def toggle_status(self, user_id: str, is_active: Optional[bool] = None) -> User:
        with get_db_session() as session:
            u = session.query(UserDB).filter(UserDB.id == user_id).first()
            if not u:
                raise ValueError("Không tìm thấy người dùng")
            if u.username.lower() == "admin":
                raise ValueError("Không thể khóa tài khoản Admin mặc định")
            if is_active is not None:
                u.status = "active" if is_active else "disabled"
            else:
                u.status = "disabled" if u.status == "active" else "active"
            session.flush()
            session.refresh(u)
            return to_user_model(u)


user_service = UserService()
