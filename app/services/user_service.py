import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import bcrypt
from app.config import settings
from app.models.schemas import User

USERS_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "users.json"
)

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


class UserService:
    def __init__(self, data_path: str = USERS_DATA_PATH):
        self.data_path = data_path
        self._lock = threading.RLock()
        self._users: List[dict] = []
        self._by_id: Dict[str, dict] = {}
        self._by_username: Dict[str, dict] = {}
        self._by_email: Dict[str, dict] = {}
        self._init_storage()

    def _init_storage(self):
        with self._lock:
            if not os.path.exists(self.data_path):
                self._create_default_users()
            else:
                try:
                    with open(self.data_path, "r", encoding="utf-8") as f:
                        self._users = json.load(f)
                except Exception:
                    self._create_default_users()
            self._rebuild_indices()

    def _create_default_users(self):
        """Khởi tạo tài khoản demo mặc định (admin & user)."""
        now = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
        default_users = [
            {
                "id": "usr_001",
                "name": "Quản trị viên AURA",
                "username": "admin",
                "email": "admin@aurastudio.vn",
                "password_hash": hash_password("admin123"),
                "role": "admin",
                "status": "active",
                "avatar": DEFAULT_AVATARS[0],
                "created_at": now,
            },
            {
                "id": "usr_002",
                "name": "Tiến Anh",
                "username": "user",
                "email": "tienanh@aurastudio.vn",
                "password_hash": hash_password("user123"),
                "role": "user",
                "status": "active",
                "avatar": DEFAULT_AVATARS[1],
                "created_at": now,
            },
            {
                "id": "usr_003",
                "name": "Minh Châu",
                "username": "minhchau",
                "email": "minhchau@gmail.com",
                "password_hash": hash_password("user123"),
                "role": "user",
                "status": "active",
                "avatar": DEFAULT_AVATARS[2],
                "created_at": now,
            },
            {
                "id": "usr_004",
                "name": "Hoàng Long",
                "username": "hoanglong",
                "email": "hoanglong@gmail.com",
                "password_hash": hash_password("user123"),
                "role": "user",
                "status": "active",
                "avatar": DEFAULT_AVATARS[3],
                "created_at": now,
            },
            {
                "id": "usr_005",
                "name": "Thùy Linh",
                "username": "thuylinh",
                "email": "thuylinh@gmail.com",
                "password_hash": hash_password("user123"),
                "role": "user",
                "status": "active",
                "avatar": DEFAULT_AVATARS[4],
                "created_at": now,
            },
        ]
        self._users = default_users
        self._save_to_disk()

    def _rebuild_indices(self):
        self._by_id = {u["id"]: u for u in self._users}
        self._by_username = {u["username"].lower(): u for u in self._users}
        self._by_email = {u["email"].lower(): u for u in self._users}

    def _save_to_disk(self):
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(self._users, f, ensure_ascii=False, indent=2)

    def to_user_model(self, u_dict: dict) -> User:
        points = u_dict.get("points_balance", 0)
        spent = u_dict.get("total_spent", 0)
        tier = u_dict.get("tier", "Silver")
        try:
            from app.db.database import get_db_connection
            conn = get_db_connection()
            try:
                row = conn.execute("SELECT points_balance, total_spent, tier FROM users WHERE id = ?;", (u_dict["id"],)).fetchone()
                if row:
                    points = row["points_balance"] or points
                    spent = row["total_spent"] or spent
                    tier = row["tier"] or tier
            finally:
                conn.close()
        except Exception:
            pass

        return User(
            id=u_dict["id"],
            name=u_dict.get("name", u_dict.get("full_name", "")),
            full_name=u_dict.get("full_name", u_dict.get("name", "")),
            username=u_dict["username"],
            email=u_dict["email"],
            phone=u_dict.get("phone"),
            address=u_dict.get("address"),
            role=u_dict.get("role", "user"),
            status=u_dict.get("status", "active"),
            is_active=(u_dict.get("status") != "disabled"),
            avatar=u_dict.get("avatar"),
            points_balance=points,
            total_spent=spent,
            tier=tier,
            created_at=u_dict.get("created_at", ""),
        )

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
        u = self._by_id.get(user_id)
        return self.to_user_model(u) if u else None

    def get_by_username(self, username: str) -> Optional[User]:
        u = self._by_username.get(username.strip().lower())
        return self.to_user_model(u) if u else None

    def get_by_email(self, email: str) -> Optional[User]:
        u = self._by_email.get(email.strip().lower())
        return self.to_user_model(u) if u else None

    def authenticate(self, username_or_email: str, plain_password: str) -> Optional[User]:
        key = username_or_email.strip().lower()
        u = self._by_username.get(key) or self._by_email.get(key)
        if not u:
            return None
        if u.get("status") == "disabled":
            raise ValueError("Tài khoản của bạn đã bị khóa. Vui lòng liên hệ Admin.")
        if not verify_password(plain_password, u["password_hash"]):
            return None
        return self.to_user_model(u)

    def register_user(self, name: str, email: str, username: str, password: str, phone: Optional[str] = None) -> User:
        with self._lock:
            u_clean = username.strip().lower()
            e_clean = email.strip().lower()

            if u_clean in self._by_username:
                raise ValueError("Tên đăng nhập đã được sử dụng")
            if e_clean in self._by_email:
                raise ValueError("Email này đã được đăng ký tài khoản")

            user_id = f"usr_{len(self._users) + 1:03d}"
            avatar = DEFAULT_AVATARS[len(self._users) % len(DEFAULT_AVATARS)]
            now_str = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

            new_record = {
                "id": user_id,
                "name": name.strip(),
                "full_name": name.strip(),
                "username": username.strip(),
                "email": e_clean,
                "phone": phone.strip() if phone else None,
                "address": None,
                "password_hash": hash_password(password),
                "role": "user",
                "status": "active",
                "avatar": avatar,
                "created_at": now_str,
            }
            self._users.append(new_record)
            self._save_to_disk()
            self._rebuild_indices()
            return self.to_user_model(new_record)

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
        with self._lock:
            u = self._by_id.get(user_id)
            if not u:
                return None

            disp_name = full_name or name
            if disp_name:
                u["name"] = disp_name.strip()
                u["full_name"] = disp_name.strip()

            if email:
                e_clean = email.strip().lower()
                if e_clean != u["email"].lower() and e_clean in self._by_email:
                    raise ValueError("Email này đã được tài khoản khác sử dụng")
                u["email"] = e_clean

            if phone is not None:
                u["phone"] = phone.strip() if phone else None

            if address is not None:
                u["address"] = address.strip() if address else None

            if avatar:
                u["avatar"] = avatar

            self._save_to_disk()
            self._rebuild_indices()
            return self.to_user_model(u)

    def change_password(self, user_id: str, old_password: str, new_password: str) -> Tuple[bool, str]:
        with self._lock:
            u = self._by_id.get(user_id)
            if not u:
                return False, "Không tìm thấy người dùng"
            if not verify_password(old_password, u["password_hash"]):
                return False, "Mật khẩu hiện tại không chính xác"
            u["password_hash"] = hash_password(new_password)
            self._save_to_disk()
            return True, "Đổi mật khẩu thành công"

    # ---------- Admin Quản lý User ----------
    def get_all_users(self) -> List[User]:
        return [self.to_user_model(u) for u in self._users]

    def update_role(self, user_id: str, new_role: str) -> User:
        with self._lock:
            u = self._by_id.get(user_id)
            if not u:
                raise ValueError("Không tìm thấy người dùng")
            if u["username"].lower() == "admin":
                raise ValueError("Không thể thay đổi quyền của tài khoản Admin mặc định")
            if new_role not in ("admin", "user"):
                raise ValueError("Vai trò không hợp lệ (admin hoặc user)")
            u["role"] = new_role
            self._save_to_disk()
            self._rebuild_indices()
            return self.to_user_model(u)

    def toggle_status(self, user_id: str, is_active: Optional[bool] = None) -> User:
        with self._lock:
            u = self._by_id.get(user_id)
            if not u:
                raise ValueError("Không tìm thấy người dùng")
            if u["username"].lower() == "admin":
                raise ValueError("Không thể khóa tài khoản Admin mặc định")
            if is_active is not None:
                u["status"] = "active" if is_active else "disabled"
            else:
                u["status"] = "disabled" if u.get("status") == "active" else "active"
            self._save_to_disk()
            self._rebuild_indices()
            return self.to_user_model(u)


user_service = UserService()
