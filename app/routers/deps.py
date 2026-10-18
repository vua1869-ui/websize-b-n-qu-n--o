import collections
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.schemas import User
from app.services.user_service import user_service

logger = logging.getLogger("aura.routers")
templates = Jinja2Templates(directory="app/templates")


# ==========================================
# Client IP Helper (chỉ tin X-Forwarded-For nếu cấu hình TRUSTED_PROXY_HEADERS)
# ==========================================
def get_client_ip(request: Request) -> str:
    if getattr(settings, "TRUSTED_PROXY_HEADERS", False):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        x_real_ip = request.headers.get("x-real-ip")
        if x_real_ip:
            return x_real_ip.strip()
    return request.client.host if request.client else "127.0.0.1"


# ==========================================
# Open Redirect Defense
# ==========================================
def safe_redirect(target: Optional[str], default: str = "/") -> str:
    """
    Chặn open redirect: chỉ nhận chuỗi bắt đầu bằng '/',
    KHÔNG bắt đầu bằng '//' hay '/\\', không chứa '\\' hoặc ký tự điều khiển.
    """
    if not target or not isinstance(target, str):
        return default
    s = target.strip()
    if not s.startswith("/"):
        return default
    if s.startswith("//") or s.startswith("/\\"):
        return default
    if "\\" in s:
        return default
    for ch in s:
        if ord(ch) < 32 or ord(ch) == 127:
            return default
    return s


# ==========================================
# Rate Limiters & Periodic Memory Cleanup
# ==========================================
_rate_limit_lock = threading.Lock()
_login_ip_attempts: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
_login_pair_attempts: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
_register_ip_attempts: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
_forgot_pw_reqs: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
_track_reqs: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
_voucher_validate_reqs: Dict[str, collections.deque] = collections.defaultdict(collections.deque)

# Backward compatibility aliases for existing tests
def _login_key(ip: str, username: str) -> str:
    return f"{ip}|{username.strip().lower()}"

_login_fails = _login_pair_attempts
_login_lock = _rate_limit_lock

LOGIN_WINDOW_SEC = 300       # 5 phút
LOGIN_MAX_IP_ATTEMPTS = 20   # 20 lần thử sai từ cùng 1 IP trong 5 phút
LOGIN_MAX_PAIR_ATTEMPTS = 5  # 5 lần thử sai cho cùng (IP + username) trong 5 phút

REGISTER_WINDOW_SEC = 3600   # 1 giờ
REGISTER_MAX_IP_ATTEMPTS = 5 # 5 lần đăng ký / giờ / IP

FORGOT_PW_WINDOW_SEC = 900   # 15 phút
FORGOT_PW_MAX_REQ = 5

TRACK_WINDOW_SEC = 60        # 1 phút
TRACK_MAX_REQ = 20

VOUCHER_VALIDATE_WINDOW_SEC = 60 # 1 phút
VOUCHER_VALIDATE_MAX_REQ = 20    # 20 lần kiểm tra voucher / phút / IP

_last_cleanup_time = time.time()


def _cleanup_expired_rate_limits(now: float) -> None:
    global _last_cleanup_time
    if now - _last_cleanup_time < 300:
        return
    _last_cleanup_time = now

    def _purge(d: Dict[str, collections.deque], max_age: float):
        keys_to_delete = []
        for k, q in d.items():
            while q and now - q[0] > max_age:
                q.popleft()
            if not q:
                keys_to_delete.append(k)
        for k in keys_to_delete:
            d.pop(k, None)

    _purge(_login_ip_attempts, LOGIN_WINDOW_SEC)
    _purge(_login_pair_attempts, LOGIN_WINDOW_SEC)
    _purge(_register_ip_attempts, REGISTER_WINDOW_SEC)
    _purge(_forgot_pw_reqs, FORGOT_PW_WINDOW_SEC)
    _purge(_track_reqs, TRACK_WINDOW_SEC)
    _purge(_voucher_validate_reqs, VOUCHER_VALIDATE_WINDOW_SEC)


def check_login_rate_limit(ip: str, username: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        _cleanup_expired_rate_limits(now)
        # 1. Theo IP
        q_ip = _login_ip_attempts[ip]
        while q_ip and now - q_ip[0] > LOGIN_WINDOW_SEC:
            q_ip.popleft()
        if len(q_ip) >= LOGIN_MAX_IP_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Địa chỉ IP của bạn đã thử đăng nhập thất bại quá nhiều lần. Vui lòng thử lại sau 5 phút.",
            )

        # 2. Theo cặp IP + username
        key_pair = f"{ip}|{username.strip().lower()}"
        q_pair = _login_pair_attempts[key_pair]
        while q_pair and now - q_pair[0] > LOGIN_WINDOW_SEC:
            q_pair.popleft()
        if len(q_pair) >= LOGIN_MAX_PAIR_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Bạn đã thử sai quá nhiều lần cho tài khoản này. Vui lòng thử lại sau 5 phút.",
            )


def record_login_failure(ip: str, username: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        _login_ip_attempts[ip].append(now)
        key_pair = f"{ip}|{username.strip().lower()}"
        _login_pair_attempts[key_pair].append(now)


def reset_login_failures(ip: str, username: str) -> None:
    with _rate_limit_lock:
        key_pair = f"{ip}|{username.strip().lower()}"
        _login_pair_attempts.pop(key_pair, None)


def check_register_rate_limit(ip: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        _cleanup_expired_rate_limits(now)
        q = _register_ip_attempts[ip]
        while q and now - q[0] > REGISTER_WINDOW_SEC:
            q.popleft()
        if len(q) >= REGISTER_MAX_IP_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Bạn đã tạo quá nhiều tài khoản trong thời gian ngắn. Vui lòng thử lại sau 1 giờ.",
            )
        q.append(now)


def check_forgot_password_rate_limit(ip: str, email: str) -> None:
    key = f"{ip}|{email.strip().lower()}"
    now = time.time()
    with _rate_limit_lock:
        _cleanup_expired_rate_limits(now)
        q = _forgot_pw_reqs[key]
        while q and now - q[0] > FORGOT_PW_WINDOW_SEC:
            q.popleft()
        if len(q) >= FORGOT_PW_MAX_REQ:
            raise HTTPException(
                status_code=429,
                detail="Bạn đã yêu cầu đặt lại mật khẩu quá nhiều lần. Vui lòng thử lại sau 15 phút.",
            )
        q.append(now)


def check_public_track_rate_limit(request: Request) -> None:
    ip = get_client_ip(request)
    now = time.time()
    with _rate_limit_lock:
        _cleanup_expired_rate_limits(now)
        q = _track_reqs[ip]
        while q and now - q[0] > TRACK_WINDOW_SEC:
            q.popleft()
        if len(q) >= TRACK_MAX_REQ:
            raise HTTPException(
                status_code=429,
                detail="Bạn đã thực hiện quá nhiều yêu cầu tra cứu. Vui lòng thử lại sau 1 phút.",
            )
        q.append(now)


def check_voucher_validate_rate_limit(request: Request) -> None:
    ip = get_client_ip(request)
    now = time.time()
    with _rate_limit_lock:
        _cleanup_expired_rate_limits(now)
        q = _voucher_validate_reqs[ip]
        while q and now - q[0] > VOUCHER_VALIDATE_WINDOW_SEC:
            q.popleft()
        if len(q) >= VOUCHER_VALIDATE_MAX_REQ:
            raise HTTPException(
                status_code=429,
                detail="Bạn đã kiểm tra mã giảm giá quá nhiều lần. Vui lòng thử lại sau 1 phút.",
            )
        q.append(now)


# ==========================================
# Helpers Masking
# ==========================================
def normalize_phone(p: Optional[str]) -> str:
    if not p:
        return ""
    digits = "".join(c for c in str(p) if c.isdigit())
    if digits.startswith("84") and len(digits) > 9:
        digits = "0" + digits[2:]
    return digits


def mask_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""
    clean = "".join(c for c in str(phone).strip() if c.isdigit())
    if len(clean) >= 6:
        return f"{clean[:3]}****{clean[-3:]}"
    return "****"


def mask_address(address: Optional[str], province: Optional[str] = None) -> str:
    if province and str(province).strip():
        return str(province).strip()
    if address:
        parts = [p.strip() for p in str(address).split(",") if p.strip()]
        if parts:
            return parts[-1]
    return "Việt Nam"


def verify_order_access(order: Dict[str, Any], user: Optional[User], phone: Optional[str]) -> Tuple[bool, bool]:
    order_user_id = order.get("user_id")
    if order_user_id:
        if user and user.is_active:
            if user.role == "admin" or str(user.id) == str(order_user_id):
                return True, True
        return False, False

    if user and user.is_active and user.role == "admin":
        return True, True

    if phone:
        order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
        clean_input = normalize_phone(phone)
        clean_order = normalize_phone(order_phone)
        if clean_input and clean_input == clean_order:
            return True, False

    return False, False


# ==========================================
# Authentication & RBAC Dependencies
# ==========================================
def get_current_user_optional(request: Request) -> Optional[User]:
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("aura_session")
    if not token:
        return None
    return user_service.verify_session_token(token)


def get_current_user(request: Request) -> User:
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập để tiếp tục")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản của bạn đã bị khóa")
    return user


def require_admin(request: Request) -> User:
    user = get_current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập trang quản trị")
    return user


class AdminRedirectException(Exception):
    def __init__(self, location: str):
        self.location = location


class AdminForbiddenException(Exception):
    def __init__(self, user: Optional[User]):
        self.user = user


def require_admin_page(request: Request) -> User:
    """
    Gom đoạn kiểm tra quyền lặp lại ở 6 route trang admin thành một dependency:
    - Chưa đăng nhập -> redirect sang login
    - Không phải admin -> hiển thị trang 403
    """
    user = get_current_user_optional(request)
    if not user:
        path = request.url.path
        raise AdminRedirectException(f"/login?redirect={safe_redirect(path, default='/admin')}")
    if user.role != "admin":
        raise AdminForbiddenException(user)
    return user


def base_context(request: Request, **extra: Any) -> Dict[str, Any]:
    """
    Gom context template lặp lại cho các trang HTML.
    """
    user = extra.get("user") or get_current_user_optional(request)
    ctx = {
        "request": request,
        "app_name": settings.APP_NAME,
        "version": settings.VERSION,
        "shipping_fee": settings.SHIPPING_FEE,
        "free_ship_threshold": settings.FREE_SHIPPING_THRESHOLD,
        "combo_percent": settings.COMBO_DISCOUNT_PERCENT,
        "user": user,
    }
    ctx.update(extra)
    return ctx
