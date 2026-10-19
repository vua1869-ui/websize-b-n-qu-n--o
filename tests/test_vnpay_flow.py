"""
Kiểm thử toàn diện luồng tích hợp cổng thanh toán VNPay 2.1.0:
1. Cấu hình VNPay (app/config.py và .env.example) và validate môi trường production.
2. Tạo Payment URL chuẩn VNPay 2.1.0:
   - Mã hóa tham số, sắp xếp alphabet
   - vnp_Amount nhân 100
   - vnp_CreateDate và vnp_ExpireDate theo múi giờ GMT+7
   - Chữ ký chuẩn HMAC-SHA512
   - Chặn tạo link thanh toán cho đơn đã thanh toán hoặc đã hủy
3. Xác minh chữ ký Return URL và IPN:
   - Chữ ký hợp lệ, chữ ký sai, dữ liệu bị giả mạo / sửa đổi
   - Khóa bí mật rỗng bị từ chối
4. Xử lý Return URL:
   - Thanh toán thành công (00 & 00)
   - Khách hủy thanh toán trên cổng VNPay (mã lỗi 24)
   - Sai số tiền thanh toán
   - Thanh toán cho đơn đã bị hủy trước đó (tự động chuyển refund_pending)
5. Xử lý IPN Webhook:
   - Chuẩn phản hồi JSON (RspCode 00, 01, 02, 04, 97)
   - Chống callback lặp / idempotency
   - Hỗ trợ cả phương thức GET và POST
"""
import hashlib
import hmac
import urllib.parse
import uuid
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.db.database import db_service
from app.services.order_service import order_service
from app.services.vnpay_service import vnpay_service, get_vnpay_response_message
from tests.conftest import CUSTOMER, item

client = TestClient(app)

TEST_SECRET = "TEST_VNPAY_HASH_SECRET_KEY_FOR_TESTING_123456"
TEST_TMN = "TESTTMN1"


def make_vnpay_callback_params(
    order_id: str,
    amount: int,
    response_code: str = "00",
    transaction_status: str = "00",
    trans_no: str = "14888999",
    secret_key: str = TEST_SECRET,
    tmn_code: str = TEST_TMN,
    extra_params: dict = None,
):
    """Tạo bộ tham số phản hồi VNPay chuẩn mã hóa và chữ ký HMAC-SHA512 thực tế."""
    params = {
        "vnp_Amount": str(amount * 100),
        "vnp_BankCode": "NCB",
        "vnp_BankTranNo": "VNP14888999",
        "vnp_CardType": "ATM",
        "vnp_OrderInfo": f"Thanh toan don hang {order_id}",
        "vnp_PayDate": "20261019120000",
        "vnp_ResponseCode": response_code,
        "vnp_TmnCode": tmn_code,
        "vnp_TransactionNo": trans_no,
        "vnp_TransactionStatus": transaction_status,
        "vnp_TxnRef": order_id,
    }
    if extra_params:
        params.update(extra_params)

    input_data = sorted(params.items())
    query_string = "&".join(f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in input_data)
    secure_hash = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    params["vnp_SecureHash"] = secure_hash
    return params


def create_test_order(payment_method="vnpay"):
    """Tạo đơn hàng mới phục vụ test"""
    req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": payment_method,
    }
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200, r.text
    data = r.json()
    order_id = data["order_id"]
    total = int(data["quote"]["total"])
    return order_id, total


class TestVNPayIntegration:

    @pytest.fixture(autouse=True)
    def setup_vnpay_config(self):
        """Cấu hình tạm thời các khóa VNPay hợp lệ cho kiểm thử"""
        old_secret = settings.VNPAY_HASH_SECRET
        old_tmn = settings.VNPAY_TMN_CODE
        settings.VNPAY_HASH_SECRET = TEST_SECRET
        settings.VNPAY_TMN_CODE = TEST_TMN
        yield
        settings.VNPAY_HASH_SECRET = old_secret
        settings.VNPAY_TMN_CODE = old_tmn

    # =========================================================================
    # 1. Kiểm thử tạo URL thanh toán VNPay
    # =========================================================================
    def test_build_payment_url_parameters_and_checksum(self):
        """Tạo URL thanh toán có đủ tham số, vnp_Amount x100, GMT+7 date và HMAC-SHA512 đúng."""
        order_id, total = create_test_order("vnpay")

        url = vnpay_service.build_payment_url(
            order_id=order_id,
            amount=total,
            client_ip="192.168.1.100, 10.0.0.1",
            order_desc=f"Thanh toan {order_id}",
        )

        assert url.startswith(settings.VNPAY_URL)
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        # Tham số cơ bản
        assert params["vnp_Version"] == ["2.1.0"]
        assert params["vnp_Command"] == ["pay"]
        assert params["vnp_TmnCode"] == [TEST_TMN]
        assert params["vnp_Amount"] == [str(total * 100)]
        assert params["vnp_CurrCode"] == ["VND"]
        assert params["vnp_TxnRef"] == [order_id]
        assert params["vnp_ReturnUrl"] == [settings.VNPAY_RETURN_URL]

        # IP phải được chuẩn hóa (lấy IP đầu tiên, không chứa dấu phẩy)
        assert params["vnp_IpAddr"] == ["192.168.1.100"]

        # vnp_CreateDate và vnp_ExpireDate phải đúng 14 ký tự
        create_date = params["vnp_CreateDate"][0]
        expire_date = params["vnp_ExpireDate"][0]
        assert len(create_date) == 14
        assert len(expire_date) == 14
        assert expire_date > create_date

        # Chữ ký kiểm tra tính toán độc lập
        received_hash = params["vnp_SecureHash"][0]
        clean_params = {k: v[0] for k, v in params.items() if k != "vnp_SecureHash"}
        sorted_pairs = sorted(clean_params.items())
        expected_query = "&".join(f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in sorted_pairs)
        expected_hash = hmac.new(
            TEST_SECRET.encode("utf-8"),
            expected_query.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        assert received_hash.lower() == expected_hash.lower()

    def test_create_payment_url_rejects_paid_or_cancelled_order(self):
        """Từ chối tạo URL thanh toán nếu đơn hàng đã được thanh toán hoặc đã bị hủy."""
        order_id, total = create_test_order("vnpay")

        # 1. Hủy đơn hàng
        db_service.cancel_order_atomic(order_id)
        phone_num = CUSTOMER.get("customer_phone") or CUSTOMER.get("phone")
        res = client.post("/api/payment/vnpay/create-payment-url", json={"order_id": order_id, "phone": phone_num})
        assert res.status_code == 400
        assert "hủy" in res.json()["detail"].lower()

        # 2. Đơn đã thanh toán
        order_id2, total2 = create_test_order("vnpay")
        db_service.confirm_payment(order_id2, total2, f"TX_PAID_{order_id2}", "vnpay")
        res2 = client.post("/api/payment/vnpay/create-payment-url", json={"order_id": order_id2, "phone": phone_num})
        assert res2.status_code == 400
        assert "đã được thanh toán" in res2.json()["detail"].lower()

    # =========================================================================
    # 2. Kiểm thử xác thực chữ ký VNPay
    # =========================================================================
    def test_verify_signature_valid(self):
        """Xác thực chữ ký hợp lệ thành công."""
        params = make_vnpay_callback_params("ORD_123", 500000)
        is_valid, data = vnpay_service.verify_response(params)
        assert is_valid is True
        assert data["vnp_TxnRef"] == "ORD_123"

    def test_verify_signature_tampered_param(self):
        """Dữ liệu bị sửa đổi (ví dụ sửa số tiền) làm chữ ký không còn hợp lệ."""
        params = make_vnpay_callback_params("ORD_123", 500000)
        params["vnp_Amount"] = "100000"  # Kẻ gian sửa tiền
        is_valid, _ = vnpay_service.verify_response(params)
        assert is_valid is False

    def test_verify_signature_invalid_secret(self):
        """Ký bằng secret key khác thì không qua được xác thực."""
        params = make_vnpay_callback_params("ORD_123", 500000, secret_key="WRONG_SECRET_KEY")
        is_valid, _ = vnpay_service.verify_response(params)
        assert is_valid is False

    def test_verify_signature_empty_secret_fails(self):
        """Nếu cấu hình secret rỗng, hàm verify_response từ chối ngay lập tức."""
        old_env = settings.APP_ENV
        old_debug = settings.DEBUG
        settings.APP_ENV = "production"
        settings.DEBUG = False
        settings.VNPAY_HASH_SECRET = ""
        try:
            params = make_vnpay_callback_params("ORD_123", 500000, secret_key="")
            is_valid, _ = vnpay_service.verify_response(params)
            assert is_valid is False
        finally:
            settings.APP_ENV = old_env
            settings.DEBUG = old_debug
            settings.VNPAY_HASH_SECRET = TEST_SECRET

    # =========================================================================
    # 3. Kiểm thử luồng Return URL
    # =========================================================================
    def test_return_url_successful_payment(self):
        """Giao dịch VNPay thành công (ResponseCode=00, TransactionStatus=00) cập nhật đơn hàng thành paid và confirmed."""
        order_id, total = create_test_order("vnpay")
        trans_no = f"VNP_{uuid.uuid4().hex[:8]}"
        params = make_vnpay_callback_params(
            order_id=order_id,
            amount=total,
            response_code="00",
            transaction_status="00",
            trans_no=trans_no,
        )

        res = client.get("/api/payment/vnpay/return", params=params, follow_redirects=False)
        # Trình duyệt nhận HTTP 302 chuyển hướng về trang thành công
        assert res.status_code == 302
        assert f"/order-success/{order_id}" in res.headers["location"]

        # Kiểm tra database
        order = db_service.get_order_by_id(order_id)
        assert order["payment_status"] == "paid"
        assert order["order_status"] == "confirmed"

    def test_return_url_customer_cancelled(self):
        """Khách hàng bấm hủy giao dịch trên VNPay (ResponseCode=24) không bị coi là thành công."""
        order_id, total = create_test_order("vnpay")
        params = make_vnpay_callback_params(
            order_id=order_id,
            amount=total,
            response_code="24",
            transaction_status="02",
        )

        # Gọi API với accept HTML (trình duyệt)
        res = client.get("/api/payment/vnpay/return", params=params, headers={"accept": "text/html"})
        assert res.status_code == 200
        assert "Giao dịch đã bị hủy" in res.text or "hủy giao dịch" in res.text.lower()
        assert f"/order-success/{order_id}" in res.text  # Có link thanh toán lại

        # Kiểm tra database không bị đổi thành paid
        order = db_service.get_order_by_id(order_id)
        assert order["payment_status"] != "paid"

    def test_return_url_amount_mismatch(self):
        """Số tiền trong callback khác với giá trị đơn hàng -> Từ chối 400."""
        order_id, total = create_test_order("vnpay")
        # Gửi số tiền sai nhưng chữ ký vẫn được ký đúng theo số tiền sai
        params = make_vnpay_callback_params(
            order_id=order_id,
            amount=total + 50000,
            response_code="00",
            transaction_status="00",
        )
        res = client.get("/api/payment/vnpay/return", params=params, headers={"accept": "application/json"})
        assert res.status_code == 400
        assert "không khớp" in res.json()["detail"].lower()

    def test_return_url_payment_arrived_after_order_cancelled(self):
        """Khách đã hủy đơn nhưng tiền vẫn trừ thành công qua VNPay -> Chuyển refund_pending tự động."""
        order_id, total = create_test_order("vnpay")
        db_service.cancel_order_atomic(order_id)

        params = make_vnpay_callback_params(
            order_id=order_id,
            amount=total,
            response_code="00",
            transaction_status="00",
            trans_no=f"VNP_LATE_{order_id}",
        )
        res = client.get("/api/payment/vnpay/return", params=params, headers={"accept": "application/json"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "refund_pending"

        order = db_service.get_order_by_id(order_id)
        assert order["payment_status"] == "refund_pending"

    # =========================================================================
    # 4. Kiểm thử luồng IPN Webhook
    # =========================================================================
    def test_ipn_invalid_signature(self):
        """IPN có chữ ký sai trả về RspCode 97."""
        params = make_vnpay_callback_params("ORD_999", 100000, secret_key="WRONG_KEY")
        res = client.get("/api/payment/vnpay/ipn", params=params)
        assert res.status_code == 200
        assert res.json() == {"RspCode": "97", "Message": "Invalid signature"}

    def test_ipn_order_not_found(self):
        """IPN cho đơn hàng không tồn tại trả về RspCode 01."""
        params = make_vnpay_callback_params("ORD_NON_EXISTENT_XYZ", 100000)
        res = client.get("/api/payment/vnpay/ipn", params=params)
        assert res.status_code == 200
        assert res.json() == {"RspCode": "01", "Message": "Order not found"}

    def test_ipn_invalid_amount(self):
        """IPN sai số tiền trả về RspCode 04."""
        order_id, total = create_test_order("vnpay")
        params = make_vnpay_callback_params(order_id=order_id, amount=total + 99000)
        res = client.get("/api/payment/vnpay/ipn", params=params)
        assert res.status_code == 200
        assert res.json() == {"RspCode": "04", "Message": "Invalid amount"}

    def test_ipn_success_and_idempotent_duplicate_call(self):
        """IPN xác nhận thành công (00). Gửi IPN lần 2 (lặp lại) trả về RspCode 02 (đã xác nhận) an toàn."""
        order_id, total = create_test_order("vnpay")
        trans_no = f"VNP_IPN_{uuid.uuid4().hex[:8]}"
        params = make_vnpay_callback_params(
            order_id=order_id,
            amount=total,
            response_code="00",
            transaction_status="00",
            trans_no=trans_no,
        )

        # Lần 1: Thành công
        res1 = client.get("/api/payment/vnpay/ipn", params=params)
        assert res1.status_code == 200
        assert res1.json() == {"RspCode": "00", "Message": "Confirm Success"}

        order = db_service.get_order_by_id(order_id)
        assert order["payment_status"] == "paid"

        # Lần 2: Callback lặp lại -> Phải trả về RspCode 02 (Order already confirmed), không bị lỗi
        res2 = client.get("/api/payment/vnpay/ipn", params=params)
        assert res2.status_code == 200
        assert res2.json() == {"RspCode": "02", "Message": "Order already confirmed"}

    def test_ipn_post_method_supported(self):
        """IPN hỗ trợ phương thức POST form-data chuẩn."""
        order_id, total = create_test_order("vnpay")
        trans_no = f"VNP_POST_{uuid.uuid4().hex[:8]}"
        params = make_vnpay_callback_params(
            order_id=order_id,
            amount=total,
            response_code="00",
            transaction_status="00",
            trans_no=trans_no,
        )
        res = client.post("/api/payment/vnpay/ipn", data=params)
        assert res.status_code == 200
        assert res.json() == {"RspCode": "00", "Message": "Confirm Success"}

    def test_ipn_payment_failed_acknowledged(self):
        """IPN gửi thông báo giao dịch thất bại (ví dụ khách hủy) được ghi nhận và trả về RspCode 00."""
        order_id, total = create_test_order("vnpay")
        params = make_vnpay_callback_params(
            order_id=order_id,
            amount=total,
            response_code="24",
            transaction_status="02",
        )
        res = client.get("/api/payment/vnpay/ipn", params=params)
        assert res.status_code == 200
        assert res.json()["RspCode"] == "00"

        # Đơn hàng vẫn giữ trạng thái chưa thanh toán
        order = db_service.get_order_by_id(order_id)
        assert order["payment_status"] != "paid"
