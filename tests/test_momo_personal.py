import pytest
from app.config import settings
from tests.conftest import CUSTOMER, item

def test_momo_personal_create_order_and_flow(client):
    # 1. Đặt hàng với phương thức momo
    order_payload = {
        **CUSTOMER,
        "payment_method": "momo",
        "items": [item("prod_001", qty=1)]
    }
    res = client.post("/api/orders", json=order_payload)
    assert res.status_code == 200, res.text
    data = res.json()
    order_id = data["order_id"]
    assert data["payment_method"] == "momo"
    assert data["payment_status"] == "unpaid"
    assert order_id in data["transfer_content"]
    assert f"/payment/mbank/{order_id}" in data["redirect_url"]
    assert "bank_info" in data

    # 2. Trang hiển thị MB Bank qua redirect từ /payment/momo/{order_id}
    page_res = client.get(f"/payment/momo/{order_id}", follow_redirects=True)
    assert page_res.status_code == 200
    assert "MB Bank" in page_res.text or "MB BANK" in page_res.text
    assert "0835731473" in page_res.text
    assert "VU TIEN ANH" in page_res.text

    # 3. Khách bấm 'Kiểm tra' KHI CHƯA CHUYỂN TIỀN:
    # Tuyệt đối không cho phép chuyển trang ảo (is_paid = False, redirect_url = None)
    confirm_res = client.post(f"/api/orders/{order_id}/confirm-momo")
    assert confirm_res.status_code == 200
    confirm_data = confirm_res.json()
    assert confirm_data["is_paid"] is False
    assert confirm_data["redirect_url"] is None
    assert "chưa ghi nhận tiền" in confirm_data["message"].lower() or "chưa ghi nhận" in confirm_data["message"]

    # 4. Kiểm tra API Auto-polling khi chưa có tiền
    poll_res = client.get(f"/api/payment/momo/status/{order_id}")
    assert poll_res.status_code == 200
    assert poll_res.json()["is_paid"] is False

    # 5. Kích hoạt nhận tiền (Webhook hoặc simulate)
    sim_res = client.post(f"/api/payment/momo/simulate-pay/{order_id}")
    assert sim_res.status_code == 200
    assert sim_res.json()["success"] is True

    # 6. Kiểm tra lại API Auto-polling sau khi nhận tiền: lập tức is_paid = True và có redirect_url
    poll_res_paid = client.get(f"/api/payment/momo/status/{order_id}")
    assert poll_res_paid.status_code == 200
    assert poll_res_paid.json()["is_paid"] is True
    assert f"/order-success/{order_id}" in poll_res_paid.json()["redirect_url"]

    # 7. Khách bấm kiểm tra sau khi đã thanh toán: được duyệt và chuyển sang order-success
    confirm_res_paid = client.post(f"/api/orders/{order_id}/confirm-momo")
    assert confirm_res_paid.status_code == 200
    assert confirm_res_paid.json()["is_paid"] is True
    assert f"/order-success/{order_id}" in confirm_res_paid.json()["redirect_url"]

    # 8. Kiểm tra trang Order Success
    success_page = client.get(f"/order-success/{order_id}?phone=0987654321")
    assert success_page.status_code == 200
    assert "Đã thanh toán thành công" in success_page.text or "Ví MoMo" in success_page.text


def test_momo_sepay_webhook_auto_payment(client):
    """Test webhook tự động bắt biến động số dư SePay với đầy đủ các trường theo đặc tả SePay."""
    # Tạo đơn mới
    order_payload = {
        **CUSTOMER,
        "payment_method": "momo",
        "items": [item("prod_001", qty=1)]
    }
    res = client.post("/api/orders", json=order_payload)
    order_id = res.json()["order_id"]
    total_amount = res.json()["amount"]

    # 1. Giả lập SePay webhook bắn về server với đầy đủ các trường chính thức
    webhook_payload = {
        "id": 92704,
        "gateway": "MBBank",
        "transactionDate": "2026-10-20 18:35:00",
        "accountNumber": "0775972171",
        "subAccount": None,
        "transferType": "in",
        "transferAmount": total_amount,
        "accumulated": 15000000,
        "code": None,
        "content": f"AURA {order_id} chuyen khoan mua quan ao",
        "referenceCode": "FT261020123456",
        "description": f"Giao dich tu MBBank AURA {order_id}",
    }
    wh_res = client.post("/api/payment/sepay/webhook", json=webhook_payload)
    assert wh_res.status_code == 200
    wh_data = wh_res.json()
    assert wh_data["success"] is True
    assert wh_data["payment_status"] == "paid"
    assert wh_data["order_id"] == order_id

    # 2. Kiểm tra trong Database: đơn hàng đã chuyển thành 'paid'
    poll_res = client.get(f"/api/payment/momo/status/{order_id}")
    assert poll_res.json()["is_paid"] is True
    assert poll_res.json()["payment_status"] == "paid"

    # 3. Test giao dịch tiền ra (transferType = "out") được bỏ qua và trả success: true
    out_payload = {
        "id": 92705,
        "gateway": "MBBank",
        "transferType": "out",
        "transferAmount": 50000,
        "content": "Rut tien mat ATM",
    }
    out_res = client.post("/api/payment/sepay/webhook", json=out_payload)
    assert out_res.status_code == 200
    assert out_res.json()["success"] is True
