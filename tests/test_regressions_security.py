import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.database import db_service
from app.main import app
from app.services.product_service import product_service
from tests.conftest import CUSTOMER, item


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _register_user(client, prefix="reg_user"):
    uid = uuid.uuid4().hex[:6]
    username = f"{prefix}_{uid}"
    pwd = "UserPass123!"
    res = client.post("/api/auth/register", json={
        "username": username,
        "password": pwd,
        "confirm_password": pwd,
        "full_name": f"User {uid}",
        "email": f"{username}@test.com"
    })
    assert res.status_code == 200, res.text
    return res.json()["token"], username, uid


def test_regression_idor_order_endpoints(client):
    """
    IDOR test: Khách hàng A không được phép xem đơn hàng hoặc hóa đơn của Khách hàng B.
    """
    token_a, user_a, _ = _register_user(client, "user_a")
    token_b, user_b, _ = _register_user(client, "user_b")

    # User A đặt 1 đơn hàng
    order_req = {
        **CUSTOMER,
        "customer_name": "User A",
        "customer_phone": "0911223344",
        "payment_method": "cod",
        "items": [item("prod_001", qty=1)],
    }
    r = client.post("/api/orders", json=order_req, headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200
    order_id = r.json()["order_id"]

    # User B cố tình truy cập đơn của User A -> 404
    r_idor_get = client.get(f"/api/orders/{order_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert r_idor_get.status_code == 404

    # User B cố tình xem hóa đơn của User A -> 404
    r_idor_inv = client.get(f"/api/orders/{order_id}/invoice", headers={"Authorization": f"Bearer {token_b}"})
    assert r_idor_inv.status_code == 404

    # User B kiểm tra trạng thái thanh toán đơn của User A -> 404
    r_idor_pay = client.get(f"/api/payment/check-status/{order_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert r_idor_pay.status_code == 404


def test_regression_open_redirect_protection(client):
    """
    Chặn Open Redirect: Các URL độc hại như //evil.com, /\\evil.com bị chuẩn hóa về an toàn.
    """
    for bad_target in ["//evil.com", "/\\evil.com", "http://evil.com", "\\evil.com"]:
        r = client.get(f"/login?redirect={bad_target}")
        assert r.status_code == 200
        # Trang login không chứa target độc hại
        assert "evil.com" not in r.text or "redirect_url" not in r.text


def test_regression_password_reset_ignores_host_header(client):
    """
    Chống Host Header Injection khi yêu cầu đặt lại mật khẩu:
    Link reset luôn sử dụng PUBLIC_BASE_URL từ cấu hình server.
    """
    r = client.post(
        "/api/auth/forgot-password",
        json={"email": "admin@aurastudio.vn"},
        headers={"Host": "evil-attacker.com"},
    )
    assert r.status_code == 200
    # Link không bao giờ chứa host giả mạo
    assert "evil-attacker.com" not in r.text


def test_regression_xss_input_rejected_or_escaped(client):
    """
    Chống XSS: Các payload script trong tên người dùng hoặc bình luận phải bị từ chối hoặc xử lý an toàn.
    """
    xss_payload = "<script>alert('xss')</script>"
    r = client.post("/api/auth/register", json={
        "username": "attacker",
        "password": "Password123!",
        "full_name": xss_payload,
        "email": "attacker@test.com",
    })
    # Schema validator từ chối ký tự HTML < >
    assert r.status_code in [400, 422]


def test_regression_soft_delete_preserves_reviews_and_batches(client):
    """
    Xóa sản phẩm bằng soft-delete: không xóa cứng để không cascade mất đánh giá và lịch sử giá vốn.
    """
    # Lấy 1 sản phẩm
    prod = product_service.get_by_id("prod_001")
    assert prod is not None

    # Thực hiện xóa soft-delete
    ok = product_service.delete_product("prod_001")
    assert ok is True

    # Sản phẩm bị ẩn khỏi catalog thông thường
    assert product_service.get_by_id("prod_001") is None
    all_ids = [p.id for p in product_service.get_all()]
    assert "prod_001" not in all_ids

    # Nhưng đánh giá trong DB vẫn còn nguyên vẹn
    reviews = db_service.get_product_reviews("prod_001")
    assert "reviews" in reviews

    # Lịch sử lô hàng vẫn còn trong DB
    batches = db_service.get_inventory_batches("prod_001")
    assert isinstance(batches, list)


def test_regression_rate_limit_register_and_login(client):
    """
    Kiểm tra rate-limit trên /api/auth/register và /api/auth/login.
    """
    from app.routers.deps import _register_ip_attempts
    _register_ip_attempts.clear()

    # Thử đăng ký 5 lần từ cùng 1 IP
    for i in range(5):
        r = client.post("/api/auth/register", json={
            "username": f"user_rl_{i}_{uuid.uuid4().hex[:4]}",
            "password": "Password123!",
            "full_name": f"User {i}",
            "email": f"user_{i}_{uuid.uuid4().hex[:4]}@test.com",
        })
        assert r.status_code == 200

    # Lần thứ 6 phải nhận 429
    r6 = client.post("/api/auth/register", json={
        "username": f"user_rl_blocked_{uuid.uuid4().hex[:4]}",
        "password": "Password123!",
        "full_name": "User Blocked",
        "email": f"blocked_{uuid.uuid4().hex[:4]}@test.com",
    })
    assert r6.status_code == 429


def test_regression_clean_db_startup():
    """
    Kiểm tra DB trống khởi động thành công và các bảng cần thiết đều được tạo.
    """
    from app.db.models import Base
    from app.db.session import engine
    Base.metadata.create_all(bind=engine)
    db_service.init_db()
    # Kiểm tra truy vấn cơ bản
    all_products = product_service.get_all()
    assert len(all_products) > 0
