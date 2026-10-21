import json
import logging
import re
import sys
from typing import Any, Dict, Optional

from app.config import settings

# Pattern để che số điện thoại trong log nếu xuất hiện dạng chuỗi
PHONE_RE = re.compile(r"(\+?84|0)(\d{2,3})(\d{3})(\d{3})")


def mask_phone(phone: str = "") -> str:
    """Che bớt số điện thoại, giữ lại đầu và cuối (ví dụ: 098****321)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) >= 9:
        return digits[:3] + "****" + digits[-3:]
    return "***"


def mask_account_number(account_num: str = "") -> str:
    """Che bớt số tài khoản ngân hàng, giữ lại tối đa 4 ký tự cuối (ví dụ: ******7890)."""
    if not account_num:
        return ""
    s = str(account_num).strip()
    if len(s) <= 4:
        return "****"
    return "*" * (len(s) - 4) + s[-4:]


def mask_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Loại bỏ hoặc che mật khẩu, token, SĐT, số tài khoản đầy đủ trước khi ghi log."""
    masked = {}
    sensitive_keys = {
        "password", "hashed_password", "token", "secret",
        "api_key", "secret_key", "confirm_password", "new_password", "old_password"
    }
    phone_keys = {"phone", "customer_phone", "sender_phone"}
    account_keys = {"account_number", "refund_account_number", "bank_account", "bank_account_number"}

    for k, v in data.items():
        lower_k = k.lower()
        if any(sk in lower_k for sk in sensitive_keys):
            masked[k] = "[REDACTED]"
        elif any(pk in lower_k for pk in phone_keys):
            masked[k] = mask_phone(str(v))
        elif any(ak in lower_k for ak in account_keys):
            masked[k] = mask_account_number(str(v))
        elif isinstance(v, dict):
            masked[k] = mask_sensitive_data(v)
        elif isinstance(v, str) and PHONE_RE.search(v):
            masked[k] = PHONE_RE.sub(r"\1\2****\4", v)
        else:
            masked[k] = v
    return masked


import contextvars

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


class RequestIdFilter(logging.Filter):
    """Filter tự động gắn request_id từ ContextVar vào mỗi LogRecord."""
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id") or not record.request_id:
            record.request_id = request_id_ctx.get() or "-"
        return True


class SimpleFormatter(logging.Formatter):
    """Formatter chuẩn dòng đơn giản, che bớt số điện thoại trong message và hiển thị Request-ID."""
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id") or not record.request_id:
            record.request_id = request_id_ctx.get() or "-"
        msg = super().format(record)
        return PHONE_RE.sub(r"\1\2****\4", msg)


def setup_logging():
    """Cấu hình logging tập trung theo LOG_LEVEL kèm Request-ID."""
    log_level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    req_filter = RequestIdFilter()
    root_logger.addFilter(req_filter)

    formatter = SimpleFormatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] [%(request_id)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Tránh gắn thêm handler nếu đã có
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        handler.addFilter(req_filter)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setLevel(log_level)
            handler.addFilter(req_filter)
            handler.setFormatter(formatter)


def get_logger(name: str = "aura") -> logging.Logger:
    """Lấy logger theo tên module."""
    return logging.getLogger(name)


money_logger = get_logger("aura.money")


def log_money_event(event_type: str, details: Optional[Dict[str, Any]] = None, **kwargs):
    """
    Ghi log các sự kiện liên quan đến tiền bạc:
    - tạo đơn
    - xác nhận thanh toán
    - thanh toán bị từ chối vì sai số tiền
    - hủy đơn
    - thay đổi điểm thưởng
    """
    payload = {}
    if details and isinstance(details, dict):
        payload.update(details)
    if kwargs:
        payload.update(kwargs)
    safe_details = mask_sensitive_data(payload)
    money_logger.info(
        "MONEY_EVENT [%s]: %s",
        event_type,
        json.dumps(safe_details, ensure_ascii=False)
    )
