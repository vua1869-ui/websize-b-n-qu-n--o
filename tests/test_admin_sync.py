"""
Kiểm thử tính năng đồng bộ dữ liệu trên trang quản trị Admin:
1. Đồng bộ tài khoản mới: Người dùng đăng ký tài khoản -> xuất hiện ngay ở đầu danh sách GET /api/admin/users và phản ánh trong GET /api/admin/stats.
2. Đồng bộ đơn hàng mới: Tạo đơn hàng mới -> hiển thị ngay trong GET /api/admin/orders và phản ánh trong GET /api/admin/stats.
3. Đồng bộ cập nhật trạng thái đơn hàng: Thay đổi trạng thái đơn (chờ thanh toán -> đã xác nhận/đã thanh toán/hủy) -> hiển thị trạng thái mới nhất ngay trong GET /api/admin/orders.
4. Phân quyền chặt chẽ (RBAC): Chỉ admin mới có quyền truy cập /api/admin/*; người dùng thông thường nhận 403 Forbidden, chưa đăng nhập nhận 401 Unauthorized.
5. Cache-Control Header: Đảm bảo các API danh sách admin trả về Cache-Control: no-store để ngăn trình duyệt/proxy cache dữ liệu cũ.
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import db_service
from tests.conftest import CUSTOMER, item

client = TestClient(app)


def get_admin_headers():
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def create_user_and_get_headers(username=None):
    if not username:
        username = f"user_{uuid.uuid4().hex[:8]}"
    email = f"{username}@test.com"
    reg_res = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "Password123!",
            "email": email,
            "full_name": f"Tester {username}",
            "phone": "0987654321",
        },
    )
    assert reg_res.status_code == 200, reg_res.text
    login_res = client.post("/api/auth/login", json={"username": username, "password": "Password123!"})
    assert login_res.status_code == 200, login_res.text
    token = login_res.json()["token"]
    return username, email, {"Authorization": f"Bearer {token}"}


class TestAdminSync:

    def test_new_user_registration_immediately_syncs_to_admin_list_and_stats(self):
        """Tài khoản mới đăng ký phải xuất hiện ngay ở đầu danh sách users của admin và tăng stats."""
        admin_headers = get_admin_headers()

        # 1. Lấy stats trước khi đăng ký
        stats_before = client.get("/api/admin/stats", headers=admin_headers)
        assert stats_before.status_code == 200
        initial_users_count = stats_before.json()["total_users"]
        initial_new_users_today = stats_before.json()["new_users_today"]

        # 2. Đăng ký người dùng mới
        unique_username = f"sync_user_{uuid.uuid4().hex[:6]}"
        unique_email = f"{unique_username}@example.com"
        reg = client.post(
            "/api/auth/register",
            json={
                "username": unique_username,
                "password": "Password123!",
                "email": unique_email,
                "full_name": f"Full Name {unique_username}",
                "phone": "0911223344",
            },
        )
        assert reg.status_code == 200, reg.text
        user_data = reg.json()

        # 3. Gọi ngay GET /api/admin/users
        res_users = client.get("/api/admin/users", headers=admin_headers)
        assert res_users.status_code == 200
        users_list = res_users.json()
        assert len(users_list) >= initial_users_count + 1

        # Người dùng mới phải xuất hiện ngay ở đầu danh sách (sắp xếp giảm dần theo ID)
        first_user = users_list[0]
        assert first_user["username"] == unique_username
        assert first_user["email"] == unique_email
        assert first_user["role"] == "user"

        # 4. Kiểm tra GET /api/admin/stats phản ánh người dùng mới ngay lập tức
        stats_after = client.get("/api/admin/stats", headers=admin_headers)
        assert stats_after.status_code == 200
        new_stats = stats_after.json()
        assert new_stats["total_users"] == initial_users_count + 1
        assert new_stats["new_users_today"] == initial_new_users_today + 1

    def test_new_order_creation_immediately_syncs_to_admin_orders_and_stats(self):
        """Đơn hàng mới tạo lập tức hiển thị trên API admin và cập nhật thống kê doanh số / đơn hàng."""
        admin_headers = get_admin_headers()

        # 1. Lấy stats trước
        stats_before = client.get("/api/admin/stats", headers=admin_headers)
        assert stats_before.status_code == 200
        initial_total_orders = stats_before.json()["total_orders"]
        initial_pending_orders = stats_before.json()["status_counts"]["pending"]

        # 2. Tạo đơn hàng mới với vietqr (trạng thái pending_payment)
        order_req = {
            **CUSTOMER,
            "items": [item("prod_001", qty=2)],
            "payment_method": "vietqr",
        }
        order_res = client.post("/api/orders", json=order_req)
        assert order_res.status_code == 200, order_res.text
        created_order = order_res.json()
        new_order_id = created_order["order_id"]

        # 3. Gọi GET /api/admin/orders
        orders_res = client.get("/api/admin/orders", headers=admin_headers)
        assert orders_res.status_code == 200
        orders_list = orders_res.json()

        # Đơn mới phải xuất hiện ngay lập tức
        found_order = next((o for o in orders_list if o["order_id"] == new_order_id), None)
        assert found_order is not None, f"Đơn hàng {new_order_id} không tìm thấy trong danh sách admin!"
        assert found_order["payment_method"] == "vietqr"
        assert found_order["status"] == "pending_payment"

        # 4. Kiểm tra thống kê stats
        stats_after = client.get("/api/admin/stats", headers=admin_headers)
        assert stats_after.status_code == 200
        new_stats = stats_after.json()
        assert new_stats["total_orders"] == initial_total_orders + 1
        assert new_stats["status_counts"]["pending"] == initial_pending_orders + 1

    def test_order_status_update_syncs_to_admin_orders(self):
        """Cập nhật trạng thái đơn hàng (xác nhận, thanh toán, hủy) phản ánh chính xác trong API admin."""
        admin_headers = get_admin_headers()

        # Tạo đơn
        order_req = {
            **CUSTOMER,
            "items": [item("prod_002", qty=1)],
            "payment_method": "vietqr",
        }
        order_res = client.post("/api/orders", json=order_req)
        assert order_res.status_code == 200
        order_id = order_res.json()["order_id"]

        # Chuyển trạng thái đơn sang 'confirmed'
        update_res = client.put(
            f"/api/admin/orders/{order_id}/status",
            headers=admin_headers,
            json={"status": "confirmed"},
        )
        assert update_res.status_code == 200, update_res.text

        # Lấy lại danh sách đơn hàng
        orders_res = client.get(f"/api/admin/orders?search={order_id}", headers=admin_headers)
        assert orders_res.status_code == 200
        matched = [o for o in orders_res.json() if o["order_id"] == order_id]
        assert len(matched) == 1
        assert matched[0]["status"] == "confirmed"

    def test_rbac_admin_endpoints_protection(self):
        """Bảo vệ phân quyền: Người dùng chưa xác thực nhận 401, người dùng không phải admin nhận 403."""
        endpoints = [
            "/api/admin/users",
            "/api/admin/orders",
            "/api/admin/stats",
        ]

        # 1. Chưa đăng nhập (không có token) -> 401
        for ep in endpoints:
            res = client.get(ep)
            assert res.status_code == 401, f"Endpoint {ep} phải trả về 401 khi chưa đăng nhập, nhưng nhận {res.status_code}"

        # 2. Đăng nhập người dùng thông thường (role user) -> 403
        _, _, user_headers = create_user_and_get_headers()
        for ep in endpoints:
            res = client.get(ep, headers=user_headers)
            assert res.status_code == 403, f"Endpoint {ep} phải trả về 403 đối với non-admin, nhưng nhận {res.status_code}"

    def test_cache_control_headers_on_admin_apis(self):
        """Kiểm tra header Cache-Control: no-store để đảm bảo browser không cache danh sách cũ."""
        admin_headers = get_admin_headers()
        endpoints = [
            "/api/admin/users",
            "/api/admin/orders",
            "/api/admin/stats",
        ]

        for ep in endpoints:
            res = client.get(ep, headers=admin_headers)
            assert res.status_code == 200
            cache_control = res.headers.get("cache-control", "")
            assert "no-store" in cache_control, f"Endpoint {ep} thiếu 'no-store' trong Cache-Control: {cache_control}"
