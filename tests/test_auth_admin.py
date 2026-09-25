"""
Kiểm thử toàn diện hệ thống Authentication và Admin Dashboard:
- Đăng ký, Đăng nhập, Đăng xuất, Đổi mật khẩu, Cập nhật Profile.
- Phân quyền RBAC (Role-Based Access Control): User vs Admin.
- Chặn người dùng thường vào /admin (403 Forbidden).
- Chuyển hướng khi chưa đăng nhập.
- Admin APIs: stats, CRUD sản phẩm, cập nhật trạng thái đơn hàng, quản lý người dùng, quét xu hướng.
- Bảo vệ tài khoản admin mặc định.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.user_service import user_service
from app.services.order_service import order_service
from app.services.product_service import product_service

client = TestClient(app)


def test_public_pages_accessible():
    """Các trang công khai: Trang chủ, Login, Register, 403 luôn truy cập được."""
    res_home = client.get("/")
    assert res_home.status_code == 200

    res_login = client.get("/login")
    assert res_login.status_code == 200
    assert "AURA Studio" in res_login.text or "Welcome Back" in res_login.text

    res_register = client.get("/register")
    assert res_register.status_code == 200
    assert "Tạo tài khoản mới" in res_register.text

    res_403 = client.get("/403")
    assert res_403.status_code == 403
    assert "Quyền truy cập bị từ chối" in res_403.text


def test_auth_login_invalid_password():
    """Đăng nhập sai mật khẩu -> 401."""
    res = client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
    assert res.status_code == 401
    assert "Tên đăng nhập hoặc mật khẩu" in res.json()["detail"]


def test_auth_login_user_and_access_control():
    """User thường đăng nhập thành công nhưng không được vào /admin (403)."""
    # 1. Đăng nhập user
    res = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert data["user"]["role"] == "user"
    user_token = data["token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # 2. Truy cập /api/auth/me thành công
    res_me = client.get("/api/auth/me", headers=user_headers)
    assert res_me.status_code == 200
    assert res_me.json()["username"] == "user"

    # 3. User thường cố tình vào /admin web page -> nhận mã 403
    res_admin_page = client.get("/admin", headers=user_headers, cookies={"aura_session": user_token})
    assert res_admin_page.status_code == 403
    assert "Quyền truy cập bị từ chối" in res_admin_page.text

    # 4. User thường cố gọi /api/admin/stats -> 403
    res_admin_api = client.get("/api/admin/stats", headers=user_headers)
    assert res_admin_api.status_code == 403


def test_unauthenticated_redirect():
    """Khách chưa đăng nhập vào /admin -> chuyển hướng về /login?redirect=/admin."""
    c = TestClient(app)
    res = c.get("/admin", follow_redirects=False)
    assert res.status_code == 302
    assert "/login?redirect=/admin" in res.headers["location"]

    res_profile = c.get("/profile", follow_redirects=False)
    assert res_profile.status_code == 302
    assert "/login?redirect=/profile" in res_profile.headers["location"]


def test_admin_login_and_dashboard_access():
    """Admin đăng nhập và truy cập đầy đủ các trang quản trị."""
    # 1. Đăng nhập admin
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["role"] == "admin"
    admin_token = data["token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    admin_cookies = {"aura_session": admin_token}

    # 2. Truy cập trang /admin
    res_admin = client.get("/admin", headers=admin_headers, cookies=admin_cookies)
    assert res_admin.status_code == 200
    assert "Bảng điều khiển Tổng quan" in res_admin.text or "Dashboard" in res_admin.text

    # 3. Truy cập các trang con admin
    for sub in ["/admin/products", "/admin/orders", "/admin/users", "/admin/trending"]:
        r = client.get(sub, headers=admin_headers, cookies=admin_cookies)
        assert r.status_code == 200, f"Failed accessing {sub}"


def test_admin_stats_api():
    """API /api/admin/stats trả về đầy đủ các chỉ số."""
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {res.json()['token']}"}

    res_stats = client.get("/api/admin/stats", headers=headers)
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert "total_revenue" in stats
    assert "total_orders" in stats
    assert "total_products" in stats
    assert stats["total_products"] >= 500
    assert "total_users" in stats
    assert stats["total_users"] >= 2


def test_admin_product_crud():
    """Admin có thể Xem, Thêm, Sửa, Xóa sản phẩm."""
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {res.json()['token']}"}

    # 1. Lấy danh sách sản phẩm phân trang
    res_list = client.get("/api/admin/products?page=1&limit=10", headers=headers)
    assert res_list.status_code == 200
    data = res_list.json()
    assert data["total"] >= 500
    assert len(data["items"]) == 10

    # 2. Thêm sản phẩm mới
    new_product_payload = {
        "name": "Áo Thun Test Admin Demo",
        "category": "ao-thun",
        "gender": "unisex",
        "price": 280000,
        "original_price": 350000,
        "stock": 50,
        "image": "https://example.com/test.jpg",
        "description": "Sản phẩm test tự động",
        "style": "Minimalism",
        "occasion": "Dạo phố",
        "tags": ["test", "demo", "cotton"],
        "is_flash_sale": False,
        "flash_sale_price": None,
    }
    res_create = client.post("/api/admin/products", json=new_product_payload, headers=headers)
    assert res_create.status_code == 200
    created = res_create.json()
    created_id = created["id"]
    assert created["name"] == "Áo Thun Test Admin Demo"

    # 3. Sửa sản phẩm
    res_update = client.put(f"/api/admin/products/{created_id}", json={"price": 299000, "stock": 45}, headers=headers)
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["price"] == 299000
    assert updated["stock"] == 45

    # 4. Xóa sản phẩm
    res_delete = client.delete(f"/api/admin/products/{created_id}", headers=headers)
    assert res_delete.status_code == 200
    assert res_delete.json()["success"] is True

    # 5. Kiểm tra sản phẩm đã bị xóa
    res_check = client.get(f"/api/products/{created_id}")
    assert res_check.status_code == 404


def test_admin_order_status_update():
    """Admin cập nhật trạng thái đơn hàng."""
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {res.json()['token']}"}

    # Lấy danh sách đơn hàng
    res_orders = client.get("/api/admin/orders?limit=5", headers=headers)
    assert res_orders.status_code == 200
    orders = res_orders.json()
    assert len(orders) > 0

    order_id = orders[0]["order_id"]
    # Cập nhật sang 'shipping'
    res_up = client.put(f"/api/admin/orders/{order_id}/status", json={"status": "shipping"}, headers=headers)
    assert res_up.status_code == 200
    assert res_up.json()["status"] == "shipping"

    # Cập nhật trạng thái không hợp lệ -> 400
    res_bad = client.put(f"/api/admin/orders/{order_id}/status", json={"status": "invalid_status"}, headers=headers)
    assert res_bad.status_code == 400


def test_admin_user_management_and_protection():
    """Quản lý người dùng và bảo vệ tài khoản admin gốc."""
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {res.json()['token']}"}

    # Danh sách người dùng
    res_users = client.get("/api/admin/users", headers=headers)
    assert res_users.status_code == 200
    users = res_users.json()
    admin_user = next((u for u in users if u["username"] == "admin"), None)
    demo_user = next((u for u in users if u["username"] == "user"), None)
    assert admin_user is not None
    assert demo_user is not None

    # Bảo vệ tài khoản admin gốc: không được hạ quyền hoặc khóa
    res_lower_admin = client.put(f"/api/admin/users/{admin_user['id']}/role", json={"role": "user"}, headers=headers)
    assert res_lower_admin.status_code == 400
    assert "Admin mặc định" in res_lower_admin.json()["detail"]

    res_lock_admin = client.put(f"/api/admin/users/{admin_user['id']}/status", json={"is_active": False}, headers=headers)
    assert res_lock_admin.status_code == 400
    assert "Admin mặc định" in res_lock_admin.json()["detail"]

    # Đổi quyền user thường sang admin rồi lại chuyển về user
    res_up_user = client.put(f"/api/admin/users/{demo_user['id']}/role", json={"role": "admin"}, headers=headers)
    assert res_up_user.status_code == 200
    assert res_up_user.json()["role"] == "admin"

    res_restore_user = client.put(f"/api/admin/users/{demo_user['id']}/role", json={"role": "user"}, headers=headers)
    assert res_restore_user.status_code == 200
    assert res_restore_user.json()["role"] == "user"


def test_user_registration_and_profile_flow():
    """Quy trình đăng ký tài khoản mới, cập nhật hồ sơ và đổi mật khẩu."""
    import uuid
    uid = uuid.uuid4().hex[:6]
    username = f"user_{uid}"
    email = f"user_{uid}@example.com"

    # 1. Đăng ký
    res_reg = client.post("/api/auth/register", json={
        "full_name": f"Học Viên {uid}",
        "username": username,
        "email": email,
        "phone": "0912345678",
        "password": "password123"
    })
    assert res_reg.status_code == 200
    data = res_reg.json()
    token = data["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Cập nhật profile
    res_prof = client.put("/api/auth/profile", json={
        "full_name": f"Học Viên Cập Nhật {uid}",
        "phone": "0987654321",
        "address": "123 Đường Thời Trang, Quận 1, TP.HCM"
    }, headers=headers)
    assert res_prof.status_code == 200
    assert res_prof.json()["full_name"] == f"Học Viên Cập Nhật {uid}"
    assert res_prof.json()["phone"] == "0987654321"

    # 3. Đổi mật khẩu
    res_pw = client.post("/api/auth/change-password", json={
        "old_password": "password123",
        "new_password": "newpassword456"
    }, headers=headers)
    assert res_pw.status_code == 200
    assert res_pw.json()["success"] is True

    # 4. Đăng nhập với mật khẩu mới
    res_login_new = client.post("/api/auth/login", json={"username": username, "password": "newpassword456"})
    assert res_login_new.status_code == 200

    # 5. Đăng xuất
    res_logout = client.post("/api/auth/logout")
    assert res_logout.status_code == 200
