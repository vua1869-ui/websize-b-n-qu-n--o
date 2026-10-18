#!/usr/bin/env bash
# ==============================================================================
# AURA STUDIO - Shell Script Phục Hồi Database từ Bản Backup
# Sử dụng: ./scripts/restore.sh [BACKUP_FILE] [TARGET_DB_PATH]
# ==============================================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-app/data/backups}"
TARGET_DB="${2:-${DATABASE_PATH:-app/data/aura_store.db}}"

if [ -n "${1:-}" ]; then
    BACKUP_FILE="$1"
else
    # Lấy bản sao lưu mới nhất
    BACKUP_FILE=$(ls -t "${BACKUP_DIR}"/aura_store_backup_*.db 2>/dev/null | head -n 1 || true)
fi

if [ -z "${BACKUP_FILE}" ] || [ ! -f "${BACKUP_FILE}" ]; then
    echo "LỖI: Không tìm thấy file sao lưu để phục hồi!"
    exit 1
fi

echo "[$(date)] Kiểm tra tính toàn vẹn file backup: ${BACKUP_FILE}..."
CHECK_RES=$(sqlite3 "${BACKUP_FILE}" "PRAGMA integrity_check;")
if [ "${CHECK_RES}" != "ok" ]; then
    echo "LỖI: File backup bị hỏng (${CHECK_RES})!"
    exit 1
fi

echo "[$(date)] Đang phục hồi dữ liệu vào ${TARGET_DB}..."
mkdir -p "$(dirname "${TARGET_DB}")"
sqlite3 "${BACKUP_FILE}" ".backup '${TARGET_DB}'"

TARGET_CHECK=$(sqlite3 "${TARGET_DB}" "PRAGMA integrity_check;")
if [ "${TARGET_CHECK}" != "ok" ]; then
    echo "LỖI: CSDL sau khi phục hồi không hợp lệ (${TARGET_CHECK})!"
    exit 1
fi

echo "[$(date)] Phục hồi cơ sở dữ liệu thành công!"
