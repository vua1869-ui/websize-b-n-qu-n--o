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


def mask_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Loại bỏ hoặc che mật khẩu, token, SĐT đầy đủ trước khi ghi log."""
    masked = {}
    sensitive_keys = {
        "password", "hashed_password", "token", "secret", "vnp_hashsecret",
        "api_key", "secret_key", "confirm_password", "new_password", "old_password"
    }
    phone_keys = {"phone", "customer_phone", "sender_phone"}

    for k, v in data.items():
        lower_k = k.lower()
        if any(sk in lower_k for sk in sensitive_keys):
            masked[k] = "[REDACTED]"
        elif any(pk in lower_k for pk in phone_keys):
            masked[k] = mask_phone(str(v))
        elif isinstance(v, dict):
            masked[k] = mask_sensitive_data(v)
        elif isinstance(v, str) and PHONE_RE.search(v):
            masked[k] = PHONE_RE.sub(r"\1\2****\4", v)
        else:
            masked[k] = v
    return masked


class SimpleFormatter(logging.Formatter):
    """Formatter chuẩn dòng đơn giản, che bớt số điện thoại trong message."""
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return PHONE_RE.sub(r"\1\2****\4", msg)


def setup_logging():
    """Cấu hình logging tập trung theo LOG_LEVEL."""
    log_level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Tránh gắn thêm handler nếu đã có
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        formatter = SimpleFormatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setLevel(log_level)


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
