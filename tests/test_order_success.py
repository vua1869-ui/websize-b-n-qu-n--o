"""
Test suite cho trang Xác nhận đơn hàng (/order-success/{order_id}):
1. Đơn hàng hợp lệ (COD) hiển thị 200, mã đơn, thông tin nhận hàng, ghi chú tiền mặt COD.
2. Đơn hàng không tồn tại -> redirect về trang chủ '/' (302)."""
from tests.conftest import CUSTOMER, item


def test_order_success_valid_cod_order(client):
    """Đơn hàng COD hợp lệ: trả HTTP 200, hiển thị mã đơn, người nhận, địa chỉ và thông báo chuẩn bị tiền mặt."""
    order_req = {
        **CUSTOMER,
        "payment_method": "cod",
        "customer_note": "Giao giờ hành chính giúp mình",
        "items": [item("prod_001", qty=2)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_data = r.json()
    order_id = order_data["order_id"]

    # Truy cập trang xác nhận đơn hàng
    res = client.get(f"/order-success/{order_id}")
    assert res.status_code == 200
    html = res.text

    # Kiểm tra các thông tin chính (Đơn vãng lai che bớt PII: 098****321 và tỉnh thành)
    assert order_id in html
    assert CUSTOMER["customer_name"] in html
    assert "098****321" in html
    assert "Hà Nội" in html
    assert "Giao giờ hành chính" in html

    # Kiểm tra trạng thái thanh toán COD: phải ghi rõ thanh toán khi nhận hàng / chuẩn bị tiền mặt
    assert "Thanh toán khi nhận hàng" in html or "COD" in html
    assert "Vui lòng chuẩn bị" in html or "tiền mặt khi nhận hàng" in html
    # Không được báo sai là "Đã thanh toán thành công"
    assert "Đã thanh toán thành công" not in html

    # Nút điều hướng
    assert "Theo dõi đơn hàng" in html
    assert "Tiếp tục mua sắm" in html
    assert f"/?tracking={order_id}" in html


def test_order_success_nonexistent_order_redirects(client):
    """Mã đơn hàng không tồn tại -> chuyển hướng 302 về trang chủ '/'."""
    res = client.get("/order-success/AURA-FAKE-NOTFOUND", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/"


def test_tracking_page_renders(client):
    """GET /tracking trả về trang tra cứu đơn hàng công khai (200 OK)."""
    res = client.get("/tracking", follow_redirects=False)
    assert res.status_code == 200
    assert "Tra cứu đơn hàng" in res.text
