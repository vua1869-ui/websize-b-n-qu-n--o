"""Khởi động AURA STUDIO:  python run.py"""
import os
import sys

# Tránh lỗi hiển thị tiếng Việt trên Windows PowerShell / CMD
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402


def _ensure_tables():
    """Tạo các bảng DB nếu chưa tồn tại (không chèn dữ liệu, không thực hiện migration).
    
    Nếu cần đưa dữ liệu demo vào, chạy: python scripts/migrate_to_db.py
    """
    try:
        from app.db.models import Base
        from app.db.session import engine
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        print(f"[WARN] Không tạo được bảng DB: {e}")


if __name__ == "__main__":
    _ensure_tables()
    url = f"http://{settings.HOST}:{settings.PORT}"
    print("=" * 60)
    print(f"{settings.APP_NAME} đang khởi động...")
    print(f"Mở trình duyệt tại: {url}")
    print("Nhấn CTRL+C để dừng.")
    print("=" * 60)
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
