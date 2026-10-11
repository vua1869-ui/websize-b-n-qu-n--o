"""
Kiểm thử tự động hệ thống Backup & Restore:
1. Script backup thực hiện online backup qua sqlite3 Connection.backup API (không copy file thô).
2. Bản backup vượt qua kiểm tra tính toàn vẹn (PRAGMA integrity_check).
3. Chính sách lưu trữ (Retention Policy): Giữ tối đa 15 bản sao lưu, tự động xóa các bản cũ.
4. Đẩy bản sao lưu ra nơi lưu trữ ngoài (Offsite Sync).
5. Script restore phục hồi dữ liệu chính xác và CSDL đích vượt qua integrity_check.
"""
import os
import sqlite3
import pytest
from scripts.backup import (
    DEFAULT_KEEP_COUNT,
    enforce_retention_policy,
    perform_sqlite_backup,
    push_offsite,
    run_backup,
)
from scripts.restore import perform_restore, run_restore, verify_db_integrity


@pytest.fixture
def sample_sqlite_db(tmp_path):
    """Tạo một database SQLite mẫu có bảng và dữ liệu để kiểm thử backup."""
    db_file = tmp_path / "test_source.db"
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT, price INTEGER);")
    cursor.execute("INSERT INTO products VALUES ('p1', 'Ao Khoac Blazer', 450000);")
    cursor.execute("INSERT INTO products VALUES ('p2', 'Quan Tay Nam', 320000);")
    conn.commit()
    conn.close()
    return str(db_file)


# ============================================================================
# 1. TEST SAO LƯU ONLINE (SQLITE3 BACKUP API) VÀ INTEGRITY CHECK
# ============================================================================

def test_backup_creates_valid_consistent_copy(sample_sqlite_db, tmp_path):
    """Bản backup tạo ra qua Connection.backup phải vẹn toàn và có đủ dữ liệu."""
    backup_file = str(tmp_path / "backups" / "aura_store_backup_test.db")

    success = perform_sqlite_backup(sample_sqlite_db, backup_file)
    assert success is True
    assert os.path.exists(backup_file)
    assert verify_db_integrity(backup_file) is True

    # Kiểm tra dữ liệu bên trong bản backup
    b_conn = sqlite3.connect(backup_file)
    b_cur = b_conn.cursor()
    b_cur.execute("SELECT COUNT(*) FROM products;")
    count = b_cur.fetchone()[0]
    b_conn.close()
    assert count == 2


# ============================================================================
# 2. TEST CHÍNH SÁCH LƯU TRỮ (GIỮ TỐI ĐA 15 BẢN, XÓA BẢN CŨ)
# ============================================================================

def test_retention_policy_keeps_15_backups(sample_sqlite_db, tmp_path):
    """Khi có hơn 15 bản sao lưu, script tự động dọn dẹp để chỉ giữ lại đúng 15 bản mới nhất."""
    backup_dir = str(tmp_path / "retention_backups")
    os.makedirs(backup_dir, exist_ok=True)

    # Giả lập tạo 18 file backup với thời gian mtime tăng dần
    for i in range(1, 19):
        b_name = f"aura_store_backup_20261011_{i:02d}0000.db"
        b_path = os.path.join(backup_dir, b_name)
        perform_sqlite_backup(sample_sqlite_db, b_path)
        # Giả lập timestamp tăng dần
        os.utime(b_path, (1700000000 + i * 100, 1700000000 + i * 100))

    # Áp dụng chính sách giữ tối đa 15 bản
    deleted = enforce_retention_policy(backup_dir, keep_count=15)
    assert deleted == 3

    # Kiểm tra số lượng file còn lại
    remaining_files = [f for f in os.listdir(backup_dir) if f.startswith("aura_store_backup_")]
    assert len(remaining_files) == 15

    # 3 file cũ nhất (01, 02, 03) phải đã bị xóa
    assert not os.path.exists(os.path.join(backup_dir, "aura_store_backup_20261011_010000.db"))
    assert not os.path.exists(os.path.join(backup_dir, "aura_store_backup_20261011_020000.db"))
    assert not os.path.exists(os.path.join(backup_dir, "aura_store_backup_20261011_030000.db"))
    # Bản mới nhất (18) phải còn
    assert os.path.exists(os.path.join(backup_dir, "aura_store_backup_20261011_180000.db"))


# ============================================================================
# 3. TEST ĐẨY BẢN SAO LƯU RA NƠI LƯU TRỮ NGOÀI (OFFSITE SYNC)
# ============================================================================

def test_push_offsite_remote_directory(sample_sqlite_db, tmp_path):
    """Đẩy bản sao lưu sang remote_dir thành công."""
    backup_file = str(tmp_path / "backups" / "aura_store_backup_offsite.db")
    perform_sqlite_backup(sample_sqlite_db, backup_file)

    remote_dir = str(tmp_path / "remote_storage")
    ok = push_offsite(backup_file, remote_dir=remote_dir)
    assert ok is True

    remote_file = os.path.join(remote_dir, "aura_store_backup_offsite.db")
    assert os.path.exists(remote_file)
    assert os.path.getsize(remote_file) == os.path.getsize(backup_file)


# ============================================================================
# 4. TEST PHỤC HỒI DỮ LIỆU (RESTORE)
# ============================================================================

def test_restore_database_success(sample_sqlite_db, tmp_path):
    """Khôi phục CSDL từ bản backup thành công và vượt qua integrity check."""
    backup_dir = str(tmp_path / "backups")
    backup_file = str(tmp_path / "backups" / "aura_store_backup_for_restore.db")
    perform_sqlite_backup(sample_sqlite_db, backup_file)

    # Tạo một database đích rỗng hoặc khác
    target_db = str(tmp_path / "restored_aura_store.db")

    # Thực hiện restore
    restore_ok = perform_restore(backup_file, target_db)
    assert restore_ok is True
    assert os.path.exists(target_db)
    assert verify_db_integrity(target_db) is True

    # Xác nhận dữ liệu trong database sau phục hồi
    conn = sqlite3.connect(target_db)
    cur = conn.cursor()
    cur.execute("SELECT name, price FROM products ORDER BY price DESC;")
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0] == ("Ao Khoac Blazer", 450000)
    assert rows[1] == ("Quan Tay Nam", 320000)
