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
