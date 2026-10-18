"""
Script phục hồi cơ sở dữ liệu SQLite từ bản sao lưu (Restore Automation)
- Hỗ trợ chọn bản sao lưu cụ thể hoặc tự động lấy bản mới nhất từ thư mục backup.
- Kiểm tra tính toàn vẹn (PRAGMA integrity_check) trước và sau khi phục hồi.
- Sử dụng cơ chế sqlite3 backup API hoặc swap an toàn có rollback phòng ngừa sự cố.
"""
import argparse
import glob
import logging
import os
import shutil
import sqlite3
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [restore]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("restore")


def get_default_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "app", "data", "aura_store.db")


def get_default_backup_dir() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "app", "data", "backups")


def find_latest_backup(backup_dir: str) -> Optional[str]:
    pattern = os.path.join(backup_dir, "aura_store_backup_*.db")
    files = sorted(glob.glob(pattern), key=os.path.getmtime)
    return files[-1] if files else None


def verify_db_integrity(db_path: str) -> bool:
    """Kiểm tra tính toàn vẹn của CSDL bằng PRAGMA integrity_check."""
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        res = cursor.fetchone()
        return bool(res and res[0] == "ok")
    finally:
        conn.close()


def perform_restore(backup_file_path: str, target_db_path: str, dry_run: bool = False) -> bool:
    """
    Phục hồi cơ sở dữ liệu từ file backup vào target_db_path.
    """
    if not os.path.exists(backup_file_path):
        raise FileNotFoundError(f"Không tìm thấy file sao lưu: {backup_file_path}")

    logger.info("Kiểm tra tính toàn vẹn file backup '%s'...", backup_file_path)
    if not verify_db_integrity(backup_file_path):
        raise RuntimeError(f"File sao lưu '{backup_file_path}' bị lỗi hoặc không vượt qua integrity check!")

    if dry_run:
        logger.info("Chế độ DRY-RUN: File sao lưu hợp lệ, bỏ qua ghi đè CSDL đích.")
        return True

    os.makedirs(os.path.dirname(target_db_path), exist_ok=True)

    # Tạo bản sao lưu dự phòng CSDL hiện tại trước khi khôi phục (nếu có)
    pre_restore_bak = None
    if os.path.exists(target_db_path) and os.path.getsize(target_db_path) > 0:
        pre_restore_bak = f"{target_db_path}.prerestore.bak"
        logger.info("Đang tạo bản sao lưu an toàn CSDL hiện tại sang '%s'...", pre_restore_bak)
        shutil.copy2(target_db_path, pre_restore_bak)

    logger.info("Đang phục hồi dữ liệu từ '%s' sang '%s'...", backup_file_path, target_db_path)
    src_conn = sqlite3.connect(backup_file_path, timeout=30.0)
    dest_conn = sqlite3.connect(target_db_path, timeout=30.0)

    try:
        with dest_conn:
            src_conn.backup(dest_conn, pages=100, sleep=0.01)

        # Kiểm tra tính toàn vẹn CSDL đích sau khi phục hồi
        cursor = dest_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        check_result = cursor.fetchone()
        if not check_result or check_result[0] != "ok":
            raise RuntimeError(f"CSDL đích sau khi phục hồi không vượt qua integrity check: {check_result}")

        logger.info("Phục hồi thành công! PRAGMA integrity_check trên CSDL đích: OK")
        # Xóa file pre-restore nếu thành công
        if pre_restore_bak and os.path.exists(pre_restore_bak):
            try:
                os.remove(pre_restore_bak)
            except Exception:
                pass
        return True
    except Exception as e:
        logger.critical("Lỗi trong quá trình phục hồi: %s", e)
        # Rollback từ pre-restore nếu có lỗi
        if pre_restore_bak and os.path.exists(pre_restore_bak):
            logger.warning("Đang rollback lại CSDL gốc từ '%s'...", pre_restore_bak)
            shutil.copy2(pre_restore_bak, target_db_path)
        raise
    finally:
        dest_conn.close()
        src_conn.close()


def run_restore(
    backup_file: Optional[str] = None,
    db_path: Optional[str] = None,
    backup_dir: Optional[str] = None,
    dry_run: bool = False,
) -> str:
    db_path = db_path or os.getenv("DATABASE_PATH") or get_default_db_path()
    backup_dir = backup_dir or os.getenv("BACKUP_DIR") or get_default_backup_dir()

    if not backup_file:
        backup_file = find_latest_backup(backup_dir)
        if not backup_file:
            raise FileNotFoundError(f"Không tìm thấy bản sao lưu nào trong thư mục: {backup_dir}")
        logger.info("Tự động chọn bản sao lưu mới nhất: %s", backup_file)

    perform_restore(backup_file, db_path, dry_run=dry_run)
    return backup_file


def main():
    parser = argparse.ArgumentParser(description="AURA Studio - SQLite Restore Automation")
    parser.add_argument("--backup-file", default=None, help="Đường dẫn file backup cần phục hồi")
    parser.add_argument("--db-path", default=None, help="Đường dẫn CSDL đích cần ghi vào")
    parser.add_argument("--backup-dir", default=None, help="Thư mục backup (dùng để tìm bản mới nhất)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra tính hợp lệ mà không ghi đè CSDL")

    args = parser.parse_args()
    try:
        used_backup = run_restore(
            backup_file=args.backup_file,
            db_path=args.db_path,
            backup_dir=args.backup_dir,
            dry_run=args.dry_run,
        )
        print(f"RESTORE_SUCCESS: {used_backup}")
        sys.exit(0)
    except Exception as e:
        logger.critical("Phục hồi thất bại: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
