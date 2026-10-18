import os
import pytest
from starlette.testclient import TestClient

from app.db.database import db_service
from app.routers.deps import _voucher_validate_reqs, VOUCHER_VALIDATE_MAX_REQ
from app.services.order_service import order_service
from tests.conftest import CUSTOMER, item


def test_admin_layout_show_toast_escapes_html():
    """1. Kiểm tra layout admin và dashboard đã escape message trong showToast và template literals."""
    layout_path = os.path.join("app", "templates", "admin", "layout.html")
    with open(layout_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Khẳng định showToast sử dụng window.esc(message)
    assert "<span>${window.esc(message)}</span>" in content
    assert "<span>${message}</span>" not in content

    # Kiểm tra dashboard.html cũng escape biến
    dashboard_path = os.path.join("app", "templates", "admin", "dashboard.html")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        dash_content = f.read()
    assert "${esc(o.order_id)}" in dash_content
    assert "${esc(o.customer?.name" in dash_content


def test_payment_check_status_requires_phone_for_guest_orders(client: TestClient):
    """2. /api/payment/check-status bắt buộc khớp SĐT đối với đơn vãng lai."""
    guest_phone = "0988665544"
    req = {
        **CUSTOMER,
        "customer_name": "Khách Vãng Lai Check Status",
        "customer_phone": guest_phone,
        "payment_method": "qr_transfer",
        "items": [item("prod_001", qty=1)],
    }
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]

    # Tạo client độc lập mới không có cookie
    from app.main import app
    unauth_client = TestClient(app)

    # Không truyền phone -> 404
    r_no_phone = unauth_client.get(f"/api/payment/check-status/{order_id}")
    assert r_no_phone.status_code == 404

    # Truyền phone sai -> 404
    r_wrong_phone = unauth_client.get(f"/api/payment/check-status/{order_id}?phone=0911223344")
    assert r_wrong_phone.status_code == 404

    # Truyền đúng phone (thử nghiệm cả định dạng có dấu cách và +84) -> 200
    r_correct_phone = unauth_client.get(f"/api/payment/check-status/{order_id}?phone=+84 988 665 544")
    assert r_correct_phone.status_code == 200
    assert r_correct_phone.json()["order_id"] == order_id

    # Admin đăng nhập có quyền xem mà không cần phone
    admin_res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_token = admin_res.json()["token"]
    r_admin = unauth_client.get(
        f"/api/payment/check-status/{order_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r_admin.status_code == 200


def test_vnpay_create_payment_url_checks_ownership(client: TestClient):
    """3. /api/payment/vnpay/create-payment-url kiểm tra quyền sở hữu đơn hàng."""
    # 3.1 Đơn hàng của User đã đăng nhập
    user_a_res = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert user_a_res.status_code == 200
    token_a = user_a_res.json()["token"]

    req_a = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)],
    }
    # Tạo đơn với danh nghĩa User A (usr_002)
    order_a = client.post(
        "/api/orders",
        json=req_a,
        headers={"Authorization": f"Bearer {token_a}"}
    ).json()
    order_id_a = order_a["order_id"]

    # Kẻ tấn công không đăng nhập thử tạo payment URL cho đơn của User A -> 403
    from app.main import app
    attacker_client = TestClient(app)
    r_hack_anon = attacker_client.post(
        "/api/payment/vnpay/create-payment-url",
        json={"order_id": order_id_a, "bank_code": "NCB"}
    )
    assert r_hack_anon.status_code == 403
    assert "quyền" in r_hack_anon.json()["detail"].lower()

    # User A gọi cho đơn của chính mình -> 200
    r_user_a = client.post(
        "/api/payment/vnpay/create-payment-url",
        json={"order_id": order_id_a, "bank_code": "NCB"},
        headers={"Authorization": f"Bearer {token_a}"}
    )
    assert r_user_a.status_code == 200
    assert "payment_url" in r_user_a.json()

    # 3.2 Đơn hàng vãng lai: bắt buộc khớp SĐT hoặc cookie
    guest_phone = "0977112233"
    guest_req = {
        **CUSTOMER,
        "customer_phone": guest_phone,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)],
    }
    guest_order = attacker_client.post("/api/orders", json=guest_req).json()
    guest_order_id = guest_order["order_id"]

    # Một client khác không có cookie, không truyền phone -> 403
    third_party_client = TestClient(app)
    r_guest_no_auth = third_party_client.post(
        "/api/payment/vnpay/create-payment-url",
        json={"order_id": guest_order_id, "bank_code": "NCB"}
    )
    assert r_guest_no_auth.status_code == 403

    # Truyền phone sai -> 403
    r_guest_wrong_phone = third_party_client.post(
        "/api/payment/vnpay/create-payment-url",
        json={"order_id": guest_order_id, "bank_code": "NCB", "phone": "0900000000"}
    )
    assert r_guest_wrong_phone.status_code == 403

    # Truyền đúng phone -> 200
    r_guest_correct_phone = third_party_client.post(
        "/api/payment/vnpay/create-payment-url",
        json={"order_id": guest_order_id, "bank_code": "NCB", "phone": guest_phone}
    )
    assert r_guest_correct_phone.status_code == 200
    assert "payment_url" in r_guest_correct_phone.json()


def test_vouchers_validate_rate_limit(client: TestClient):
    """4. Rate-limit /api/vouchers/validate: chặn 429 khi gọi vượt quá ngưỡng cho phép."""
    _voucher_validate_reqs.clear()

    # Gửi VOUCHER_VALIDATE_MAX_REQ request hợp lệ liên tiếp
    for i in range(VOUCHER_VALIDATE_MAX_REQ):
        r = client.post("/api/vouchers/validate", json={"code": "FREESHIP", "subtotal": 500000})
        assert r.status_code == 200, f"Request {i+1} failed with status {r.status_code}"

    # Request thứ (VOUCHER_VALIDATE_MAX_REQ + 1) phải bị rate limit 429
    r_blocked = client.post("/api/vouchers/validate", json={"code": "FREESHIP", "subtotal": 500000})
    assert r_blocked.status_code == 429
    assert "quá nhiều lần" in r_blocked.json()["detail"].lower()

    # Dọn dẹp sau test
    _voucher_validate_reqs.clear()
