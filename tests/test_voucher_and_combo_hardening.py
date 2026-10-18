import datetime
import uuid
import pytest
from starlette.testclient import TestClient

from app.config import settings
from app.db.database import db_service
from app.models.schemas import Voucher, OrderItem
from app.services.order_service import make_combo_token, order_service
from app.services.product_service import product_service
from tests.conftest import CUSTOMER, item


def test_voucher_expired_rejected(client: TestClient):
    """Test voucher hết hạn bị từ chối ở cả API quote, create order và validate."""
    vcode = f"EXP_{uuid.uuid4().hex[:6].upper()}"
    expired_time = datetime.datetime.now() - datetime.timedelta(days=2)
    
    # 1. Lưu voucher hết hạn vào DB
    v = Voucher(
        code=vcode,
        title="Voucher Đã Hết Hạn",
        kind="amount",
        value=20000,
        min_order=50000,
        badge="Hết hạn",
        expire_in="Đã hết hạn",
        expires_at=expired_time,
        max_uses=100,
        max_uses_per_user=1,
    )
    db_service.save_voucher(v)

    # 2. Kiểm tra API validate voucher
    r_val = client.post("/api/vouchers/validate", json={"code": vcode, "subtotal": 200000})
    assert r_val.status_code == 200
    assert r_val.json()["valid"] is False
    assert "hết hạn" in r_val.json()["message"].lower()

    # 3. Kiểm tra API quote
    r_quote = client.post("/api/orders/quote", json={
        "items": [item("prod_001", qty=1)],
        "voucher_code": vcode
    })
    assert r_quote.status_code == 200
    assert r_quote.json()["voucher_discount"] == 0
    assert "hết hạn" in r_quote.json()["voucher_message"].lower()

    # 4. Kiểm tra API tạo đơn -> Phải chặn 400
    req_order = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "voucher_code": vcode,
    }
    r_order = client.post("/api/orders", json=req_order)
    assert r_order.status_code == 400
    assert "hết hạn" in r_order.json()["detail"].lower()


def test_voucher_max_uses_system_exceeded(client: TestClient):
    """Test voucher đã dùng hết số lượt toàn hệ thống (max_uses) bị từ chối 400."""
    vcode = f"MAXSYS_{uuid.uuid4().hex[:6].upper()}"
    v = Voucher(
        code=vcode,
        title="Voucher 1 Lượt Hệ Thống",
        kind="amount",
        value=30000,
        min_order=50000,
        badge="1 Lượt Duy Nhất",
        expire_in="Còn hiệu lực",
        expires_at=datetime.datetime.now() + datetime.timedelta(days=1),
        max_uses=1,
        max_uses_per_user=1,
    )
    db_service.save_voucher(v)

    # Đơn 1: Guest 1 đặt đơn dùng voucher thành công
    req_1 = {
        **CUSTOMER,
        "customer_phone": "0911000111",
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "voucher_code": vcode,
    }
    r1 = client.post("/api/orders", json=req_1)
    assert r1.status_code == 200

    # Đơn 2: Guest 2 với SĐT khác thử đặt dùng cùng voucher -> bị chặn vì hết lượt hệ thống
    req_2 = {
        **CUSTOMER,
        "customer_phone": "0922000222",
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "voucher_code": vcode,
    }
    r2 = client.post("/api/orders", json=req_2)
    assert r2.status_code == 400
    assert "hết lượt sử dụng trên hệ thống" in r2.json()["detail"].lower()


def test_voucher_guest_same_phone_reuse_rejected_and_refund_on_cancel(client: TestClient):
    """Test khách vãng lai dùng lại cùng SĐT (kể cả định dạng +84) bị chặn 400, và được hoàn lượt khi đơn bị huỷ."""
    vcode = f"GUEST_{uuid.uuid4().hex[:6].upper()}"
    v = Voucher(
        code=vcode,
        title="Ưu đãi mỗi khách 1 lần",
        kind="amount",
        value=25000,
        min_order=50000,
        badge="Mỗi khách 1 lần",
        expire_in="Còn hiệu lực",
        expires_at=datetime.datetime.now() + datetime.timedelta(days=2),
        max_uses=10,
        max_uses_per_user=1,
    )
    db_service.save_voucher(v)

    phone_guest = "0988776655"
    req_1 = {
        **CUSTOMER,
        "customer_name": "Khách Vãng Lai A",
        "customer_phone": phone_guest,
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "voucher_code": vcode,
    }

    # Lần 1: Thành công
    r1 = client.post("/api/orders", json=req_1)
    assert r1.status_code == 200
    order_id_1 = r1.json()["order_id"]

    # Lần 2: Cùng SĐT (dùng định dạng quốc tế +84 988 776 655) -> Phải bị chặn 400 do hết 1 lượt của user/phone
    req_2 = {
        **CUSTOMER,
        "customer_name": "Khách Vãng Lai A (Mua tiếp)",
        "customer_phone": "+84 988 776 655",
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "voucher_code": vcode,
    }
    r2 = client.post("/api/orders", json=req_2)
    assert r2.status_code == 400
    assert "hết 1 lượt" in r2.json()["detail"].lower()

    # Khách vãng lai khác (SĐT khác) vẫn dùng được
    req_other = {
        **CUSTOMER,
        "customer_name": "Khách Vãng Lai B",
        "customer_phone": "0912999888",
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "voucher_code": vcode,
    }
    r_other = client.post("/api/orders", json=req_other)
    assert r_other.status_code == 200

    # Hủy đơn hàng 1 của Khách A -> Hoàn lượt dùng voucher
    cancel_success = db_service.cancel_order_atomic(order_id_1)
    assert cancel_success is True

    # Khách A dùng lại SĐT 0988776655 đặt lại đơn -> Thành công!
    r3 = client.post("/api/orders", json=req_1)
    assert r3.status_code == 200
    assert r3.json()["order_id"] != order_id_1


def test_combo_quantity_99_capped_to_one_set(client: TestClient, monkeypatch):
    """Test giới hạn combo_token chỉ giảm tối đa cho 1 set (số lượng mỗi món 99 chỉ được giảm 1 lần)."""
    p1 = product_service.get_by_id("prod_001")
    p2 = product_service.get_by_id("prod_002")
    assert p1 and p2

    token = make_combo_token([p1.id, p2.id])

    # Nới lỏng tạm thời MAX_QTY_PER_LINE và stock cho bài test quantity 99
    monkeypatch.setattr(settings, "MAX_QTY_PER_LINE", 100)
    orig_stock1 = p1.stock
    orig_stock2 = p2.stock
    p1.stock = 500
    p2.stock = 500

    try:
        items_payload = [
            {"product_id": p1.id, "color": p1.colors[0].name, "size": p1.sizes[0], "quantity": 99, "combo_token": token},
            {"product_id": p2.id, "color": p2.colors[0].name, "size": p2.sizes[0], "quantity": 99, "combo_token": token},
        ]

        # 1. Kiểm tra quote
        r_quote = client.post("/api/orders/quote", json={"items": items_payload})
        assert r_quote.status_code == 200
        q = r_quote.json()

        # Tổng giá trị giỏ hàng (99 cái mỗi món)
        expected_subtotal = (p1.final_price * 99) + (p2.final_price * 99)
        assert q["subtotal"] == expected_subtotal

        # Giảm giá combo CHỈ tính trên 1 set (1 cái mỗi món)
        one_set_amount = p1.final_price + p2.final_price
        expected_combo_discount = one_set_amount * settings.COMBO_DISCOUNT_PERCENT // 100

        # Khẳng định: chỉ giảm tối đa cho 1 set duy nhất!
        assert q["combo_discount"] == expected_combo_discount

        # Khẳng định: không bị nhân lên 99 lần
        uncapped_discount = expected_subtotal * settings.COMBO_DISCOUNT_PERCENT // 100
        assert q["combo_discount"] < uncapped_discount
        assert q["combo_discount"] == uncapped_discount // 99 or q["combo_discount"] == expected_combo_discount

    finally:
        p1.stock = orig_stock1
        p2.stock = orig_stock2
