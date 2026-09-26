"""
Test Suite cho Cổng thanh toán VNPay Sandbox (Phase 3):
1. Tạo payment URL với query parameters chuẩn VNPay (vnp_TxnRef, vnp_Amount=amount*100, vnp_SecureHash, sorted keys).
2. Xử lý Return URL với chữ ký hợp lệ (response code 00) -> cập nhật trạng thái đơn confirmed/paid.
3. Xử lý Return URL với chữ ký SAI -> trả 400, không cập nhật đơn.
4. Xử lý Return URL với response code thất bại (!= 00) -> giữ trạng thái unpaid/pending_payment.
5. Xử lý IPN với chữ ký hợp lệ -> trả RspCode 00 và cập nhật đơn hàng.
6. Xử lý IPN với chữ ký sai -> trả RspCode 97.
7. Xử lý IPN đơn đã thanh toán -> trả RspCode 02.
8. Xử lý IPN sai số tiền -> trả RspCode 04.
"""
import hashlib
import hmac
import urllib.parse
import pytest
from app.config import settings
from tests.conftest import CUSTOMER, item


def _generate_vnpay_params(order_id: str, amount: int, response_code: str = "00", **overrides):
    """Tạo bộ tham số phản hồi VNPay có chữ ký HMAC-SHA512 hợp lệ."""
    params = {
        "vnp_Amount": str(int(amount) * 100),
        "vnp_BankCode": "NCB",
        "vnp_BankTranNo": "VNP14043749",
        "vnp_CardType": "ATM",
        "vnp_OrderInfo": f"Thanh toan don hang {order_id}",
        "vnp_PayDate": "20260926190000",
        "vnp_ResponseCode": response_code,
        "vnp_TmnCode": settings.VNPAY_TMN_CODE,
        "vnp_TransactionNo": "14043749",
        "vnp_TransactionStatus": "00" if response_code == "00" else "02",
        "vnp_TxnRef": order_id,
    }
    params.update(overrides)

    sorted_params = sorted(params.items())
    hash_data = urllib.parse.urlencode(sorted_params)
    secure_hash = hmac.new(
        settings.VNPAY_HASH_SECRET.encode("utf-8"),
        hash_data.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    params["vnp_SecureHash"] = secure_hash
    return params


def test_vnpay_create_payment_url_success(client):
    """Test tạo payment URL thành công với định dạng tham số và chữ ký chuẩn VNPay."""
    # 1. Tạo đơn hàng VNPay
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=2)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_data = r.json()
    order_id = order_data["order_id"]
    total_amount = order_data["quote"]["total"]

    # 2. Gọi API tạo payment URL
    res = client.post(
        "/api/payment/vnpay/create-payment-url",
        json={"order_id": order_id, "bank_code": "NCB"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["order_id"] == order_id
    payment_url = data["payment_url"]

    # 3. Phân tích URL và query params
    parsed = urllib.parse.urlparse(payment_url)
    assert parsed.scheme == "https"
    assert "sandbox.vnpayment.vn" in parsed.netloc or "vnpayment.vn" in parsed.netloc

    q_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    # parse_qs trả về list cho mỗi key
    flat_params = {k: v[0] for k, v in q_params.items()}

    assert flat_params.get("vnp_Version") == "2.1.0"
    assert flat_params.get("vnp_Command") == "pay"
    assert flat_params.get("vnp_TmnCode") == settings.VNPAY_TMN_CODE
    assert flat_params.get("vnp_TxnRef") == order_id
    assert flat_params.get("vnp_BankCode") == "NCB"
    assert flat_params.get("vnp_Amount") == str(int(total_amount) * 100)
    assert "vnp_SecureHash" in flat_params

    # 4. Xác thực chữ ký HMAC-SHA512 của payment URL
    received_hash = flat_params["vnp_SecureHash"]
    vnp_to_hash = {k: v for k, v in flat_params.items() if k.startswith("vnp_") and k not in ["vnp_SecureHash", "vnp_SecureHashType"]}
    sorted_items = sorted(vnp_to_hash.items())
    expected_hash = hmac.new(
        settings.VNPAY_HASH_SECRET.encode("utf-8"),
        urllib.parse.urlencode(sorted_items).encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    assert received_hash.lower() == expected_hash.lower()


def test_vnpay_create_payment_url_nonexistent_order(client):
    """Test tạo payment URL với đơn hàng không tồn tại -> trả 404."""
    res = client.post(
        "/api/payment/vnpay/create-payment-url",
        json={"order_id": "AURA-NONEXISTENT-ORDER"}
    )
    assert res.status_code == 404


def test_vnpay_return_url_valid_signature_success(client):
    """Test Return URL với chữ ký hợp lệ và code 00 -> đơn hàng cập nhật confirmed & paid."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")

    # Gọi Return URL
    res = client.get(
        "/api/payment/vnpay/return",
        params=params,
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["order_id"] == order_id
    assert data["payment_status"] == "paid"
    assert data["order_status"] == "confirmed"

    # Kiểm tra trạng thái đơn qua check-status endpoint
    check_r = client.get(f"/api/payment/check-status/{order_id}")
    assert check_r.status_code == 200
    assert check_r.json()["status"] == "paid"


def test_vnpay_return_url_invalid_signature(client):
    """Test Return URL với chữ ký SAI -> trả 400 và không cập nhật đơn hàng."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")
    # Giả mạo chữ ký
    params["vnp_SecureHash"] = "tampered_fake_signature_abc123"

    res = client.get(
        "/api/payment/vnpay/return",
        params=params,
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 400

    # Đơn hàng vẫn chưa thanh toán
    check_r = client.get(f"/api/payment/check-status/{order_id}")
    assert check_r.status_code == 200
    assert check_r.json()["status"] == "unpaid"


def test_vnpay_return_url_failed_code(client):
    """Test Return URL với code thất bại (ví dụ: khách hủy giao dịch code 24) -> giữ unpaid."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # Mã phản hồi 24: Khách hàng hủy giao dịch
    params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="24")

    res = client.get(
        "/api/payment/vnpay/return",
        params=params,
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["payment_status"] == "unpaid"

    # Trạng thái đơn vẫn là unpaid
    check_r = client.get(f"/api/payment/check-status/{order_id}")
    assert check_r.status_code == 200
    assert check_r.json()["status"] == "unpaid"


def test_vnpay_ipn_valid_signature_success(client):
    """Test IPN endpoint với chữ ký đúng -> trả JSON chuẩn VNPay (RspCode 00) và cập nhật đơn."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")

    # VNPay có thể gọi IPN qua GET hoặc POST query/form
    res = client.get("/api/payment/vnpay/ipn", params=params)
    assert res.status_code == 200
    data = res.json()
    assert data["RspCode"] == "00"
    assert data["Message"] == "Confirm Success"

    # Đơn hàng được cập nhật thành công
    check_r = client.get(f"/api/payment/check-status/{order_id}")
    assert check_r.status_code == 200
    assert check_r.json()["status"] == "paid"


def test_vnpay_ipn_invalid_signature(client):
    """Test IPN endpoint với chữ ký sai -> trả RspCode 97 (Invalid signature)."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")
    params["vnp_SecureHash"] = "invalid_hash_signature"

    res = client.post("/api/payment/vnpay/ipn", params=params)
    assert res.status_code == 200
    data = res.json()
    assert data["RspCode"] == "97"
    assert "signature" in data["Message"].lower()


def test_vnpay_ipn_order_already_confirmed(client):
    """Test IPN endpoint khi đơn đã được xác nhận thanh toán trước đó -> trả RspCode 02."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")

    # Lần 1: Xác nhận thành công
    res1 = client.get("/api/payment/vnpay/ipn", params=params)
    assert res1.status_code == 200
    assert res1.json()["RspCode"] == "00"

    # Lần 2: Đơn đã thanh toán rồi -> RspCode 02
    res2 = client.get("/api/payment/vnpay/ipn", params=params)
    assert res2.status_code == 200
    assert res2.json()["RspCode"] == "02"
    assert "already" in res2.json()["Message"].lower()


def test_vnpay_ipn_invalid_amount(client):
    """Test IPN endpoint với số tiền không khớp với đơn hàng -> trả RspCode 04."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # Sai số tiền: chênh lệch 10.000đ
    params = _generate_vnpay_params(order_id=order_id, amount=total + 10000, response_code="00")

    res = client.get("/api/payment/vnpay/ipn", params=params)
    assert res.status_code == 200
    assert res.json()["RspCode"] == "04"
    assert "amount" in res.json()["Message"].lower()
