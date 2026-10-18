import hashlib
import hmac
import json
import pytest
from app.config import settings
from app.db.database import db_service, get_db_connection
from app.services.order_service import order_service
from tests.conftest import CUSTOMER, item


def _send_webhook(client, order_id: str, amount: int, tx_code: str = "TX123456"):
    payload = {
        "order_id": order_id,
        "amount": amount,
        "transaction_code": tx_code,
        "channel": "vietqr",
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/payment/webhook",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )


def test_webhook_cancelled_order_records_transaction_and_refund_pending(client):
    """
    Kịch bản 1: Đơn đã hủy mà webhook nhận được tiền:
    - Vẫn ghi nhận payment_transactions với trạng thái 'unmatched_or_cancelled'.
    - Cập nhật payment_status = 'refund_pending'.
    - Giữ nguyên order_status = 'cancelled'.
    - Có chỗ cho admin thấy danh sách cần hoàn tiền (get_refund_pending_orders / /api/admin/orders/refund-pending).
    """
    # 1. Tạo đơn hàng pending_payment
    order_req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": "vnpay",
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total_amount = r.json()["quote"]["total"]

    # 2. Hủy đơn hàng (ví dụ do hết hạn hoặc khách hủy)
    order_service.update_order_status(order_id, "cancelled")
    order_before = db_service.get_order_by_id(order_id)
    assert order_before["order_status"] == "cancelled"

    # 3. Webhook nhận được tiền thanh toán cho đơn đã hủy
    tx_code = f"TX_CANCELLED_{order_id}"
    res = _send_webhook(client, order_id, total_amount, tx_code=tx_code)
    assert res.status_code == 200
    res_data = res.json()
    assert res_data.get("status") == "refund_pending" or "hoàn tiền" in res_data.get("message", "").lower()

    # 4. Kiểm tra CSDL:
    order_after = db_service.get_order_by_id(order_id)
    assert order_after["order_status"] == "cancelled", "order_status không được bị ghi đè khỏi 'cancelled'"
    assert order_after["payment_status"] == "refund_pending", "payment_status phải là 'refund_pending'"

    # Kiểm tra bảng payment_transactions có lưu giao dịch với status = 'unmatched_or_cancelled'
    conn = get_db_connection(db_service.db_path)
    try:
        tx = conn.execute(
            "SELECT * FROM payment_transactions WHERE order_id = ? AND transaction_code = ?;",
            (order_id, tx_code),
        ).fetchone()
        assert tx is not None, "Phải lưu giao dịch vào bảng payment_transactions"
        assert tx["status"] == "unmatched_or_cancelled"
    finally:
        conn.close()

    # 5. Kiểm tra danh sách cần hoàn tiền cho Admin
    refund_list = db_service.get_refund_pending_orders()
    assert any(o["order_id"] == order_id for o in refund_list), "Đơn hàng phải xuất hiện trong danh sách cần hoàn tiền của admin"


def test_webhook_shipping_order_does_not_overwrite_status(client):
    """
    Kịch bản 2: Đơn đang ở trạng thái 'shipping':
    - Webhook thanh toán đến chỉ cập nhật payment_status = 'paid' và paid_at.
    - KHÔNG ĐƯỢC ghi đè order_status về 'confirmed' hay shipping_status về 'ready_to_pick'.
    - Mọi đổi trạng thái phải tuân thủ VALID_STATUS_TRANSITIONS.
    """
    # 1. Tạo đơn COD
    order_req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total_amount = r.json()["quote"]["total"]

    # 2. Chuyển đơn sang 'shipping'
    order_service.update_order_status(order_id, "shipping")
    order_before = db_service.get_order_by_id(order_id)
    assert order_before["order_status"] == "shipping"
    assert order_before["shipping_status"] == "in_transit"

    # 3. Webhook thanh toán gửi đến (ví dụ khách thanh toán chuyển khoản bổ sung khi shipper giao)
    tx_code = f"TX_SHIPPING_{order_id}"
    res = _send_webhook(client, order_id, total_amount, tx_code=tx_code)
    assert res.status_code == 200

    # 4. Kiểm tra: order_status và shipping_status KHÔNG bị lùi về 'confirmed' / 'ready_to_pick'
    order_after = db_service.get_order_by_id(order_id)
    assert order_after["order_status"] == "shipping", "order_status phải giữ nguyên là 'shipping'"
    assert order_after["shipping_status"] == "in_transit", "shipping_status phải giữ nguyên là 'in_transit'"
    assert order_after["payment_status"] == "paid", "payment_status phải được cập nhật thành 'paid'"
    assert order_after.get("paid_at") is not None, "paid_at phải được ghi nhận"


def test_webhook_idempotent_duplicate_call(client):
    """
    Kịch bản 3: Webhook gọi lặp lại 2 lần:
    - Lần 1: Xác nhận thành công.
    - Lần 2 (cùng tx_code, cùng order_id): Idempotent, không bị lỗi 500, không cộng điểm/doanh thu 2 lần.
    """
    order_req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": "vnpay",
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total_amount = r.json()["quote"]["total"]

    tx_code = f"TX_IDEMPOTENT_{order_id}"

    # Gửi webhook lần 1
    res1 = _send_webhook(client, order_id, total_amount, tx_code=tx_code)
    assert res1.status_code == 200

    # Kiểm tra trạng thái đã paid
    order_1 = db_service.get_order_by_id(order_id)
    assert order_1["payment_status"] == "paid"

    # Gửi webhook lần 2 (lặp lại y hệt)
    res2 = _send_webhook(client, order_id, total_amount, tx_code=tx_code)
    assert res2.status_code == 200, f"Webhook gọi lặp phải trả về 200 OK, thực tế: {res2.status_code}"

    # Đảm bảo không tạo thêm dòng duplicate trong payment_transactions
    conn = get_db_connection(db_service.db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM payment_transactions WHERE order_id = ? AND transaction_code = ?;",
            (order_id, tx_code),
        ).fetchone()[0]
        assert count == 1, f"Chỉ được có đúng 1 bản ghi giao dịch trong CSDL, thực tế: {count}"
    finally:
        conn.close()
