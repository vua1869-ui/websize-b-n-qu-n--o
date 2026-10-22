import concurrent.futures
import datetime
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.database import db_service, get_db_transaction, normalize_phone
from app.db.models import OrderDB, ProductVariantDB
from app.db.session import engine, get_db_session
from app.models.schemas import OrderCreateRequest
from app.services.order_service import order_service
from app.services.product_service import product_service
from app.services.stock_reconcile import reconcile_stock
from app.services.user_service import user_service
from tests.conftest import CUSTOMER, item


@pytest.fixture(scope="module", autouse=True)
def drain_initial_backlog():
    """Xả sạch backlog các đơn demo quá hạn có sẵn trong DB đúng 1 lần cho cả module test."""
    for _ in range(50):
        expired = order_service.expire_unpaid_orders(ttl_minutes=1, batch=500)
        if not expired:
            break


# ============================================================================
# BÀI TOÁN 1 — ĐƠN THANH TOÁN ONLINE BỊ BỎ DỞ LÀM KẸT KHO
# ============================================================================
def test_bt1_expire_unpaid_orders_success(client: TestClient):
    p_initial = product_service.get_by_id("prod_001")
    initial_stock = p_initial.stock

    # Tạo đơn MB Bank (pending_payment, unpaid)
    req = OrderCreateRequest(
        payment_method="mbank",
        items=[item("prod_001", qty=2)],
        **CUSTOMER,
    )
    res = client.post("/api/orders", json=req.model_dump())
    assert res.status_code == 200
    oid = res.json()["order_id"]

    # Kho đã bị trừ 2
    assert product_service.get_by_id("prod_001").stock == initial_stock - 2

    # Giả lập thời gian quá hạn (35 phút trước)
    past_time = (datetime.datetime.now() - datetime.timedelta(minutes=35)).isoformat()
    with get_db_session() as session:
        o = session.query(OrderDB).filter(OrderDB.order_id == oid).first()
        o.created_at = past_time

    # Chạy expire_unpaid_orders
    expired = order_service.expire_unpaid_orders(ttl_minutes=30)
    assert oid in expired

    # Kiểm tra đơn đã bị hủy và tồn kho được hoàn
    cancelled_order = db_service.get_order_by_id(oid)
    assert cancelled_order["order_status"] == "cancelled"
    assert "Tự động hủy" in (cancelled_order.get("internal_note") or "")

    # Tồn kho RAM và DB về lại số ban đầu
    assert product_service.get_by_id("prod_001").stock == initial_stock
    with get_db_session() as session:
        from app.db.models import ProductDB
        pdb = session.query(ProductDB).filter(ProductDB.id == "prod_001").first()
        assert pdb.stock == initial_stock


def test_bt1_orders_not_expired_when_recent_or_cod_or_paid(client: TestClient):
    unique_phone = f"09{uuid.uuid4().int % 100000000:08d}"
    cust = {**CUSTOMER, "customer_phone": unique_phone}

    # 1. Đơn MB Bank mới tạo (chưa quá hạn)
    req_mbank = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **cust)
    res1 = client.post("/api/orders", json=req_mbank.model_dump())
    assert res1.status_code == 200
    oid_mbank = res1.json()["order_id"]

    # 2. Đơn COD (không bao giờ tự hủy do chưa thanh toán online)
    req_cod = OrderCreateRequest(payment_method="cod", items=[item("prod_001", qty=1)], **cust)
    res2 = client.post("/api/orders", json=req_cod.model_dump())
    assert res2.status_code == 200
    oid_cod = res2.json()["order_id"]

    # 3. Đơn đã thanh toán (paid)
    req_paid = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **cust)
    res3 = client.post("/api/orders", json=req_paid.model_dump())
    assert res3.status_code == 200
    oid_paid = res3.json()["order_id"]
    db_service.confirm_payment(oid_paid, amount=res3.json()["amount"], transaction_code="TX_PAID_123", payment_channel="mbank")

    # Đặt thời gian tạo về 60 phút trước cho đơn COD và Paid
    past_time = (datetime.datetime.now() - datetime.timedelta(minutes=60)).isoformat()
    with get_db_session() as session:
        for o in session.query(OrderDB).filter(OrderDB.order_id.in_([oid_cod, oid_paid])):
            o.created_at = past_time

    # Chạy expire với TTL=30
    expired = order_service.expire_unpaid_orders(ttl_minutes=30)
    assert oid_mbank not in expired
    assert oid_cod not in expired
    assert oid_paid not in expired


def test_bt1_race_condition_guard_against_paid_order(client: TestClient):
    req = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **CUSTOMER)
    res = client.post("/api/orders", json=req.model_dump())
    oid = res.json()["order_id"]

    # Khách thanh toán thành công
    db_service.confirm_payment(oid, amount=res.json()["amount"], transaction_code="TX_RACE_456", payment_channel="mbank")

    # Tác vụ tự hủy chạy ngay sau đó với only_if_unpaid_pending=True
    cancelled = db_service.cancel_order_atomic(oid, only_if_unpaid_pending=True)
    assert cancelled is False

    order = db_service.get_order_by_id(oid)
    assert order["payment_status"] == "paid"
    assert order["order_status"] != "cancelled"


def test_bt1_ttl_zero_does_nothing(client: TestClient):
    expired = order_service.expire_unpaid_orders(ttl_minutes=0)
    assert expired == []


def test_bt1_admin_expire_endpoint(client: TestClient):
    # Khách vãng lai gọi -> 401 hoặc 403
    client.cookies.clear()
    res_guest = client.post("/api/admin/orders/expire-unpaid")
    assert res_guest.status_code in (401, 403)

    # Đăng nhập Admin hợp lệ
    admin_u = user_service.get_by_username("admin")
    assert admin_u is not None
    token = user_service.create_session_token(admin_u)
    client.cookies.set("aura_session", token)

    res_admin = client.post("/api/admin/orders/expire-unpaid")
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert data["success"] is True
    assert "expired_count" in data
    assert "order_ids" in data
    client.cookies.clear()


# ============================================================================
# BÀI TOÁN 2 — BẤM "ĐẶT HÀNG" NHIỀU LẦN / MẠNG RETRY (IDEMPOTENCY)
# ============================================================================
def test_bt2_idempotency_same_key_same_order(client: TestClient):
    key = f"idem_{uuid.uuid4().hex}"
    p_init = product_service.get_by_id("prod_001").stock

    req = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **CUSTOMER)
    headers = {"Idempotency-Key": key}

    # Request 1
    res1 = client.post("/api/orders", json=req.model_dump(), headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert "Idempotent-Replayed" not in res1.headers

    # Request 2 (cùng key, cùng nội dung)
    res2 = client.post("/api/orders", json=req.model_dump(), headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert res2.headers.get("Idempotent-Replayed") == "true"
    assert data1["order_id"] == data2["order_id"]

    # Kho chỉ bị trừ 1 lần
    p_after = product_service.get_by_id("prod_001").stock
    assert p_init - p_after == 1


def test_bt2_idempotency_same_key_different_body_422(client: TestClient):
    key = f"idem_{uuid.uuid4().hex}"
    req1 = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **CUSTOMER)
    res1 = client.post("/api/orders", json=req1.model_dump(), headers={"Idempotency-Key": key})
    assert res1.status_code == 200

    # Cùng key nhưng khác items/số lượng
    req2 = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=2)], **CUSTOMER)
    res2 = client.post("/api/orders", json=req2.model_dump(), headers={"Idempotency-Key": key})
    assert res2.status_code == 422
    assert "Idempotency-Key" in res2.json()["detail"]


def test_bt2_idempotency_without_key_creates_different_orders(client: TestClient):
    req = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **CUSTOMER)
    res1 = client.post("/api/orders", json=req.model_dump())
    res2 = client.post("/api/orders", json=req.model_dump())
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res1.json()["order_id"] != res2.json()["order_id"]


def test_bt2_idempotency_invalid_key_format_400(client: TestClient):
    req = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **CUSTOMER)
    res = client.post("/api/orders", json=req.model_dump(), headers={"Idempotency-Key": "short"})
    assert res.status_code == 400


def test_bt2_idempotency_first_failed_retry_same_key_succeeds(client: TestClient):
    key = f"idem_{uuid.uuid4().hex}"
    curr_stock = product_service.get_by_id("prod_001").stock

    # Lần 1: gửi số lượng vượt quá tồn kho hiện có nhưng <= 99 (lỗi 409 từ create_order)
    over_stock_qty = min(99, max(curr_stock + 1, 2))
    p = product_service.get_by_id("prod_001")
    old_stock = p.stock
    p.stock = 1  # Đặt tạm tồn kho = 1 để qty=5 chắc chắn bị lỗi 409

    try:
        req_fail = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=5)], **CUSTOMER)
        res_fail = client.post("/api/orders", json=req_fail.model_dump(), headers={"Idempotency-Key": key})
        assert res_fail.status_code == 409
    finally:
        p.stock = old_stock

    # Lần 2: sửa giỏ hàng và gửi lại với CÙNG key -> phải thành công
    req_ok = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **CUSTOMER)
    res_ok = client.post("/api/orders", json=req_ok.model_dump(), headers={"Idempotency-Key": key})
    assert res_ok.status_code == 200


def test_bt2_idempotency_concurrent_requests(client: TestClient):
    key = f"idem_{uuid.uuid4().hex}"
    p_init = product_service.get_by_id("prod_001").stock
    req = OrderCreateRequest(payment_method="mbank", items=[item("prod_001", qty=1)], **CUSTOMER)
    body_json = req.model_dump()

    def send_order():
        return client.post("/api/orders", json=body_json, headers={"Idempotency-Key": key})

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(send_order) for _ in range(6)]
        results = [f.result() for f in futures]

    order_ids = set()
    for r in results:
        assert r.status_code == 200
        order_ids.add(r.json()["order_id"])

    # Chỉ duy nhất 1 order_id được sinh ra
    assert len(order_ids) == 1
    # Kho chỉ bị trừ đúng 1 lần
    assert p_init - product_service.get_by_id("prod_001").stock == 1


# ============================================================================
# BÀI TOÁN 3 — ĐƠN "MỒ CÔI" KHI THIẾU CẤU HÌNH THANH TOÁN
# ============================================================================
def test_bt3_missing_bank_config_no_orphan_order(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "BANK_ID", "")
    p_init = product_service.get_by_id("prod_001").stock

    with get_db_session() as session:
        count_before = session.query(OrderDB).count()

    req = OrderCreateRequest(payment_method="qr_transfer", items=[item("prod_001", qty=1)], **CUSTOMER)
    res = client.post("/api/orders", json=req.model_dump())
    assert res.status_code == 400
    assert "chưa sẵn sàng" in res.json()["detail"]

    # Đơn không được tạo và kho không bị trừ
    with get_db_session() as session:
        count_after = session.query(OrderDB).count()
        assert count_after == count_before
    assert product_service.get_by_id("prod_001").stock == p_init


# ============================================================================
# BÀI TOÁN 4 — "database is locked" KHI NHIỀU NGƯỜI CÙNG GHI
# ============================================================================
def test_bt4_sqlalchemy_sqlite_pragmas_and_timeout():
    if "sqlite" in str(engine.url):
        from sqlalchemy import text
        with engine.connect() as conn:
            wal = conn.execute(text("PRAGMA journal_mode;")).scalar()
            assert wal.lower() == "wal"


# ============================================================================
# BÀI TOÁN 5 — PHÂN TRANG KHÔNG GIỚI HẠN
# ============================================================================
def test_bt5_auth_orders_pagination_bounds(client: TestClient):
    normal_u = user_service.get_by_username("user") or user_service.get_by_id("usr_001")
    assert normal_u is not None
    token = user_service.create_session_token(normal_u)
    client.cookies.set("aura_session", token)

    # limit = 100000 -> 422
    res_too_large = client.get("/api/auth/orders?limit=100000")
    assert res_too_large.status_code == 422

    # limit = 0 -> 422
    res_zero = client.get("/api/auth/orders?limit=0")
    assert res_zero.status_code == 422

    # limit = 100 -> 200
    res_ok = client.get("/api/auth/orders?limit=100")
    assert res_ok.status_code == 200
    assert isinstance(res_ok.json(), list)

    client.cookies.clear()


# ============================================================================
# BÀI TOÁN 7 — LỆCH TỒN KHO GIỮA RAM VÀ DB
# ============================================================================
def test_bt7_stock_reconcile_and_apply(client: TestClient):
    pid = "prod_001"
    p = product_service.get_by_id(pid)

    with get_db_session() as session:
        vars_sum = session.query(ProductVariantDB).filter(ProductVariantDB.product_id == pid).all()
        expected_sum = sum(v.stock for v in vars_sum)

    # Tạo lệch nhân tạo: cập nhật products.stock = 9999 trong DB
    with get_db_transaction(db_service.db_path) as conn:
        conn.execute("UPDATE products SET stock = 9999 WHERE id = ?;", (pid,))

    # Đối soát (chỉ đọc) -> phát hiện lệch
    report = reconcile_stock(apply_fix=False)
    assert report["has_mismatch"] is True
    assert any(m["product_id"] == pid for m in report["product_variant_mismatches"])

    # Áp dụng fix (--apply)
    fix_report = reconcile_stock(apply_fix=True, admin_id="test_admin")
    assert fix_report["fixed"] is True

    # DB và RAM đều về đúng tổng variant
    with get_db_transaction(db_service.db_path) as conn:
        row = conn.execute("SELECT stock FROM products WHERE id = ?;", (pid,)).fetchone()
        assert row["stock"] == expected_sum

    assert product_service.get_by_id(pid).stock == expected_sum

    # Khôi phục lại
    db_service.seed_reset_stock()
    product_service._load_products()


# ============================================================================
# BÀI TOÁN 8 — GỬI EMAIL LÀM CHẬM REQUEST ĐẶT ĐƠN
# ============================================================================
def test_bt8_background_email_does_not_block_order(client: TestClient):
    mock_sender = MagicMock()

    def slow_send(*args, **kwargs):
        time.sleep(2.0)
        raise RuntimeError("SMTP connection timeout!")

    mock_sender.send_order_confirmation.side_effect = slow_send

    unique_phone = f"09{uuid.uuid4().int % 100000000:08d}"
    with patch("app.services.email_sender.get_email_sender", return_value=mock_sender):
        req = OrderCreateRequest(
            payment_method="cod",
            items=[item("prod_001", qty=1)],
            customer_name="Khách Hàng Email",
            customer_phone=unique_phone,
            customer_email="fast@example.com",
            customer_address="123 Phố Huế, Hà Nội",
        )
        start_t = time.time()
        res = client.post("/api/orders", json=req.model_dump())
        duration = time.time() - start_t

        assert res.status_code == 200
        # Request phải phản hồi ngay lập tức (< 1.5 giây), không bị chặn 2 giây bởi email
        assert duration < 1.5


# ============================================================================
# BÀI TOÁN 9 — CHỐNG LẠM DỤNG COD VÀ FLASH SALE
# ============================================================================
def test_bt9_cod_abuse_prevention(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "COD_MAX_OPEN_PER_PHONE", 3)
    test_phone = f"09{uuid.uuid4().int % 100000000:08d}"

    # Đặt 3 đơn COD liên tiếp (ngưỡng tối đa mặc định là 3)
    last_oid = None
    for _ in range(3):
        req = OrderCreateRequest(
            payment_method="cod",
            items=[item("prod_001", qty=1)],
            customer_name="Người Dùng COD",
            customer_phone=test_phone,
            customer_address="123 Đường Test COD, Quận 1",
        )
        res = client.post("/api/orders", json=req.model_dump())
        assert res.status_code == 200
        last_oid = res.json()["order_id"]

    # Đơn COD thứ 4 cùng số điện thoại -> bị từ chối 429
    req_4th = OrderCreateRequest(
        payment_method="cod",
        items=[item("prod_001", qty=1)],
        customer_name="Người Dùng COD",
        customer_phone=test_phone,
        customer_address="123 Đường Test COD, Quận 1",
    )
    res_4th = client.post("/api/orders", json=req_4th.model_dump())
    assert res_4th.status_code == 429
    assert "đang chờ giao" in res_4th.json()["detail"]

    # Hủy bớt 1 đơn -> đặt lại được
    assert last_oid is not None
    db_service.cancel_order_atomic(last_oid)

    res_retry = client.post("/api/orders", json=req_4th.model_dump())
    assert res_retry.status_code == 200


def test_bt9_flash_sale_qty_limit(client: TestClient, monkeypatch):
    pid = "prod_001"
    p = product_service.get_by_id(pid)
    now = datetime.datetime.now(datetime.timezone.utc)
    monkeypatch.setattr(p, "flash_sale", True)
    monkeypatch.setattr(p, "flash_sale_price", 99000)
    monkeypatch.setattr(p, "flash_sale_start", now - datetime.timedelta(hours=1))
    monkeypatch.setattr(p, "flash_sale_end", now + datetime.timedelta(hours=1))

    assert p.is_in_flash_sale is True

    test_phone = "0977 888 999"

    # 1. Đặt vượt quá giới hạn trong 1 đơn (qty=3 > max 2) -> 409
    req_over = OrderCreateRequest(
        payment_method="mbank",
        items=[item(pid, qty=3)],
        customer_name="Khách Flash Sale",
        customer_phone=test_phone,
        customer_address="789 Đường Flash Sale, Hà Nội",
    )
    res_over = client.post("/api/orders", json=req_over.model_dump())
    assert res_over.status_code == 409
    assert "tối đa 2" in res_over.json()["detail"]

    # 2. Đặt đơn 1 với qty=2 -> 200 thành công
    req_ok = OrderCreateRequest(
        payment_method="mbank",
        items=[item(pid, qty=2)],
        customer_name="Khách Flash Sale",
        customer_phone=test_phone,
        customer_address="789 Đường Flash Sale, Hà Nội",
    )
    res_ok = client.post("/api/orders", json=req_ok.model_dump())
    assert res_ok.status_code == 200
    oid = res_ok.json()["order_id"]

    # 3. Đặt thêm đơn 2 trong cùng ngày -> 409
    req_again = OrderCreateRequest(
        payment_method="mbank",
        items=[item(pid, qty=1)],
        customer_name="Khách Flash Sale",
        customer_phone=test_phone,
        customer_address="789 Đường Flash Sale, Hà Nội",
    )
    res_again = client.post("/api/orders", json=req_again.model_dump())
    assert res_again.status_code == 409

    # 4. Khi đơn bị hủy -> không còn tính vào giới hạn và được mua lại
    db_service.cancel_order_atomic(oid)
    res_after_cancel = client.post("/api/orders", json=req_again.model_dump())
    assert res_after_cancel.status_code == 200


# ============================================================================
# BÀI TOÁN 10 — SAO LƯU TỰ ĐỘNG VÀ GIÁM SÁT (/healthz)
# ============================================================================
def test_bt10_healthz_monitoring_fields(client: TestClient):
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert "pending_payment_count" in data
    assert "oldest_pending_payment_age_minutes" in data
    assert "last_backup_at" in data
    assert isinstance(data["pending_payment_count"], int)
