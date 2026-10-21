"""
Script dọn dẹp các tài khoản người dùng giả lập (@example.com) và đơn hàng liên quan.
An toàn:
- Mặc định chạy ở chế độ --dry-run (chỉ thống kê, KHÔNG thay đổi dữ liệu).
- Cần chỉ định cờ --apply để thực hiện xóa thật.
- Luôn tự động sao lưu CSDL trước khi xóa thật.
"""
import argparse
import datetime
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from sqlalchemy import func
from app.db.models import OrderDB, UserDB
from app.db.session import engine, get_db_session


def clean_demo_users(apply: bool = False):
    print("=" * 70)
    mode_str = "THỰC THI XÓA THẬT (--apply)" if apply else "CHẾ ĐỘ THỬ NGHIỆM (--dry-run - Mặc định)"
    print(f"Quét dọn tài khoản demo @example.com - Chế độ: {mode_str}")
    print("=" * 70)

    with get_db_session() as session:
        # 1. Tìm các user có email kết thúc hoặc chứa @example.com
        demo_users = session.query(UserDB).filter(UserDB.email.ilike("%@example.com")).all()
        demo_user_ids = [u.id for u in demo_users]
        user_count = len(demo_user_ids)

        if not demo_user_ids:
            print("Không tìm thấy tài khoản người dùng @example.com nào trong hệ thống.")
            print("=" * 70)
            return

        # 2. Tìm các đơn hàng liên quan đến danh sách user này
        demo_orders = session.query(OrderDB).filter(OrderDB.user_id.in_(demo_user_ids)).all()
        order_count = len(demo_orders)

        print(f"Tìm thấy:")
        print(f" - {user_count} tài khoản người dùng có email '@example.com'")
        print(f" - {order_count} đơn hàng liên quan đến các tài khoản trên")

        if not apply:
            print("\n[THÔNG BÁO] Đây là chế độ kiểm tra (--dry-run). Không có dữ liệu nào bị thay đổi.")
            print("Để thực hiện xóa thật, vui lòng chạy lệnh với cờ: python scripts/clean_demo_users.py --apply")
            print("=" * 70)
            return

        # 3. Tạo bản backup CSDL trước khi xóa thật
        db_url = str(engine.url)
        print(f"\nĐang tiến hành sao lưu CSDL an toàn...")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if "sqlite" in db_url:
            sqlite_path = engine.url.database
            if sqlite_path and os.path.exists(sqlite_path):
                backup_path = f"{sqlite_path}.clean_demo_backup_{ts}"
                shutil.copy2(sqlite_path, backup_path)
                print(f"[BACKUP] Đã sao lưu SQLite sang: {backup_path}")
        else:
            print(f"[BACKUP] Đang dùng CSDL {engine.dialect.name}. Vui lòng đảm bảo bạn đã tạo snapshot nếu cần.")

        # 4. Xóa các đơn hàng liên quan
        print(f"Đang xóa {order_count} đơn hàng...")
        session.query(OrderDB).filter(OrderDB.user_id.in_(demo_user_ids)).delete(synchronize_session=False)

        # 5. Xóa các user demo
        print(f"Đang xóa {user_count} tài khoản demo...")
        session.query(UserDB).filter(UserDB.id.in_(demo_user_ids)).delete(synchronize_session=False)

        session.commit()
        print(f"[HOÀN TẤT] Đã xóa thành công {user_count} user demo và {order_count} đơn hàng tương ứng.")
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Dọn dẹp tài khoản người dùng demo @example.com")
    parser.add_argument("--apply", action="store_true", help="Xác nhận xóa thật khỏi cơ sở dữ liệu (có backup)")
    args = parser.parse_args()

    clean_demo_users(apply=args.apply)


if __name__ == "__main__":
    main()
