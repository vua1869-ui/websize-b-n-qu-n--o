"""
Script sao lưu cơ sở dữ liệu SQLite định kỳ (Backup Automation)
- Sử dụng chuẩn sqlite3 online backup API (source_conn.backup) - KHÔNG COPY FILE THÔ.
- Đảm bảo tính nhất quán dữ liệu ACID ngay cả khi đang có người dùng truy cập.
- Kiểm tra tính toàn vẹn ngay sau khi sao lưu (PRAGMA integrity_check).
- Duy trì chính sách lưu trữ: giữ tối đa 15 bản sao lưu gần nhất, tự động xóa các bản cũ hơn.
- Hỗ trợ đẩy bản sao lưu ra nơi lưu trữ ngoài máy chủ (Offsite Sync qua remote dir hoặc offsite cmd).
"""
import argparse
import datetime
import glob
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [backup]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backup")

DEFAULT_KEEP_COUNT = 15


def get_default_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "app", "data", "aura_store.db")


def get_default_backup_dir() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "app", "data", "backups")


def perform_sqlite_backup(source_db_path: str, backup_file_path: str) -> bool:
    """
    Thực hiện online backup an toàn qua sqlite3 Connection.backup API.
    Tuyệt đối không copy file thô khi CSDL đang hoạt động.
    """
    if not os.path.exists(source_db_path):
        raise FileNotFoundError(f"Không tìm thấy cơ sở dữ liệu nguồn: {source_db_path}")

    os.makedirs(os.path.dirname(backup_file_path), exist_ok=True)

    logger.info("Bắt đầu sao lưu CSDL từ '%s' sang '%s'...", source_db_path, backup_file_path)

    # 1. Kết nối DB nguồn và DB đích
    src_conn = sqlite3.connect(source_db_path, timeout=30.0)
    dest_conn = sqlite3.connect(backup_file_path, timeout=30.0)

    try:
        # 2. Sử dụng sqlite3 Online Backup API
        with dest_conn:
            src_conn.backup(dest_conn, pages=100, sleep=0.01)

        # 3. Kiểm tra tính toàn vẹn (integrity check) trên file backup
        cursor = dest_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        check_result = cursor.fetchone()
        if not check_result or check_result[0] != "ok":
            raise RuntimeError(f"Kiểm tra tính toàn vẹn file backup thất bại: {check_result}")

        logger.info("Sao lưu thành công! PRAGMA integrity_check: OK (kích thước: %d bytes)", os.path.getsize(backup_file_path))
        return True
    finally:
        dest_conn.close()
        src_conn.close()


def enforce_retention_policy(backup_dir: str, keep_count: int = DEFAULT_KEEP_COUNT) -> int:
    """
    Giữ tối đa keep_count (mặc định 15) bản sao lưu mới nhất, xóa các bản cũ hơn.
    """
    pattern = os.path.join(backup_dir, "aura_store_backup_*.db")
    backup_files = sorted(glob.glob(pattern), key=os.path.getmtime)

    deleted_count = 0
    if len(backup_files) > keep_count:
        files_to_remove = backup_files[: len(backup_files) - keep_count]
        for f in files_to_remove:
            try:
                os.remove(f)
                deleted_count += 1
                logger.info("Đã xóa bản sao lưu cũ vượt định ngạch: %s", os.path.basename(f))
            except Exception as e:
                logger.warning("Không thể xóa bản sao lưu cũ '%s': %s", f, e)

    logger.info("Chính sách lưu trữ: Hiện còn %d bản sao lưu (giới hạn tối đa: %d bản).", min(len(backup_files), keep_count), keep_count)
    return deleted_count


def push_offsite(
    backup_file_path: str,
    remote_dir: Optional[str] = None,
    offsite_cmd: Optional[str] = None,
) -> bool:
    """
    Đẩy bản sao lưu ra nơi lưu trữ ngoài máy chủ:
    - remote_dir: Thư mục gắn kết ngoài (NFS / Cloud Mount / External Storage).
    - offsite_cmd: Lệnh shell tùy chỉnh (ví dụ: rclone copy {backup_path} s3:my-bucket).
    """
    success = True
    filename = os.path.basename(backup_file_path)

    # 1. Đẩy ra thư mục lưu trữ ngoài (Remote Directory)
    if remote_dir:
        try:
            os.makedirs(remote_dir, exist_ok=True)
            target_remote_path = os.path.join(remote_dir, filename)
            shutil.copy2(backup_file_path, target_remote_path)
            logger.info("Đã đẩy bản sao lưu ra nơi lưu trữ ngoài máy chủ: %s", target_remote_path)
        except Exception as e:
            logger.error("Lỗi khi đẩy bản sao lưu ra remote_dir '%s': %s", remote_dir, e)
            success = False

    # 2. Chạy lệnh đẩy ra Cloud ngoài (Offsite Command)
    if offsite_cmd:
        cmd = offsite_cmd.format(backup_path=backup_file_path, filename=filename)
        logger.info("Đang thực thi lệnh offsite sync: %s", cmd)
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                logger.info("Lệnh offsite sync thành công: %s", res.stdout.strip())
            else:
                logger.error("Lệnh offsite sync thất bại (code %d): %s", res.returncode, res.stderr.strip())
                success = False
        except Exception as e:
            logger.error("Lỗi khi thực thi lệnh offsite: %s", e)
            success = False

    return success


def run_backup(
    db_path: Optional[str] = None,
    backup_dir: Optional[str] = None,
    keep_count: int = DEFAULT_KEEP_COUNT,
    remote_dir: Optional[str] = None,
    offsite_cmd: Optional[str] = None,
) -> str:
    db_path = db_path or os.getenv("DATABASE_PATH") or get_default_db_path()
    backup_dir = backup_dir or os.getenv("BACKUP_DIR") or get_default_backup_dir()
    remote_dir = remote_dir or os.getenv("REMOTE_BACKUP_DIR")
    offsite_cmd = offsite_cmd or os.getenv("OFFSITE_COMMAND")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"aura_store_backup_{timestamp}.db"
    backup_file_path = os.path.join(backup_dir, backup_filename)

    # Thực hiện sao lưu
    perform_sqlite_backup(db_path, backup_file_path)

    # Áp dụng chính sách duy trì 15 bản
    enforce_retention_policy(backup_dir, keep_count)

    # Đẩy ra nơi lưu trữ ngoài
    if remote_dir or offsite_cmd:
        push_offsite(backup_file_path, remote_dir=remote_dir, offsite_cmd=offsite_cmd)
    else:
        logger.info("Không cấu hình REMOTE_BACKUP_DIR hoặc OFFSITE_COMMAND, hoàn tất sao lưu cục bộ.")

    return backup_file_path


def main():
    parser = argparse.ArgumentParser(description="AURA Studio - SQLite Online Backup Automation")
    parser.add_argument("--db-path", default=None, help="Đường dẫn file database nguồn SQLite")
    parser.add_argument("--backup-dir", default=None, help="Thư mục lưu trữ bản backup")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP_COUNT, help="Số bản backup tối đa cần giữ (mặc định: 15)")
    parser.add_argument("--remote-dir", default=None, help="Thư mục ngoài máy chủ để đồng bộ bản backup")
    parser.add_argument("--offsite-cmd", default=None, help="Lệnh đồng bộ offsite (VD: rclone copy {backup_path} remote:bucket)")

    args = parser.parse_args()
    try:
        backup_path = run_backup(
            db_path=args.db_path,
            backup_dir=args.backup_dir,
            keep_count=args.keep,
            remote_dir=args.remote_dir,
            offsite_cmd=args.offsite_cmd,
        )
        print(f"BACKUP_SUCCESS: {backup_path}")
        sys.exit(0)
    except Exception as e:
        logger.critical("Sao lưu thất bại: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
