#!/usr/bin/env bash
# ==============================================================================
# AURA STUDIO - Shell Script Backup Database bằng sqlite3 .backup
# Sử dụng: ./scripts/backup.sh [DB_PATH] [BACKUP_DIR] [KEEP_COUNT]
# ==============================================================================
set -euo pipefail

DB_PATH="${1:-${DATABASE_PATH:-app/data/aura_store.db}}"
BACKUP_DIR="${2:-${BACKUP_DIR:-app/data/backups}}"
KEEP_COUNT="${3:-15}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/aura_store_backup_${TIMESTAMP}.db"

mkdir -p "${BACKUP_DIR}"

echo "[$(date)] Bắt đầu sao lưu SQLite bằng lệnh .backup..."
sqlite3 "${DB_PATH}" ".backup '${BACKUP_FILE}'"

# Kiểm tra tính toàn vẹn
CHECK_RES=$(sqlite3 "${BACKUP_FILE}" "PRAGMA integrity_check;")
if [ "${CHECK_RES}" != "ok" ]; then
    echo "[$(date)] LỖI: Bản sao lưu không vượt qua integrity check: ${CHECK_RES}"
    exit 1
fi

echo "[$(date)] Sao lưu thành công: ${BACKUP_FILE}"

# Giữ 15 bản sao lưu mới nhất, xóa các bản cũ
cd "${BACKUP_DIR}"
ls -t aura_store_backup_*.db 2>/dev/null | tail -n +$((KEEP_COUNT + 1)) | xargs -I {} rm -f "{}" || true
echo "[$(date)] Đã áp dụng chính sách giữ tối đa ${KEEP_COUNT} bản sao lưu."

# Đẩy ra nơi lưu trữ ngoài nếu được cấu hình
if [ -n "${REMOTE_BACKUP_DIR:-}" ]; then
    mkdir -p "${REMOTE_BACKUP_DIR}"
    cp -p "${BACKUP_FILE}" "${REMOTE_BACKUP_DIR}/"
    echo "[$(date)] Đã đẩy bản sao lưu ra REMOTE_BACKUP_DIR: ${REMOTE_BACKUP_DIR}"
fi

if [ -n "${OFFSITE_COMMAND:-}" ]; then
    echo "[$(date)] Đang chạy lệnh OFFSITE_COMMAND..."
    eval "${OFFSITE_COMMAND//\{backup_path\}/${BACKUP_FILE}}"
fi
