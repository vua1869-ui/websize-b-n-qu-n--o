import pytest
from app.db.database import db_service
from app.services.product_service import product_service
from tests.conftest import CUSTOMER, item


def test_denial_of_inventory_reproduction(client):
    """
    Test tái hiện lỗ hổng Denial of Inventory:
    1. Kẻ tấn công gửi 20 đơn pending_payment (VNPay/QR) mà không thanh toán.
       (8 đơn x 2sp + 12 đơn x 1sp = 28 sản phẩm bị giam).
    2. Kho của prod_001 bị giam trừ dần từ 32 xuống 4.
    3. Khách hàng thật vào mua 5 sản phẩm bị từ chối (409 Conflict) vì kho chỉ còn 4.
    4. Không có trường expires_at cho đơn pending_payment, kho bị giam vĩnh viễn.
    5. Không có rate-limit theo IP hoặc SĐT trên POST /api/orders (cả 20 đơn đều tạo thành công tức thì từ cùng 1 IP/SĐT).
    """
    # 1. Kiểm tra tồn ban đầu của prod_001
    prod = product_service.get_by_id("prod_001")
    initial_stock = prod.stock
    assert initial_stock == 32, f"Tồn kho ban đầu của prod_001 là 32 (thực tế: {initial_stock})"

    attacker_phone = "0900000001"
    created_order_ids = []

    # 2. Tạo 20 đơn hàng pending_payment liên tiếp từ cùng 1 IP / SĐT (không hề bị rate-limit)
    # 8 đơn đầu x 2 sp, 12 đơn sau x 1 sp => tổng cộng 28 sản phẩm
    for i in range(20):
        qty = 2 if i < 8 else 1
        req_body = {
            "customer_name": f"Attacker {i}",
            "customer_phone": attacker_phone,
            "customer_address": "123 Fraud St, Hanoi",
            "items": [item("prod_001", qty=qty)],
            "payment_method": "vnpay",
        }
        res = client.post("/api/orders", json=req_body)
        assert res.status_code == 200, f"Đơn thứ {i+1} tạo thất bại: {res.text}"
        data = res.json()
        assert data["status"] == "pending_payment"
        created_order_ids.append(data["order_id"])

    # 3. Chứng minh kho bị giam xuống còn 4
    current_stock = product_service.get_by_id("prod_001").stock
    assert current_stock == 4, f"Kho phải bị trừ từ 32 xuống 4, thực tế: {current_stock}"

    # 4. Khách hàng thật muốn mua 5 sản phẩm -> Bị từ chối (409 Conflict) do kho bị giam chỉ còn 4
    legitimate_res = client.post("/api/orders", json={
        **CUSTOMER,
        "customer_phone": "0988888888",
        "items": [item("prod_001", qty=5)],
        "payment_method": "cod",
    })
    assert legitimate_res.status_code == 409
    err_detail = legitimate_res.json()["detail"]
    assert "chỉ còn 4 sản phẩm" in err_detail or "hết hàng" in err_detail

    # 5. Chứng minh tất cả 20 đơn pending_payment này KHÔNG có trường expires_at trong CSDL
    for order_id in created_order_ids:
        order_db = db_service.get_order_by_id(order_id)
        assert order_db is not None
        assert order_db.get("order_status") == "pending_payment"
        # Hiện tại chưa có cột expires_at hoặc giá trị là None (chưa được quản lý hết hạn)
        assert "expires_at" not in order_db or order_db.get("expires_at") is None
