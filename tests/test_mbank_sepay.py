import pytest
from app.config import settings
from tests.conftest import CUSTOMER, item

BANK_ACCOUNT_NUMBER = "0000000000"
BANK_ACCOUNT_NAME = "TEST ACCOUNT"


def configure_mbank_test_settings(monkeypatch):
    monkeypatch.setattr(settings, "MBBANK_ACCOUNT_NUMBER", BANK_ACCOUNT_NUMBER)
    monkeypatch.setattr(settings, "MBBANK_ACCOUNT_NAME", BANK_ACCOUNT_NAME)
    monkeypatch.setattr(settings, "MBBANK_BANK_NAME", "MB Bank")
    monkeypatch.setattr(settings, "MBBANK_BANK_CODE", "MB")
    monkeypatch.setattr(settings, "MBBANK_QR_IMAGE", "")


def test_mbank_create_order_and_flow(client, monkeypatch):
    """
    Kiểm tra toàn bộ luồng Chuyển khoản Ngân hàng MB Bank (Tự động xác nhận qua SePay):
    1. Đặt hàng với payment_method = 'mbank'
    2. Kiểm tra thông tin STK, Chủ TK, Số tiền, Cú pháp chuyển khoản AURA-{order_id}
    3. Kiểm tra trang thanh toán /payment/mbank/{order_id} và file ảnh QR
    4. Kiểm tra API Polling 3s /api/orders/{order_id}/status
    5. Giả lập SePay webhook bắn về và kiểm tra trạng thái chuyển paid ngay lập tức
    """
    # 1. Đặt hàng với phương thức mbank
    configure_mbank_test_settings(monkeypatch)
    order_payload = {
        **CUSTOMER,
        "payment_method": "mbank",
        "items": [item("prod_001", qty=1)]
    }
    res = client.post("/api/orders", json=order_payload)
    assert res.status_code == 200, res.text
    data = res.json()
    order_id = data["order_id"]
    total_amount = data["amount"]

    assert data["payment_method"] == "mbank"
    assert data["payment_status"] == "unpaid"
    assert data["status"] == "pending_payment"
    assert data["bank_account_number"] == BANK_ACCOUNT_NUMBER
    assert data["bank_account_name"] == BANK_ACCOUNT_NAME
    assert "MB Bank" in data["bank_name"] or "Quân Đội" in data["bank_name"]
    assert order_id in data["transfer_content"]
    assert data["redirect_url"] == f"/payment/mbank/{order_id}"
    assert "bank_info" in data
    assert data["bank_info"]["account_number"] == BANK_ACCOUNT_NUMBER

    # 2. Kiểm tra trang giao diện MB Bank /payment/mbank/{order_id}
    page_res = client.get(f"/payment/mbank/{order_id}")
    assert page_res.status_code == 200
    assert "MB Bank" in page_res.text or "MB BANK" in page_res.text
    assert BANK_ACCOUNT_NUMBER in page_res.text
    assert BANK_ACCOUNT_NAME in page_res.text
    assert order_id in page_res.text
    assert f"img.vietqr.io/image/MB-{BANK_ACCOUNT_NUMBER}" in page_res.text
    assert "Copy" in page_res.text
    assert "/api/orders/" in page_res.text

    # 4. Kiểm tra API Polling ngầm của Frontend (/api/orders/{order_id}/status) khi chưa thanh toán
    poll_res = client.get(f"/api/orders/{order_id}/status")
    assert poll_res.status_code == 200
    poll_data = poll_res.json()
    assert poll_data["success"] is True
    assert poll_data["order_id"] == order_id
    assert poll_data["payment_status"] == "unpaid"
    assert poll_data["is_paid"] is False

    # 5. Khách bấm kiểm tra thủ công khi chưa có tiền
    confirm_res = client.post(f"/api/orders/{order_id}/confirm-mbank")
    assert confirm_res.status_code == 200
    confirm_data = confirm_res.json()
    assert confirm_data["is_paid"] is False
    assert confirm_data["redirect_url"] is None
    assert "chưa ghi nhận tiền" in confirm_data["message"].lower() or "chưa ghi nhận" in confirm_data["message"]

    # 6. SePay bắn webhook thông báo tiền thật đã vào tài khoản MB Bank
    webhook_payload = {
        "id": 99991,
        "gateway": "MBBank",
        "transactionDate": "2026-10-20 19:15:00",
        "accountNumber": BANK_ACCOUNT_NUMBER,
        "transferType": "in",
        "transferAmount": total_amount,
        "accumulated": 25000000,
        "content": f"AURA-{order_id}",
        "referenceCode": "FT261020888999",
        "description": f"Chuyen tien don AURA-{order_id}",
    }
    wh_res = client.post("/api/payment/sepay/webhook", json=webhook_payload)
    assert wh_res.status_code == 200
    wh_data = wh_res.json()
    assert wh_data["success"] is True
    assert wh_data["payment_status"] == "paid"
    assert wh_data["order_id"] == order_id

    # 7. Kiểm tra lại Polling Frontend: ngay lập tức trả is_paid = True và có redirect_url sang success.html
    poll_res_paid = client.get(f"/api/orders/{order_id}/status")
    assert poll_res_paid.status_code == 200
    poll_paid_data = poll_res_paid.json()
    assert poll_paid_data["is_paid"] is True
    assert poll_paid_data["payment_status"] == "paid"
    assert f"/order-success/{order_id}" in poll_paid_data["redirect_url"]

    # 8. Khách bấm kiểm tra sau khi đã thanh toán: is_paid = True
    confirm_res_paid = client.post(f"/api/orders/{order_id}/confirm-mbank")
    assert confirm_res_paid.status_code == 200
    assert confirm_res_paid.json()["is_paid"] is True
    assert f"/order-success/{order_id}" in confirm_res_paid.json()["redirect_url"]

    # 9. Kiểm tra trang Order Success hiển thị đúng thông tin
    success_page = client.get(f"/order-success/{order_id}?phone=0987654321")
    assert success_page.status_code == 200
    assert "Đã thanh toán thành công" in success_page.text or "MB Bank" in success_page.text


def test_banking_method_alias(client, monkeypatch):
    """Kiểm tra phương thức 'banking' hợp lệ trong VALID_PAYMENT_METHODS."""
    configure_mbank_test_settings(monkeypatch)
    order_payload = {
        **CUSTOMER,
        "payment_method": "banking",
        "items": [item("prod_001", qty=1)]
    }
    res = client.post("/api/orders", json=order_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["payment_method"] == "banking"
    assert data["bank_account_number"] == BANK_ACCOUNT_NUMBER
    assert data["redirect_url"] == f"/payment/mbank/{data['order_id']}"
