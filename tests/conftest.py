import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.services.order_service import order_service  # noqa: E402
from app.services.product_service import product_service  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    """Mỗi test: nạp lại kho gốc và ghi đơn vào file tạm (không đụng dữ liệu thật)."""
    order_service.orders_path = str(tmp_path / "orders.jsonl")
    product_service._load_products()
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
