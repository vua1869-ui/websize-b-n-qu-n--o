import pytest
from app.db.database import db_service, get_db_connection
from tests.conftest import CUSTOMER, item


def test_product_reviews_api(client):
    # 1. Lấy danh sách đánh giá của sản phẩm prod_001
    r = client.get("/api/products/prod_001/reviews")
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data and "reviews" in data
    summary = data["summary"]
    assert summary["total_reviews"] > 0
    assert 1.0 <= summary["average_rating"] <= 5.0
    assert "rating_breakdown" in summary
    assert "5" in summary["rating_breakdown"] or 5 in summary["rating_breakdown"]

    # 2. Kiểm tra thông tin người đánh giá có số đo thật
    reviews = data["reviews"]
    assert len(reviews) > 0
    first_rev = reviews[0]
    assert "user_name" in first_rev
    assert "rating" in first_rev
    assert "comment" in first_rev
    assert "is_verified_buyer" in first_rev
    assert first_rev["is_verified_buyer"] == 1 or first_rev["is_verified_buyer"] is True

    # 3. Lọc theo số sao
    r_filtered = client.get("/api/products/prod_001/reviews?rating=5")
    assert r_filtered.status_code == 200
    for rev in r_filtered.json()["reviews"]:
        assert rev["rating"] == 5


def test_add_product_review(client):
    review_payload = {
        "user_name": "Lê Kiều Trang",
        "rating": 5,
        "comment": "Áo mặc cực kỳ ưng ý, vải linen đanh và thoáng, mặc mùa hè rất dễ chịu!",
        "height_cm": 163.5,
        "weight_kg": 49.0,
        "purchased_size": "S",
        "purchased_color": "Be / Kem",
        "fit_feedback": "Vừa vặn"
    }
    r = client.post("/api/products/prod_001/reviews", json=review_payload)
    assert r.status_code == 200
    res = r.json()
    assert res["success"] is True
    assert "review" in res
    created = res["review"]
    assert created["user_name"] == "Lê Kiều Trang"
    assert created["rating"] == 5
    assert created["height_cm"] == 163.5
    assert created["weight_kg"] == 49.0

    # Kiểm tra lại qua GET reviews
    r_check = client.get("/api/products/prod_001/reviews")
    all_revs = r_check.json()["reviews"]
    assert any(rev["user_name"] == "Lê Kiều Trang" for rev in all_revs)


def test_size_chart_api(client):
    # 1. Bảng size áo blazer (prod_001)
    r1 = client.get("/api/products/prod_001/size-chart")
    assert r1.status_code == 200
    data1 = r1.json()
    assert "columns" in data1 and "rows" in data1
    assert "measuring_guide" in data1 and "care_instructions" in data1
    assert any("ngực" in col.lower() or "vai" in col.lower() for col in data1["columns"])
    assert len(data1["rows"]) >= 3

    # 2. Bảng size quần jeans (prod_006)
    r2 = client.get("/api/products/prod_006/size-chart")
    assert r2.status_code == 200
    data2 = r2.json()
    assert any("eo" in col.lower() or "mông" in col.lower() for col in data2["columns"])

    # 3. Bảng size váy đầm (prod_004: Đầm Lụa Maxi)
    r3 = client.get("/api/products/prod_004/size-chart")
    assert r3.status_code == 200
    data3 = r3.json()
    assert any("vòng 1" in col.lower() or "v1" in col.lower() for col in data3["columns"])


def test_order_logistics_and_public_tracking(client):
    # 1. Tạo đơn hàng mới
    order_req = {
        **CUSTOMER,
        "customer_phone": "0988776655",
        "payment_method": "cod",
        "province": "Thành phố Hồ Chí Minh",
        "district": "Quận 1",
        "ward": "Phường Bến Nghé",
        "specific_address": "Số 99 Lê Lợi",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_data = r.json()
    order_id = order_data["order_id"]
    tracking_code = order_data["tracking_code"]

    assert tracking_code is not None and "GHN" in tracking_code
    assert "carrier" in order_data and "GHN" in order_data["carrier"]
    assert "estimated_delivery" in order_data

    # 2. Tra cứu bằng order_id
    r_track1 = client.get(f"/api/orders/track/{order_id}")
    assert r_track1.status_code == 200
    tr1 = r_track1.json()
    assert tr1["order_id"] == order_id
    assert tr1["tracking_code"] == tracking_code
    assert "timeline" in tr1 and len(tr1["timeline"]) == 6
    assert tr1["timeline"][0]["key"] == "ordered"
    assert tr1["timeline"][0]["status"] == "completed"

    # 3. Tra cứu bằng tracking_code
    r_track2 = client.get(f"/api/orders/track/{tracking_code}")
    assert r_track2.status_code == 200
    tr2 = r_track2.json()
    assert tr2["order_id"] == order_id

    # 4. Tra cứu bằng số điện thoại người nhận
    r_track3 = client.get("/api/orders/track/0988776655")
    assert r_track3.status_code == 200
    tr3 = r_track3.json()
    assert tr3["order_id"] == order_id


def test_order_invoice_and_notification(client):
    # 1. Tạo đơn hàng
    order_req = {
        **CUSTOMER,
        "customer_name": "Trần Thu Hà",
        "customer_phone": "0912998877",
        "payment_method": "qr_transfer",
        "items": [item("prod_002", qty=2)]
    }
    order_data = client.post("/api/orders", json=order_req).json()
    order_id = order_data["order_id"]

    # 2. Lấy dữ liệu hóa đơn điện tử E-Invoice
    r_inv = client.get(f"/api/orders/{order_id}/invoice")
    assert r_inv.status_code == 200
    inv = r_inv.json()
    assert "invoice_number" in inv and "INV-AURA" in inv["invoice_number"]
    assert "seller" in inv and inv["seller"]["tax_code"] == "0317894562"
    assert "buyer" in inv and inv["buyer"]["name"] == "Trần Thu Hà"
    assert "vat_rate" in inv and inv["vat_rate"] == 8
    assert inv["vat_amount"] > 0
    assert inv["total_amount"] > 0
    assert len(inv["items"]) > 0

    # 3. Giả lập gửi thông báo đơn hàng qua Zalo / SMS
    r_notify = client.post(f"/api/orders/{order_id}/send-notification?channel=zalo")
    assert r_notify.status_code == 200
    notif = r_notify.json()
    assert notif["success"] is True
    assert notif["channel"] == "zalo"
    assert "thành công" in notif["message"].lower()
