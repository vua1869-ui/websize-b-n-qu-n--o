"""
Kiểm thử địa giới hành chính 2 cấp (Nghị quyết 202/2025/QH15) và quy trình đặt hàng:
1. Endpoint GET /api/locations trả về đủ 34 tỉnh/thành, mỗi tỉnh có ít nhất 1 ward.
2. Kiểm tra server validate: ward_code không thuộc province_code đã chọn -> bị từ chối 422.
3. Đặt hàng hợp lệ với province/ward thật từ dữ liệu đã nạp -> lưu địa chỉ đầy đủ dạng chuỗi hiển thị.
"""
import pytest
from app.db.database import db_service
from tests.conftest import CUSTOMER, item


def test_get_locations_34_provinces_and_wards(client):
    """GET /api/locations trả về đủ 34 tỉnh/thành phố, mỗi tỉnh có danh sách xã/phường/đặc khu."""
    r = client.get("/api/locations")
    assert r.status_code == 200
    locations = r.json()
    assert isinstance(locations, list)
    assert len(locations) == 34, f"Cả nước phải có đúng 34 tỉnh/thành phố sau sáp nhập, hiện tại có {len(locations)}"

    for prov in locations:
        assert "code" in prov and "name" in prov and "wards" in prov
        assert isinstance(prov["code"], int)
        assert len(prov["name"]) > 0
        wards = prov["wards"]
        assert isinstance(wards, list)
        assert len(wards) >= 1, f"Tỉnh {prov['name']} phải có ít nhất 1 xã/phường"
        for w in wards:
            assert "code" in w and "name" in w
            assert isinstance(w["code"], int)
            assert len(w["name"]) > 0


def test_order_rejected_when_ward_does_not_belong_to_province(client):
    """Server validate: ward_code không thuộc province_code đã chọn -> trả mã lỗi 422 rõ ràng bằng tiếng Việt."""
    loc_res = client.get("/api/locations")
    assert loc_res.status_code == 200
    locations = loc_res.json()
    assert len(locations) >= 2

    # Lấy tỉnh 1 và tỉnh 2
    prov1 = locations[0]
    prov2 = locations[1]
    # Lấy một xã/phường thuộc tỉnh 2 gán vào tỉnh 1
    ward_of_prov2 = prov2["wards"][0]

    order_payload = {
        "customer_name": "Nguyễn Hoàng Nam",
        "customer_phone": "0912345678",
        "province_code": prov1["code"],
        "ward_code": ward_of_prov2["code"],  # Sai: ward của tỉnh 2 gán cho tỉnh 1
        "specific_address": "Số 45 Đại lộ Hòa Bình",
        "payment_method": "cod",
        "items": [item("prod_001", qty=1)]
    }

    r = client.post("/api/orders", json=order_payload)
    assert r.status_code == 422
    data = r.json()
    assert "detail" in data
    assert "không thuộc" in data["detail"].lower()

    # Thử với mã ward không tồn tại
    order_payload_invalid_code = {
        "customer_name": "Nguyễn Hoàng Nam",
        "customer_phone": "0912345678",
        "province_code": prov1["code"],
        "ward_code": 99999999,  # Mã không tồn tại
        "specific_address": "Số 45 Đại lộ Hòa Bình",
        "payment_method": "cod",
        "items": [item("prod_001", qty=1)]
    }
    r2 = client.post("/api/orders", json=order_payload_invalid_code)
    assert r2.status_code == 422
    assert "detail" in r2.json()


def test_order_valid_location_persists_full_display_address(client):
    """Đặt hàng với province/ward thật lấy từ dữ liệu -> lưu địa chỉ đầy đủ dạng chuỗi hiển thị vào bản ghi đơn."""
    loc_res = client.get("/api/locations")
    assert loc_res.status_code == 200
    locations = loc_res.json()

    # Chọn tỉnh và xã/phường thật từ dữ liệu
    target_prov = locations[0]
    target_ward = target_prov["wards"][0]
    street_text = "Số 108 Phố Cổ, Tầng 3 Tòa nhà Sunrise"

    order_payload = {
        "customer_name": "Trần Thị Mai",
        "customer_phone": "0987654321",
        "province_code": target_prov["code"],
        "ward_code": target_ward["code"],
        "specific_address": street_text,
        "payment_method": "cod",
        "items": [item("prod_002", qty=1)]
    }

    r = client.post("/api/orders", json=order_payload)
    assert r.status_code == 200
    res = r.json()
    assert "order_id" in res
    order_id = res["order_id"]

    # Kiểm tra bản ghi đơn hàng trong cơ sở dữ liệu
    saved_order = db_service.get_order_by_id(order_id)
    assert saved_order is not None
    cust = saved_order["customer"]
    assert cust["province"] == target_prov["name"]
    assert cust["ward"] == target_ward["name"]

    expected_full_address = f"{street_text}, {target_ward['name']}, {target_prov['name']}"
    assert cust["address"] == expected_full_address
    assert saved_order["customer_address"] == expected_full_address
