import hashlib
import hmac
import json
import pytest
from app.config import settings
from app.db.database import db_service, get_db_connection
from app.services.geo_service import geo_service
from tests.conftest import CUSTOMER, item


def test_sqlite_wal_and_pragmas():
    conn = get_db_connection()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1
    finally:
        conn.close()


def test_geo_provinces_districts_wards(client):
    # 1. 63 tỉnh/thành
    r = client.get("/api/geo/provinces")
    assert r.status_code == 200
    provinces = r.json()
    assert len(provinces) == 63
    assert any("Hà Nội" in (p if isinstance(p, str) else p.get("name", "")) for p in provinces)

    # 2. Quận/Huyện của Hà Nội (mã "01")
    r = client.get("/api/geo/districts?province_code=01")
    assert r.status_code == 200
    districts = r.json()
    assert len(districts) > 0
    assert any("Ba Đình" in (d if isinstance(d, str) else d.get("name", "")) for d in districts)

    # 3. Phường/Xã của Ba Đình (mã "001")
    r = client.get("/api/geo/wards?district_code=001")
    assert r.status_code == 200
    wards = r.json()
    assert len(wards) > 0
    assert any("Phường" in (w if isinstance(w, str) else w.get("name", "")) for w in wards)


def test_product_variants_endpoint(client):
    r = client.get("/api/products/prod_001/variants")
    assert r.status_code == 200
    variants = r.json()
    assert isinstance(variants, list) and len(variants) > 0
    first = variants[0]
    assert "color" in first and "size" in first and "stock" in first and "sku" in first


def test_vietqr_creation_and_polling_and_simulate_webhook(client):
    # 1. Tạo đơn hàng chọn thanh toán qr_transfer
    order_req = {
        **CUSTOMER,
        "payment_method": "qr_transfer",
        "province": "Thành phố Hà Nội",
        "district": "Quận Ba Đình",
        "ward": "Phường Phúc Xá",
        "specific_address": "Số 12 phố X",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    data = r.json()
    order_id = data["order_id"]
    assert data["status"] == "pending_payment"
    assert data["payment_status"] == "unpaid"
    assert "qr_code_url" in data and "vietqr.io" in data["qr_code_url"]
    assert "bank_info" in data and data["bank_info"]["account_number"] == "0900000001"

    # 2. Kiểm tra status qua Polling endpoint
    r = client.get(f"/api/payment/check-status/{order_id}")
    assert r.status_code == 200
    poll_data = r.json()
    assert poll_data["order_id"] == order_id
    assert poll_data["status"] == "unpaid"

    # 3. Giả lập thanh toán Webhook thành công (simulate-success cần quyền admin)
    admin_res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_token = admin_res.json()["token"]
    r = client.post(f"/api/payment/simulate-success/{order_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    sim_data = r.json()
    assert sim_data["status"] == "paid"
    assert sim_data["order_status"] == "confirmed"

    # 4. Kiểm tra lại status qua Polling -> phải là paid ngay lập tức
    r = client.get(f"/api/payment/check-status/{order_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "paid"


def test_webhook_endpoint_processing(client):
    # Tạo đơn mới
    order_req = {
        **CUSTOMER,
        "payment_method": "qr_transfer",
        "items": [item("prod_002", qty=1)]
    }
    order = client.post("/api/orders", json=order_req).json()
    order_id = order["order_id"]
    total = order["quote"]["total"]

    # Gửi webhook thanh toán ngân hàng (VD SePay / Casso / VietQR IPN) với chữ ký HMAC-SHA256
    webhook_payload = {
        "content": f"AURA {order_id}",
        "transferAmount": total,
        "referenceCode": f"FT{order_id.replace('-', '')}",
    }
    raw_body = json.dumps(webhook_payload).encode("utf-8")
    sig = hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/payment/webhook",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Signature": sig}
    )
    assert r.status_code == 200
    res = r.json()
    assert res["success"] is True
    assert res["order_id"] == order_id

    # Đơn hàng phải chuyển sang confirmed / paid
    poll = client.get(f"/api/payment/check-status/{order_id}").json()
    assert poll["status"] == "paid"
