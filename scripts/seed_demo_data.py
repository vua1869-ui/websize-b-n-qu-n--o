"""
Script khởi tạo dữ liệu giả lập chuẩn (Mock Seed Data) dành cho môi trường phát triển (Dev).
Giúp lập trình viên dễ dàng chạy dự án độc lập mà không cần dùng dữ liệu thật của khách hàng.
"""
import datetime
import os
import secrets
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

app_env = os.getenv("APP_ENV", "development").strip().lower()
admin_pw_env = os.getenv("SEED_ADMIN_PASSWORD")
if app_env == "production" and not admin_pw_env:
    print("[LỖI BẢO MẬT] Từ chối chạy seed trên môi trường production khi chưa đặt biến môi trường SEED_ADMIN_PASSWORD.")
    sys.exit(1)

from app.db.models import UserDB
from app.db.session import Base, engine, get_db_session
from app.services.user_service import hash_password


def seed_demo_data():
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    admin_pw_env = os.getenv("SEED_ADMIN_PASSWORD")

    if app_env == "production" and not admin_pw_env:
        print("[LỖI BẢO MẬT] Từ chối chạy seed trên môi trường production khi chưa đặt biến môi trường SEED_ADMIN_PASSWORD.")
        sys.exit(1)

    generated_admin_pw = None
    if admin_pw_env:
        admin_password = admin_pw_env
    else:
        admin_password = secrets.token_urlsafe(12)
        generated_admin_pw = admin_password

    user_pw_env = os.getenv("SEED_USER_PASSWORD")
    generated_user_pw = None
    if user_pw_env:
        user_password = user_pw_env
    else:
        user_password = secrets.token_urlsafe(12)
        generated_user_pw = user_password

    mock_users = [
        {
            "id": "usr_001",
            "username": "admin",
            "email": "admin@aurastudio.vn",
            "name": "Quản trị viên",
            "full_name": "Quản trị viên Hệ thống",
            "phone": "0900000000",
            "address": "123 Đường Thời Trang, Quận 1, TP. Hồ Chí Minh",
            "role": "admin",
            "status": "active",
            "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=200&auto=format&fit=crop",
            "points_balance": 500,
            "total_spent": 15000000,
            "tier": "Diamond",
            "password": admin_password
        },
        {
            "id": "usr_002",
            "username": "user",
            "email": "khachhang.demo@aurastudio.vn",
            "name": "Khách Hàng Demo",
            "full_name": "Nguyễn Văn Thuận",
            "phone": "0987654321",
            "address": "456 Đường Nguyễn Trãi, Quận 5, TP. Hồ Chí Minh",
            "role": "user",
            "status": "active",
            "avatar": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?q=80&w=200&auto=format&fit=crop",
            "points_balance": 120,
            "total_spent": 2500000,
            "tier": "Gold",
            "password": user_password
        }
    ]

    print("=" * 70)
    print("Bắt đầu nạp dữ liệu mẫu giả (Mock Seed Data)...")
    print("=" * 70)

    # 1. Đảm bảo cấu trúc bảng trong CSDL
    Base.metadata.create_all(bind=engine)

    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    seeded_count = 0

    with get_db_session() as session:
        for u in mock_users:
            existing = session.query(UserDB).filter(UserDB.id == u["id"]).first()
            if not existing:
                user_db = UserDB(
                    id=u["id"],
                    username=u["username"],
                    email=u["email"],
                    password_hash=hash_password(u["password"]),
                    name=u["name"],
                    full_name=u["full_name"],
                    phone=u["phone"],
                    address=u["address"],
                    role=u["role"],
                    status=u["status"],
                    avatar=u["avatar"],
                    points_balance=u["points_balance"],
                    total_spent=u["total_spent"],
                    tier=u["tier"],
                    created_at=now_str,
                    token_version=1
                )
                session.add(user_db)
                seeded_count += 1

    print(f"Đã nạp mới {seeded_count} tài khoản demo (admin / user).")
    if generated_admin_pw:
        print(f"[QUAN TRỌNG - LƯU NGAY] Mật khẩu ngẫu nhiên cho admin: {generated_admin_pw}")
    if generated_user_pw:
        print(f"[QUAN TRỌNG - LƯU NGAY] Mật khẩu ngẫu nhiên cho user: {generated_user_pw}")
    print("Môi trường đã sẵn sàng!")
    print("=" * 70)


if __name__ == "__main__":
    seed_demo_data()
