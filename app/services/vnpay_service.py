"""
Dịch vụ tích hợp Cổng thanh toán VNPay (Sandbox & Production):
- Tạo Payment URL chuẩn HMAC-SHA512 theo tài liệu VNPay 2.1.0.
- Xác thực chữ ký dữ liệu Return URL và IPN.
"""
from datetime import datetime
import datetime as dt
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import requests

from app.config import settings

logger = logging.getLogger(__name__)


def create_vnpay_url(order_id, amount, order_info, ip_addr, vnp_tmn_code, vnp_hash_secret, vnp_url, vnp_return_url, bank_code=None):
    vnp_Params = {
        'vnp_Version': '2.1.0',
        'vnp_Command': 'pay',
        'vnp_TmnCode': str(vnp_tmn_code).strip(),
        'vnp_Amount': str(int(float(amount) * 100)),
        'vnp_CurrCode': 'VND',
        'vnp_TxnRef': str(order_id),
        'vnp_OrderInfo': str(order_info),
        'vnp_OrderType': 'other',
        'vnp_Locale': 'vn',
        'vnp_ReturnUrl': str(vnp_return_url).strip(),
        'vnp_IpAddr': '127.0.0.1' if ip_addr in ['::1', 'localhost', None, ''] else str(ip_addr),
        'vnp_CreateDate': datetime.now().strftime('%Y%m%d%H%M%S')
    }

    if bank_code:
        vnp_Params['vnp_BankCode'] = str(bank_code).strip()

    inputData = sorted(vnp_Params.items())
    queryString = ''
    seq = 0
    for key, val in inputData:
        if str(key).startswith('vnp_') and val is not None and str(val) != "":
            encoded_val = urllib.parse.quote_plus(str(val))
            if seq == 1:
                queryString += "&" + str(key) + '=' + encoded_val
            else:
                seq = 1
                queryString = str(key) + '=' + encoded_val

    secret_key = str(vnp_hash_secret).strip()
    hashValue = hmac.new(
        secret_key.encode('utf-8'),
        queryString.encode('utf-8'),
        hashlib.sha512
    ).hexdigest()

    return f"{vnp_url}?{queryString}&vnp_SecureHash={hashValue}"


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
        Tạo URL redirect sang cổng VNPay theo chuẩn VNPay 2.1.0 HMAC-SHA512.
        - vnp_Amount = amount * 100 (theo quy định VNPay: nhân 100 loại bỏ phần thập phân).
        - Sắp xếp tham số theo thứ tự chữ cái và encode bằng quote_plus.
        """
        order_info = order_desc or f"Thanh toan don hang {order_id}"
        return create_vnpay_url(
            order_id=order_id,
            amount=amount,
            order_info=order_info,
            ip_addr=client_ip,
            vnp_tmn_code=settings.VNPAY_TMN_CODE,
            vnp_hash_secret=settings.VNPAY_HASH_SECRET,
            vnp_url=settings.VNPAY_URL,
            vnp_return_url=settings.VNPAY_RETURN_URL,
            bank_code=bank_code,
        )

    def verify_response(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Xác thực chữ ký phản hồi từ VNPay (Return URL hoặc IPN) chuẩn VNPay 2.1.0.
        Trả về (is_valid, clean_params).
        """
        clean_params = dict(params)
        received_hash = clean_params.pop("vnp_SecureHash", None)
        clean_params.pop("vnp_SecureHashType", None)

        # Chỉ giữ lại các trường bắt đầu bằng vnp_
        vnp_data = {k: str(v) for k, v in clean_params.items() if str(k).startswith("vnp_") and v is not None and str(v) != ""}
        inputData = sorted(vnp_data.items())

        # Tạo queryString chuẩn quote_plus
        queryString = ''
        seq = 0
        for key, val in inputData:
            encoded_val = urllib.parse.quote_plus(str(val))
            if seq == 1:
                queryString += "&" + str(key) + '=' + encoded_val
            else:
                seq = 1
                queryString = str(key) + '=' + encoded_val

        secret_key = str(settings.VNPAY_HASH_SECRET).strip()
        expected_hash = hmac.new(
            secret_key.encode("utf-8"),
            queryString.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        is_valid = bool(received_hash and hmac.compare_digest(received_hash.lower(), expected_hash.lower()))

        # Fallback thêm urlencode nếu đối tác test gửi theo urlencode legacy
        if not is_valid and received_hash:
            hash_data_legacy = urllib.parse.urlencode(inputData)
            legacy_hash = hmac.new(
                secret_key.encode("utf-8"),
                hash_data_legacy.encode("utf-8"),
                hashlib.sha512,
            ).hexdigest()
            is_valid = bool(hmac.compare_digest(received_hash.lower(), legacy_hash.lower()))

        return is_valid, vnp_data

    def refund(
        self,
        order_id: str,
        trans_no: str,
        amount: int,
        trans_date: Optional[str] = None,
        create_by: str = "admin",
        client_ip: str = "127.0.0.1",
        order_desc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Gửi yêu cầu hoàn tiền (Refund) tới API VNPay 2.1.0:
        Endpoint: https://sandbox.vnpayment.vn/merchant_webapi/api/transaction
        Chuỗi ký chuẩn HMAC-SHA512 ngăn cách bằng dấu gạch đứng | :
        vnp_RequestId|vnp_Version|vnp_Command|vnp_TmnCode|vnp_TransactionType|vnp_TxnRef|vnp_Amount|vnp_TransactionNo|vnp_TransactionDate|vnp_CreateBy|vnp_CreateDate|vnp_IpAddr|vnp_OrderInfo
        """
        now = dt.datetime.now()
        vnp_request_id = f"{order_id}_{int(time.time() * 1000)}"
        vnp_version = "2.1.0"
        vnp_command = "refund"
        vnp_tmn_code = settings.VNPAY_TMN_CODE
        vnp_transaction_type = "02"  # 02: Hoàn toàn phần, 03: Hoàn một phần
        vnp_txn_ref = order_id
        vnp_amount = str(int(amount) * 100)
        vnp_transaction_no = str(trans_no or "")
        vnp_transaction_date = trans_date or now.strftime("%Y%m%d%H%M%S")
        vnp_create_by = create_by or "admin"
        vnp_create_date = now.strftime("%Y%m%d%H%M%S")
        vnp_ip_addr = client_ip or "127.0.0.1"
        vnp_order_info = order_desc or f"Hoan tien don hang {order_id}"

        # Chuỗi dữ liệu nối bằng | theo đúng đặc tả VNPay 2.1.0 Refund API
        raw_hash = (
            f"{vnp_request_id}|"
            f"{vnp_version}|"
            f"{vnp_command}|"
            f"{vnp_tmn_code}|"
            f"{vnp_transaction_type}|"
            f"{vnp_txn_ref}|"
            f"{vnp_amount}|"
            f"{vnp_transaction_no}|"
            f"{vnp_transaction_date}|"
            f"{vnp_create_by}|"
            f"{vnp_create_date}|"
            f"{vnp_ip_addr}|"
            f"{vnp_order_info}"
        )

        secure_hash = hmac.new(
            settings.VNPAY_HASH_SECRET.encode("utf-8"),
            raw_hash.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        payload = {
            "vnp_RequestId": vnp_request_id,
            "vnp_Version": vnp_version,
            "vnp_Command": vnp_command,
            "vnp_TmnCode": vnp_tmn_code,
            "vnp_TransactionType": vnp_transaction_type,
            "vnp_TxnRef": vnp_txn_ref,
            "vnp_Amount": vnp_amount,
            "vnp_OrderInfo": vnp_order_info,
            "vnp_TransactionNo": vnp_transaction_no,
            "vnp_TransactionDate": vnp_transaction_date,
            "vnp_CreateBy": vnp_create_by,
            "vnp_CreateDate": vnp_create_date,
            "vnp_IpAddr": vnp_ip_addr,
            "vnp_SecureHash": secure_hash,
        }

        try:
            logger.info("VNPay Refund Request -> %s, TxnRef=%s, Amount=%s", settings.VNPAY_API_URL, order_id, amount)
            response = requests.post(
                settings.VNPAY_API_URL,
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=15,
            )
            res_json = response.json()
            logger.info("VNPay Refund Response: %s", res_json)

            rsp_code = str(res_json.get("vnp_ResponseCode", ""))
            is_success = rsp_code == "00"
            msg = res_json.get("vnp_Message") or f"VNPay response code {rsp_code}"
            return {
                "success": is_success,
                "response_code": rsp_code,
                "message": msg,
                "refund_trans_id": res_json.get("vnp_ResponseId") or vnp_request_id,
                "data": res_json,
            }
        except Exception as exc:
            logger.error("Lỗi khi kết nối API Hoàn tiền VNPay: %s", exc)
            return {
                "success": False,
                "message": f"Không thể kết nối đến cổng hoàn tiền VNPay: {str(exc)}",
                "refund_trans_id": vnp_request_id,
            }


vnpay_service = VNPayService()

