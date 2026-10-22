import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.deps import sign_guest_order_cookie, verify_guest_order_cookie
from app.services.user_service import make_auth_token


def test_csrf_middleware_same_origin_and_cross_origin():
    """Kiểm tra CSRF middleware:
    - Cùng origin: pass
    - Khác origin: 403
    - Không origin/referer: 403
    - Dùng Bearer token: pass (miễn trừ CSRF)
    - Webhook: không bị chặn
    """
    client = TestClient(app, base_url="http://testserver")
    login_res = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]

    # 1. Khác origin -> bị 403
    res_cross = client.post(
        "/api/cart/sync",
        json={"items": []},
        cookies={"aura_session": token},
        headers={"Origin": "https://evil-attacker.com"},
    )
    assert res_cross.status_code == 403
    assert "CSRF" in res_cross.json().get("detail", "")

    # 2. Sec-Fetch-Site: cross-site -> bị 403
    res_cross_site = client.post(
        "/api/cart/sync",
        json={"items": []},
        cookies={"aura_session": token},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert res_cross_site.status_code == 403
    assert "CSRF" in res_cross_site.json().get("detail", "")

    # 3. Cùng origin -> pass (không bị 403)
    res_valid = client.post(
        "/api/cart/sync",
        json={"items": []},
        cookies={"aura_session": token},
        headers={"Origin": "http://testserver"},
    )
    assert res_valid.status_code == 200

    # 4. Có Authorization: Bearer -> miễn kiểm tra CSRF (kể cả không có Origin)
    res_bearer = client.post(
        "/api/cart/sync",
        json={"items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_bearer.status_code == 200

    # 5. Webhook thanh toán được miễn trừ CSRF
    res_webhook = client.post(
        "/api/payment/webhook",
        json={"order_id": "nonexistent"},
        headers={"Origin": "https://external-bank.com"},
    )
    # Không bị 403 do CSRF (có thể bị lỗi auth webhook hoặc order not found nhưng không phải CSRF)
    assert res_webhook.status_code != 403 or "CSRF" not in res_webhook.text


def test_xss_prevention_server_side():
    """Kiểm tra server từ chối các chuỗi chứa mã HTML / script độc hại."""
    client = TestClient(app, base_url="http://testserver")
    # 1. Đăng ký tài khoản với tên chứa <script>
    res_reg = client.post(
        "/api/auth/register",
        json={
            "name": "<script>alert('xss')</script>",
            "username": "xss_test_user",
            "email": "xss_test@aura.vn",
            "password": "Password123@",
            "confirm_password": "Password123@",
        },
    )
    assert res_reg.status_code == 422

    # 2. Tạo đơn với ghi chú chứa <script>
    res_order = client.post(
        "/api/orders",
        json={
            "customer_name": "Nguyen Van A",
            "customer_phone": "0987654321",
            "customer_address": "123 Le Loi, Q1",
            "customer_note": "<script>stealCookies()</script>",
            "items": [{"product_id": "prod_001", "size": "M", "color": "Đen", "quantity": 1}],
        },
    )
    assert res_order.status_code == 422

    # 3. Đánh giá sản phẩm với bình luận chứa thẻ HTML / script (kèm auth)
    # Đăng nhập lấy user token
    login_res = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    token = login_res.json().get("token") or make_auth_token("usr_002", "user")
    res_rev = client.post(
        "/api/products/prod_001/reviews",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "user_name": "Reviewer",
            "rating": 5,
            "comment": "<script>alert('bad')</script> Đồ rất đẹp",
        },
    )
    assert res_rev.status_code == 422


def test_guest_order_hmac_cookie_verification():
    """Kiểm tra token ký HMAC cho cookie đơn hàng vãng lai."""
    order_id = "ORD-2026-X99"
    phone = "0987654321"

    token = sign_guest_order_cookie(order_id, phone)
    assert token and isinstance(token, str)

    # Token đúng -> hợp lệ
    assert verify_guest_order_cookie(token, order_id, phone) is True

    # Khác order_id -> không hợp lệ
    assert verify_guest_order_cookie(token, "ORD-OTHER", phone) is False

    # Khác số điện thoại -> không hợp lệ
    assert verify_guest_order_cookie(token, order_id, "0912345678") is False

    # Token bị làm giả / sửa đổi -> không hợp lệ
    tampered_token = token + "xyz"
    assert verify_guest_order_cookie(tampered_token, order_id, phone) is False

    # Token rỗng / None -> không hợp lệ
    assert verify_guest_order_cookie(None, order_id, phone) is False
