import hashlib
import os
import shutil
import sys
import tempfile
from typing import Optional

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ORIG_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "aura_store.db"
)


def _get_db_sha256(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


# 1. Tính mã băm SHA256 ban đầu của aura_store.db gốc
_ORIG_SHA256 = _get_db_sha256(_ORIG_DB_PATH)

# 2. Tạo bản COPY của CSDL trong thư mục tạm trước khi import app/database/session
_TMP_SESSION_DIR = tempfile.mkdtemp(prefix="aura_test_session_")
_TEST_DB_PATH = os.path.join(_TMP_SESSION_DIR, "aura_store_test.db").replace("\\", "/")

if os.path.exists(_ORIG_DB_PATH):
    shutil.copy2(_ORIG_DB_PATH, _TEST_DB_PATH)

# Thiết lập biến môi trường để cả SQLAlchemy engine lẫn sqlite3 thô trỏ vào bản copy
os.environ["AURA_SQLITE_PATH"] = _TEST_DB_PATH
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

import app.db.database as db_module  # noqa: E402
from app.db.database import db_service  # noqa: E402
from app.main import app  # noqa: E402
from app.services.order_service import order_service  # noqa: E402
from app.services.product_service import product_service  # noqa: E402

# Đảm bảo chắc chắn cả hai tầng DB đều dùng bản copy
db_module.DB_PATH = _TEST_DB_PATH
db_service.db_path = _TEST_DB_PATH

from sqlalchemy import create_engine  # noqa: E402
from app.db import session as session_module  # noqa: E402

session_module.DATABASE_URL = f"sqlite:///{_TEST_DB_PATH}"
session_module.engine = create_engine(
    session_module.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 20},
    pool_pre_ping=True,
)
session_module.SessionLocal.configure(bind=session_module.engine)

from app.config import settings  # noqa: E402
settings.COD_MAX_OPEN_PER_PHONE = 0
settings.MBBANK_ACCOUNT_NUMBER = "0000000000"
settings.MBBANK_ACCOUNT_NAME = "TEST ACCOUNT"
settings.MBBANK_BANK_NAME = "MB Bank"
settings.MBBANK_BANK_CODE = "MB"
settings.MBBANK_QR_IMAGE = ""


@pytest.fixture(scope="session", autouse=True)
def protect_real_database():
    """Đảm bảo mọi test ghi vào bản copy tạm và CSDL thật aura_store.db không bị thay đổi."""
    yield
    # Kiểm tra tính toàn vẹn của CSDL gốc
    final_sha256 = _get_db_sha256(_ORIG_DB_PATH)
    assert final_sha256 == _ORIG_SHA256, (
        f"LỖI BẢO VỆ DỮ LIỆU: File CSDL gốc '{_ORIG_DB_PATH}' đã bị ghi đè/thay đổi trong lúc chạy test! "
        f"SHA256 ban đầu: {_ORIG_SHA256}, SHA256 sau test: {final_sha256}"
    )
    # Dọn dẹp thư mục tạm
    try:
        shutil.rmtree(_TMP_SESSION_DIR, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    """Mỗi test: nạp lại kho gốc và ghi đơn vào file tạm (không đụng dữ liệu thật)."""
    order_service.orders_path = str(tmp_path / "orders.jsonl")
    db_service.seed_reset_stock()
    product_service._load_products()
    from app.routers.deps import (
        _forgot_pw_reqs, _login_ip_attempts, _login_pair_attempts,
        _register_ip_attempts, _track_reqs,
    )
    _login_ip_attempts.clear()
    _login_pair_attempts.clear()
    _register_ip_attempts.clear()
    _forgot_pw_reqs.clear()
    _track_reqs.clear()
    yield


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


CUSTOMER = {"customer_name": "Nguyễn Văn A", "customer_phone": "0987 654 321",
            "customer_address": "12 Phố Huế, Hai Bà Trưng, Hà Nội"}


def item(pid="prod_001", size="M", color=None, qty=1, **kw):
    p = product_service.get_by_id(pid)
    return {"product_id": pid, "size": size if size in p.sizes else p.sizes[0],
            "color": color or p.colors[0].name, "quantity": qty, **kw}
