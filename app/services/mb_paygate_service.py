"""MB VietQR Paygate signing, collection and refund API helpers."""
import hashlib
import requests

from app.config import settings


class MBPaygateError(Exception):
    pass


class MBPaygateService:
    def is_configured(self):
        return all((settings.MB_PAYGATE_MERCHANT_ID, settings.MB_PAYGATE_ACCESS_CODE,
                    settings.MB_PAYGATE_HASH_SECRET, settings.MB_PAYGATE_RETURN_URL,
                    settings.MB_PAYGATE_CANCEL_URL, settings.MB_PAYGATE_IPN_URL))

    def sign(self, data):
        fields = [(str(k), str(v)) for k, v in data.items()
                  if k not in ("mac", "mac_type") and v is not None]
        query = "&".join(f"{k}={v}" for k, v in sorted(fields))
        mac_type = str(data.get("mac_type", "MD5")).upper()
        digest = hashlib.sha256 if mac_type == "SHA256" else hashlib.md5
        return digest((settings.MB_PAYGATE_HASH_SECRET + query).encode("utf-8")).hexdigest().upper()

    def verify(self, data):
        return bool(data.get("mac")) and self.sign(data) == str(data["mac"]).upper()

    def _post(self, url, payload):
        try:
            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MBPaygateError("Không thể kết nối MB Paygate") from exc
        if not isinstance(data, dict):
            raise MBPaygateError("Phản hồi MB Paygate không hợp lệ")
        return data

    def create_order(self, order_id, amount, client_ip):
        if not self.is_configured():
            raise MBPaygateError("MB VietQR Paygate chưa được cấu hình đầy đủ")
        payload = {
            "amount": int(amount), "currency": "VND", "access_code": settings.MB_PAYGATE_ACCESS_CODE,
            "merchant_id": settings.MB_PAYGATE_MERCHANT_ID, "order_info": f"AURA {order_id}",
            "order_reference": order_id, "return_url": settings.MB_PAYGATE_RETURN_URL,
            "cancel_url": settings.MB_PAYGATE_CANCEL_URL, "ipn_url": settings.MB_PAYGATE_IPN_URL,
            "pay_type": "QR", "ip_address": client_ip or "127.0.0.1", "payment_method": "QR",
            "mac_type": "MD5",
        }
        payload["mac"] = self.sign(payload)
        return self._post(settings.MB_PAYGATE_CREATE_ORDER_URL, payload)

    def refund(self, amount, transaction_reference_id, trans_date, description="Order refund"):
        if not all((settings.MB_PAYGATE_MERCHANT_ID, settings.MB_PAYGATE_ACCESS_CODE, settings.MB_PAYGATE_HASH_SECRET)):
            raise MBPaygateError("MB VietQR Paygate chưa được cấu hình đầy đủ")
        payload = {
            "txn_amount": int(amount), "desc": description[:200],
            "access_code": settings.MB_PAYGATE_ACCESS_CODE, "merchant_id": settings.MB_PAYGATE_MERCHANT_ID,
            "transaction_reference_id": str(transaction_reference_id), "trans_date": str(trans_date),
            "mac_type": "MD5",
        }
        payload["mac"] = self.sign(payload)
        return self._post(settings.MB_PAYGATE_REFUND_URL, payload)


mb_paygate_service = MBPaygateService()
