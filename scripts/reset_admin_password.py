"""
Script an toàn để quản trị viên đặt lại mật khẩu cho tài khoản 'admin'.
Nhận mật khẩu mới qua tham số dòng lệnh (--password / -p) hoặc nhập bảo mật qua getpass.
Mật khẩu được hash bằng bcrypt (rounds=12) qua hàm hash_password() của hệ thống.
Tự động cập nhật password_hash, password_changed_at và tăng token_version để vô hiệu hóa phiên cũ.
"""
import argparse
import getpass
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from sqlalchemy import func
from app.db.models import UserDB
from app.db.session import get_db_session
from app.services.user_service import hash_password


def reset_admin_password(new_password: str) -> bool:
    """Cập nhật mật khẩu cho user 'admin' và tăng token_version."""
    if not new_password:
        print("[LỖI] Mật khẩu không được để trống.")
        return False
    if len(new_password) < 6:
        print("[LỖI] Mật khẩu phải có tối thiểu 6 ký tự.")
        return False

    with get_db_session() as session:
        admin_user = session.query(UserDB).filter(func.lower(UserDB.username) == "admin").first()
        if not admin_user:
            print("[LỖI] Không tìm thấy tài khoản 'admin' trong cơ sở dữ liệu.")
            return False

        hashed = hash_password(new_password)
        admin_user.password_hash = hashed
        admin_user.password_changed_at = time.time()
        admin_user.token_version = (admin_user.token_version or 1) + 1
        session.commit()

        print(f"[THÀNH CÔNG] Đã đổi mật khẩu cho tài khoản 'admin'.")
        print(f"[THÔNG TIN] token_version mới: {admin_user.token_version} (các phiên đăng nhập cũ đã bị thu hồi).")
        return True


def main():
    parser = argparse.ArgumentParser(description="Đặt lại mật khẩu cho tài khoản Admin AURA Studio")
    parser.add_argument("--password", "-p", type=str, help="Mật khẩu mới (nếu không truyền sẽ dùng prompt getpass)")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("Nhập mật khẩu mới cho admin: ")
        confirm = getpass.getpass("Xác nhận lại mật khẩu mới: ")
        if password != confirm:
            print("[LỖI] Mật khẩu xác nhận không trùng khớp.")
            sys.exit(1)

    success = reset_admin_password(password)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
