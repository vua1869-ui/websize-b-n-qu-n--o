import hashlib
import hmac
import json
import pytest
from app.config import settings
from tests.conftest import CUSTOMER, item


def _login_token(client, username="user", password="user123"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["token"]


def test_payment_webhook_hmac_verification(client):
    """Test webhook xác thực HMAC-SHA256: thiếu chữ ký, sai chữ ký, và đúng chữ ký."""
    # 1. Tạo đơn hàng mới để test
    order_req = {
        **CUSTOMER,
        "payment_method": "qr_transfer",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    payload = {
        "content": f"AURA {order_id}",
        "transferAmount": total,
        "referenceCode": f"FT{order_id.replace('-', '')}",
    }
    raw_body = json.dumps(payload).encode("utf-8")

    # 1. Thiếu header X-Signature -> 401
    r_no_sig = client.post(
        "/api/payment/webhook",
        content=raw_body,
        headers={"Content-Type": "application/json"}
    )
    assert r_no_sig.status_code == 401
    assert "chữ ký" in r_no_sig.json()["detail"].lower()

    # 2. Chữ ký sai -> 401
    r_wrong_sig = client.post(
        "/api/payment/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": "invalid_hex_signature_here"
        }
    )
    assert r_wrong_sig.status_code == 401
    assert "chữ ký" in r_wrong_sig.json()["detail"].lower()

    # 3. Chữ ký đúng -> 200
    secret = settings.PAYMENT_WEBHOOK_SECRET
    valid_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    r_valid = client.post(
        "/api/payment/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": valid_sig
        }
    )
    assert r_valid.status_code == 200
    assert r_valid.json()["success"] is True
    assert r_valid.json()["order_id"] == order_id

    # Đơn hàng đã được xác nhận thanh toán
    status_r = client.get(f"/api/payment/check-status/{order_id}")
    assert status_r.status_code == 200
    assert status_r.json()["status"] == "paid"


def test_simulate_success_access_control_and_debug_mode(client):
    """Test simulate-success: bị chặn khi không phải admin và khi DEBUG=False."""
    # Tạo đơn hàng
    order_req = {
        **CUSTOMER,
        "payment_method": "qr_transfer",
        "items": [item("prod_001", qty=1)]
    }
    order_id = client.post("/api/orders", json=order_req).json()["order_id"]

    # 1. Khách vãng lai không đăng nhập -> 401
    r_unauth = client.post(f"/api/payment/simulate-success/{order_id}")
    assert r_unauth.status_code == 401

    # 2. Đăng nhập user thường (role="user") -> 403
    user_token = _login_token(client, "user", "user123")
    r_user = client.post(
        f"/api/payment/simulate-success/{order_id}",
        headers={"Authorization": f"Bearer {user_token}"}
    )
    assert r_user.status_code == 403

    # 3. Đăng nhập admin (role="admin") khi DEBUG=True -> 200
    orig_debug = settings.DEBUG
    try:
        settings.DEBUG = True
        admin_token = _login_token(client, "admin", "admin123")
        r_admin = client.post(
            f"/api/payment/simulate-success/{order_id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert r_admin.status_code == 200
        assert r_admin.json()["success"] is True
        assert r_admin.json()["payment_status"] == "paid"

        # 4. Khi DEBUG=False -> 404 (kể cả admin)
        settings.DEBUG = False
        r_prod = client.post(
            f"/api/payment/simulate-success/{order_id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert r_prod.status_code == 404
    finally:
        settings.DEBUG = orig_debug


def test_session_cookie_httponly_and_secure(client):
    """Test cookie aura_session có httponly=True và cờ secure theo DEBUG."""
    orig_debug = settings.DEBUG
    try:
        # Trường hợp 1: DEBUG = True -> httponly=True, secure=False
        settings.DEBUG = True
        res_login_dev = client.post(
            "/api/auth/login",
            json={"username": "user", "password": "user123"}
        )
        assert res_login_dev.status_code == 200
        cookie_header_dev = res_login_dev.headers.get("set-cookie", "").lower()
        assert "httponly" in cookie_header_dev
        assert "secure" not in cookie_header_dev

        # Trường hợp 2: DEBUG = False -> httponly=True, secure=True
        settings.DEBUG = False
        res_login_prod = client.post(
            "/api/auth/login",
            json={"username": "user", "password": "user123"}
        )
        assert res_login_prod.status_code == 200
        cookie_header_prod = res_login_prod.headers.get("set-cookie", "").lower()
        assert "httponly" in cookie_header_prod
        assert "secure" in cookie_header_prod
    finally:
        settings.DEBUG = orig_debug
