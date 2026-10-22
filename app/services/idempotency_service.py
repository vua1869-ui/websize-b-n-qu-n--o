import logging
import re
import threading
import time
from typing import Callable, Optional, Tuple

from fastapi import HTTPException

from app.config import settings
from app.db.models import Base, IdempotencyKeyDB
from app.db.session import get_db_session

logger = logging.getLogger("aura.idempotency")

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.]{8,128}$")


class IdempotencyService:
    def __init__(self):
        self._locks = [threading.Lock() for _ in range(64)]
        self._last_cleanup = 0.0
        self._cleanup_lock = threading.Lock()
        self._table_created = False
        self._table_lock = threading.Lock()

    def _ensure_table(self):
        with self._table_lock:
            try:
                from app.db import session as sess_module
                Base.metadata.create_all(bind=sess_module.engine, tables=[IdempotencyKeyDB.__table__])
                self._table_created = True
            except Exception as e:
                logger.warning("Không thể khởi tạo bảng idempotency_keys: %s", e)

    def _maybe_cleanup(self):
        now = time.time()
        if now - self._last_cleanup < 600:
            return
        with self._cleanup_lock:
            if now - self._last_cleanup < 600:
                return
            self._last_cleanup = now
            try:
                ttl_hours = getattr(settings, "IDEMPOTENCY_TTL_HOURS", 24)
                cutoff = now - (ttl_hours * 3600)
                with get_db_session() as session:
                    session.query(IdempotencyKeyDB).filter(IdempotencyKeyDB.created_at < cutoff).delete()
            except Exception as e:
                logger.warning("Lỗi dọn dẹp idempotency keys cũ: %s", e)

    def run(
        self,
        key: Optional[str],
        scope: str,
        request_hash: str,
        fn: Callable[[], str],
    ) -> Tuple[str, bool]:
        """
        Thực thi hàm fn với cơ chế Idempotency.
        Trả về (result_json, replayed: bool).
        """
        # 1. Không có key: thực thi bình thường (tương thích ngược)
        if not key:
            return fn(), False

        # 2. Kiểm tra định dạng key
        if not KEY_PATTERN.match(key):
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key không hợp lệ. Phải gồm 8-128 ký tự chữ, số hoặc _ - : .",
            )

        self._ensure_table()
        self._maybe_cleanup()

        stored_key = f"{scope}|{key}"
        lock_idx = abs(hash(stored_key)) % len(self._locks)
        lock = self._locks[lock_idx]

        with lock:
            # 3. Kiểm tra xem key đã tồn tại chưa
            with get_db_session() as session:
                existing = session.query(IdempotencyKeyDB).filter(IdempotencyKeyDB.key == stored_key).first()
                if existing:
                    if existing.request_hash != request_hash:
                        raise HTTPException(
                            status_code=422,
                            detail="Idempotency-Key này đã được dùng cho một yêu cầu khác nội dung",
                        )
                    return existing.response_json, True

            # 4. Chưa có: chạy fn()
            # CHỈ lưu khi fn() thành công (nếu ném exception thì không lưu để khách thử lại)
            result_json = fn()

            now_ts = time.time()
            try:
                with get_db_session() as session:
                    item = IdempotencyKeyDB(
                        key=stored_key,
                        request_hash=request_hash,
                        response_json=result_json,
                        created_at=now_ts,
                    )
                    session.add(item)
            except Exception as e:
                logger.warning("Không thể lưu idempotency key '%s': %s", stored_key, e)

            return result_json, False


idempotency_service = IdempotencyService()
