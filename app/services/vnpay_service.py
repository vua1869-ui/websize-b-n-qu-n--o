"""
Dịch vụ tích hợp Cổng thanh toán VNPay (Sandbox & Production):
- Tạo Payment URL chuẩn HMAC-SHA512 theo tài liệu VNPay 2.1.0.
- Xác thực chữ ký dữ liệu Return URL và IPN.
"""
import datetime as dt
import hashlib
import hmac
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from app.config import settings


class VNPayService:
    def __init__(self):
        pass

    def build_payment_url(
        self,
        order_id: str,
        amount: int,
        client_ip: str = "127.0.0.1",
        order_desc: Optional[str] = None,
        bank_code: Optional[str] = None,
    ) -> str:
        """
        Tạo URL redirect sang cổng VNPay.
        - vnp_Amount = amount * 100 (theo quy định VNPay: nhân 100 loại bỏ phần thập phân).
        - Sắp xếp tham số theo thứ tự chữ cái và ký bằng HMAC-SHA512.
        """
        now = dt.datetime.now()
        create_date = now.strftime("%Y%m%d%H%M%S")

        vnp_params = {
            "vnp_Version": "2.1.0",
            "vnp_Command": "pay",
            "vnp_TmnCode": settings.VNPAY_TMN_CODE,
            "vnp_Amount": str(int(amount) * 100),
            "vnp_CreateDate": create_date,
            "vnp_CurrCode": "VND",
            "vnp_IpAddr": client_ip or "127.0.0.1",
            "vnp_Locale": "vn",
            "vnp_OrderInfo": order_desc or f"Thanh toan don hang {order_id}",
            "vnp_OrderType": "other",
            "vnp_ReturnUrl": settings.VNPAY_RETURN_URL,
            "vnp_TxnRef": order_id,
        }

        if bank_code:
            vnp_params["vnp_BankCode"] = bank_code

        # Sắp xếp các tham số theo thứ tự a-z
        sorted_params = sorted(vnp_params.items())
        hash_data = urllib.parse.urlencode(sorted_params)

        # Ký HMAC-SHA512
        secure_hash = hmac.new(
            settings.VNPAY_HASH_SECRET.encode("utf-8"),
            hash_data.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        return f"{settings.VNPAY_URL}?{hash_data}&vnp_SecureHash={secure_hash}"

    def verify_response(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Xác thực chữ ký phản hồi từ VNPay (Return URL hoặc IPN).
        Trả về (is_valid, clean_params).
        """
        clean_params = dict(params)
        received_hash = clean_params.pop("vnp_SecureHash", None)
        clean_params.pop("vnp_SecureHashType", None)

        # Chỉ giữ lại các trường bắt đầu bằng vnp_
        vnp_data = {k: str(v) for k, v in clean_params.items() if k.startswith("vnp_")}
        sorted_params = sorted(vnp_data.items())
        hash_data = urllib.parse.urlencode(sorted_params)

        expected_hash = hmac.new(
            settings.VNPAY_HASH_SECRET.encode("utf-8"),
            hash_data.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        is_valid = bool(received_hash and hmac.compare_digest(received_hash.lower(), expected_hash.lower()))
        return is_valid, vnp_data


vnpay_service = VNPayService()
