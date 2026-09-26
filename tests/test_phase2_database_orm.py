"""
Kiểm thử Giai đoạn 2 (Phase 2): Gộp dữ liệu về Database qua SQLAlchemy ORM:
- Kiểm tra toàn vẹn dữ liệu sau migration: Không mất dữ liệu từ users.json & orders.jsonl.
- Thao tác User qua SQLAlchemy ORM với Transaction: authenticate, register_user, update_profile, change_password, update_role, toggle_status.
- Thao tác Order qua SQLAlchemy ORM: create_order, get_orders, get_order_by_id, update_order_status, count_orders, get_admin_stats.
- Đảm bảo API Contract nguyên vẹn, không ghi vào orders.jsonl.
"""
import uuid
import pytest
from app.db.models import UserDB, OrderDB, OrderItemDB
from app.db.session import get_db_session
from app.services.order_service import order_service
from app.services.user_service import user_service
from tests.conftest import CUSTOMER, item


def test_migration_data_integrity():
    """Xác nhận toàn bộ dữ liệu từ users.json và orders.jsonl đã nằm trọn vẹn trong CSDL."""
    with get_db_session() as session:
        # Ít nhất 19 users từ users.json
        user_count = session.query(UserDB).count()
        assert user_count >= 19, f"Cần ít nhất 19 users nhưng chỉ có {user_count}"

        # Kiểm tra các user demo chủ chốt
        admin_u = session.query(UserDB).filter(UserDB.username == "admin").first()
        assert admin_u is not None
        assert admin_u.role == "admin"

        user_u = session.query(UserDB).filter(UserDB.username == "user").first()
        assert user_u is not None
        assert user_u.role == "user"

        # Đơn hàng từ orders.jsonl (ít nhất 13 đơn)
        order_count = session.query(OrderDB).count()
        assert order_count >= 13, f"Cần ít nhất 13 orders nhưng chỉ có {order_count}"

        # Kiểm tra đơn hàng AURA-260925-713F8F
        order_sample = session.query(OrderDB).filter(OrderDB.order_id == "AURA-260925-713F8F").first()
        assert order_sample is not None
        assert order_sample.customer_name == "Lê Thị Thảo"
        assert order_sample.total_amount == 429000


def test_user_service_orm_operations():
    """Kiểm tra mọi thao tác user_service tương tác chuẩn qua SQLAlchemy ORM."""
    test_uname = f"testuser_{uuid.uuid4().hex[:6]}"
    test_email = f"{test_uname}@example.com"

    # 1. Đăng ký user mới vào DB
    created = user_service.register_user(
        name="Người Dùng Mới",
        email=test_email,
        username=test_uname,
        password="password123",
        phone="0987654321",
    )
    assert created.id is not None
    assert created.username == test_uname
    assert created.email == test_email

    # Kiểm tra trực tiếp trong DB
    with get_db_session() as session:
        in_db = session.query(UserDB).filter(UserDB.id == created.id).first()
        assert in_db is not None
        assert in_db.username == test_uname

    # 2. Xác thực (authenticate) bằng username
    auth_user = user_service.authenticate(test_uname, "password123")
    assert auth_user is not None
    assert auth_user.id == created.id

    # 3. Xác thực bằng email
    auth_email = user_service.authenticate(test_email, "password123")
    assert auth_email is not None
    assert auth_email.id == created.id

    # 4. Cập nhật Profile
    updated = user_service.update_profile(
        user_id=created.id,
        full_name="Tên Mới Cập Nhật",
        phone="0911223344",
        address="123 Đường Số 1, Hà Nội",
    )
    assert updated.full_name == "Tên Mới Cập Nhật"
    assert updated.phone == "0911223344"
    assert updated.address == "123 Đường Số 1, Hà Nội"

    # 5. Đổi mật khẩu
    ok, msg = user_service.change_password(created.id, "password123", "newpassword456")
    assert ok is True
    # Đăng nhập lại với mật khẩu mới
    assert user_service.authenticate(test_uname, "newpassword456") is not None
    assert user_service.authenticate(test_uname, "password123") is None

    # 6. Thay đổi quyền role
    role_updated = user_service.update_role(created.id, "admin")
    assert role_updated.role == "admin"

    # 7. Khóa / Mở khóa tài khoản (toggle_status)
    locked = user_service.toggle_status(created.id, is_active=False)
    assert locked.is_active is False
    assert locked.status == "disabled"

    # Không thể đăng nhập khi bị khóa
    with pytest.raises(ValueError, match="bị khóa"):
        user_service.authenticate(test_uname, "newpassword456")


def test_order_service_orm_operations(client):
    """Kiểm tra đặt hàng, lưu DB qua SQLAlchemy và truy vấn báo cáo."""
    order_service.orders_path = None
    initial_count = order_service.count_orders()

    # 1. Tạo đơn hàng qua API
    order_req = {
        **CUSTOMER,
        "payment_method": "cod",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    res_data = r.json()
    order_id = res_data["order_id"]

    # Số lượng đơn hàng trong DB tăng thêm 1
    new_count = order_service.count_orders()
    assert new_count == initial_count + 1

    # 2. Tìm đơn theo ID qua ORM
    fetched = order_service.get_order_by_id(order_id)
    assert fetched is not None
    assert fetched["order_id"] == order_id
    assert fetched["customer"]["name"] == CUSTOMER["customer_name"]

    # 3. Cập nhật trạng thái đơn hàng (Admin)
    updated = order_service.update_order_status(order_id, "confirmed")
    assert updated["status"] == "confirmed"

    # 4. Thống kê admin phản ánh đúng
    stats = order_service.get_admin_stats()
    assert stats["total_orders"] == new_count
    assert stats["total_revenue"] > 0
    assert stats["status_counts"]["confirmed"] > 0
