"""
Test suite cho trang Xác nhận đơn hàng (/order-success/{order_id}):
1. Đơn hàng hợp lệ (COD) hiển thị 200, mã đơn, thông tin nhận hàng, ghi chú tiền mặt COD.
2. Đơn hàng không tồn tại -> redirect về trang chủ '/' (302).
3. Đơn hàng đã thanh toán qua VNPay -> hiển thị badge 'Đã thanh toán thành công' màu xanh.
4. VNPay return URL khi trình duyệt truy cập -> redirect 302 về /order-success/{order_id}.
5. Route /tracking redirect đúng định dạng query param sang trang chủ.
"""
import pytest
from app.config import settings
from tests.conftest import CUSTOMER, item
from tests.test_payment_vnpay import _generate_vnpay_params


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

    # Kiểm tra các thông tin chính
    assert order_id in html
    assert CUSTOMER["customer_name"] in html
    assert CUSTOMER["customer_phone"].replace(" ", "") in html
    assert "12 Phố Huế" in html
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


def test_order_success_vnpay_paid_order(client):
    """Đơn hàng đã thanh toán VNPay: hiển thị badge và thông báo 'Đã thanh toán thành công'."""
    # 1. Tạo đơn hàng VNPay
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # 2. Giả lập callback thành công từ VNPay
    vnp_params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")
    ret_res = client.get("/api/payment/vnpay/return", params=vnp_params, headers={"Accept": "application/json"})
    assert ret_res.status_code == 200
    assert ret_res.json()["payment_status"] == "paid"

    # 3. Truy cập trang xác nhận đơn hàng
    res = client.get(f"/order-success/{order_id}")
    assert res.status_code == 200
    html = res.text

    assert order_id in html
    assert "Đã thanh toán thành công" in html
    assert "Cổng VNPay" in html or "VNPay" in html
    # Không hiển thị lưu ý chuẩn bị tiền mặt COD
    assert "Vui lòng chuẩn bị" not in html


def test_vnpay_return_redirects_browser_to_order_success(client):
    """Khi trình duyệt (không gửi Accept: application/json) hoàn tất VNPay -> redirect 302 về /order-success/{order_id}."""
    order_req = {
        **CUSTOMER,
        "payment_method": "vnpay",
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    vnp_params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")
    
    # Request giả lập trình duyệt (Accept: text/html,...)
    res = client.get(
        "/api/payment/vnpay/return",
        params=vnp_params,
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        follow_redirects=False
    )
    assert res.status_code == 302
    assert res.headers["location"] == f"/order-success/{order_id}"


def test_tracking_route_redirects(client):
    """GET /tracking?code=... chuyển hướng về /?tracking=..."""
    res = client.get("/tracking?code=GHN-VN-123456", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/?tracking=GHN-VN-123456"

    res_empty = client.get("/tracking", follow_redirects=False)
    assert res_empty.status_code == 302
    assert res_empty.headers["location"] == "/?tracking="
