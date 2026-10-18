"""
Dịch vụ tích hợp Cổng thanh toán MoMo (Sandbox & Production):
- Tạo Payment URL (All In One) chuẩn HMAC-SHA256 theo tài liệu MoMo Payment Gateway v2 API.
- Xác thực chữ ký phản hồi Return URL và IPN webhook.
- Gọi API Hoàn tiền (Refund) tự động cho Merchant.
"""
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, Optional, Tuple

import requests

from app.config import settings

logger = logging.getLogger(__name__)


class MoMoService:
    def __init__(self):
        pass

    def build_payment_url(
        self,
        order_id: str,
        amount: int,
        order_desc: Optional[str] = None,
        extra_data: str = "",
    ) -> Dict[str, Any]:
        """
        Tạo yêu cầu thanh toán sang MoMo v2 Gateway API (requestType: captureWallet).
        Ký bằng HMAC-SHA256 với secretKey của đối tác.
        Trả về dictionary chứa payUrl, qrCodeUrl, deeplink...
        """
        partner_code = settings.MOMO_PARTNER_CODE
        access_key = settings.MOMO_ACCESS_KEY
        secret_key = settings.MOMO_SECRET_KEY
        redirect_url = settings.MOMO_RETURN_URL
        ipn_url = settings.MOMO_IPN_URL
        order_info = order_desc or f"Thanh toan don hang {order_id}"
        request_type = "captureWallet"
        request_id = f"{order_id}_{int(time.time() * 1000)}"

        # Chuỗi dữ liệu chuẩn để tạo chữ ký MoMo v2
        raw_signature = (
            f"accessKey={access_key}&"
            f"amount={int(amount)}&"
            f"extraData={extra_data}&"
            f"ipnUrl={ipn_url}&"
            f"orderId={order_id}&"
            f"orderInfo={order_info}&"
            f"partnerCode={partner_code}&"
            f"redirectUrl={redirect_url}&"
            f"requestId={request_id}&"
            f"requestType={request_type}"
        )

        signature = hmac.new(
            secret_key.encode("utf-8"),
            raw_signature.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        payload = {
            "partnerCode": partner_code,
            "partnerName": settings.APP_NAME,
            "storeId": "AuraStore",
            "requestId": request_id,
            "amount": int(amount),
            "orderId": order_id,
            "orderInfo": order_info,
            "redirectUrl": redirect_url,
            "ipnUrl": ipn_url,
            "lang": "vi",
            "extraData": extra_data,
            "requestType": request_type,
            "signature": signature,
        }

        try:
            logger.info("MoMo Create Payment Request -> %s, orderId=%s, amount=%s", settings.MOMO_API_URL, order_id, amount)
            response = requests.post(
                settings.MOMO_API_URL,
                json=payload,
                headers={"Content-Type": "application/json; charset=UTF-8"},
                timeout=15,
            )
            res_json = response.json()
            logger.info("MoMo Response: %s", res_json)

            if res_json.get("resultCode") == 0:
                return {
                    "success": True,
                    "payment_url": res_json.get("payUrl"),
                    "qr_code_url": res_json.get("qrCodeUrl"),
                    "deeplink": res_json.get("deeplink"),
                    "order_id": order_id,
                    "request_id": request_id,
                    "raw": res_json,
                }
            else:
                err_msg = res_json.get("message") or f"MoMo error code {res_json.get('resultCode')}"
                logger.warning("MoMo API returned error: %s", err_msg)
                return {
                    "success": False,
                    "message": err_msg,
                    "result_code": res_json.get("resultCode"),
                    "order_id": order_id,
                    "raw": res_json,
                }
        except Exception as exc:
            logger.error("Lỗi khi kết nối API MoMo: %s", exc)
            return {
                "success": False,
                "message": f"Không thể kết nối đến cổng thanh toán MoMo: {str(exc)}",
                "order_id": order_id,
            }

    def verify_signature(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Xác thực chữ ký phản hồi từ MoMo (dùng chung cho cả Return URL và IPN webhook).
        Chuỗi ký chuẩn của MoMo Return/IPN:
        accessKey=$accessKey&amount=$amount&extraData=$extraData&message=$message&orderId=$orderId&orderInfo=$orderInfo&orderType=$orderType&partnerCode=$partnerCode&payType=$payType&requestId=$requestId&responseTime=$responseTime&resultCode=$resultCode&transId=$transId
        """
        data = dict(params)
        received_signature = data.get("signature", "")
        access_key = settings.MOMO_ACCESS_KEY
        secret_key = settings.MOMO_SECRET_KEY

        amount = str(data.get("amount", ""))
        extra_data = str(data.get("extraData", ""))
        message = str(data.get("message", ""))
        order_id = str(data.get("orderId", ""))
        order_info = str(data.get("orderInfo", ""))
        order_type = str(data.get("orderType", ""))
        partner_code = str(data.get("partnerCode", ""))
        pay_type = str(data.get("payType", ""))
        request_id = str(data.get("requestId", ""))
        response_time = str(data.get("responseTime", ""))
        result_code = str(data.get("resultCode", ""))
        trans_id = str(data.get("transId", ""))

        raw_signature = (
            f"accessKey={access_key}&"
            f"amount={amount}&"
            f"extraData={extra_data}&"
            f"message={message}&"
            f"orderId={order_id}&"
            f"orderInfo={order_info}&"
            f"orderType={order_type}&"
            f"partnerCode={partner_code}&"
            f"payType={pay_type}&"
            f"requestId={request_id}&"
            f"responseTime={response_time}&"
            f"resultCode={result_code}&"
            f"transId={trans_id}"
        )

        expected_signature = hmac.new(
            secret_key.encode("utf-8"),
            raw_signature.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        is_valid = bool(
            received_signature and hmac.compare_digest(received_signature.lower(), expected_signature.lower())
        )
        if not is_valid:
            logger.warning(
                "MoMo signature verification failed! Expected: %s, Received: %s, Raw: %s",
                expected_signature,
                received_signature,
                raw_signature,
            )

        return is_valid, data

    def refund(
        self,
        order_id: str,
        trans_id: str,
        amount: int,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Gọi API hoàn tiền (Refund) của MoMo:
        Endpoint: https://test-payment.momo.vn/v2/gateway/api/refund
        Chuỗi ký chuẩn:
        accessKey=$accessKey&amount=$amount&description=$description&orderId=$orderId&partnerCode=$partnerCode&requestId=$requestId&transId=$transId
        """
        partner_code = settings.MOMO_PARTNER_CODE
        access_key = settings.MOMO_ACCESS_KEY
        secret_key = settings.MOMO_SECRET_KEY

        refund_order_id = f"REF_{order_id}_{int(time.time())}"
        request_id = f"REQ_{order_id}_{int(time.time() * 1000)}"
        desc = description or f"Hoan tien don hang {order_id}"

        # Chuyển trans_id thành số nguyên nếu là dạng chuỗi số (MoMo yêu cầu transId là int64)
        try:
            trans_id_val = int(trans_id)
        except (ValueError, TypeError):
            trans_id_val = trans_id

        raw_signature = (
            f"accessKey={access_key}&"
            f"amount={int(amount)}&"
            f"description={desc}&"
            f"orderId={refund_order_id}&"
            f"partnerCode={partner_code}&"
            f"requestId={request_id}&"
            f"transId={trans_id_val}"
        )

        signature = hmac.new(
            secret_key.encode("utf-8"),
            raw_signature.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        payload = {
            "partnerCode": partner_code,
            "orderId": refund_order_id,
            "requestId": request_id,
            "amount": int(amount),
            "transId": trans_id_val,
            "lang": "vi",
            "description": desc,
            "signature": signature,
        }

        try:
            logger.info("MoMo Refund Request -> %s, payload: %s", settings.MOMO_REFUND_URL, payload)
            response = requests.post(
                settings.MOMO_REFUND_URL,
                json=payload,
                headers={"Content-Type": "application/json; charset=UTF-8"},
                timeout=15,
            )
            res_json = response.json()
            logger.info("MoMo Refund Response: %s", res_json)

            is_success = res_json.get("resultCode") == 0
            return {
                "success": is_success,
                "result_code": res_json.get("resultCode"),
                "message": res_json.get("message") or ("Hoàn tiền thành công" if is_success else "Lỗi hoàn tiền"),
                "refund_trans_id": res_json.get("transId") or refund_order_id,
                "data": res_json,
            }
        except Exception as exc:
            logger.error("Lỗi khi kết nối API Hoàn tiền MoMo: %s", exc)
            return {
                "success": False,
                "message": f"Không thể kết nối đến cổng hoàn tiền MoMo: {str(exc)}",
                "refund_trans_id": refund_order_id,
            }


momo_service = MoMoService()
