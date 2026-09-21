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

if __name__ == "__main__":
    url = f"http://{settings.HOST}:{settings.PORT}"
    print("=" * 60)
    print(f"{settings.APP_NAME} đang khởi động...")
    print(f"Mở trình duyệt tại: {url}")
    print("Nhấn CTRL+C để dừng.")
    print("=" * 60)
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
