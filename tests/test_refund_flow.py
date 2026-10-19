"""
Kiểm thử toàn diện quy trình hoàn tiền khi khách hàng hủy đơn đã thanh toán:
1. Ghi nhận refund_requests đầy đủ thông tin khi hủy đơn đã thanh toán.
2. Từ chối hoàn tiền cho đơn chưa thanh toán (400).
3. Từ chối hoàn tiền với số tiền vượt quá số tiền đã thanh toán hoặc <= 0 (400).
4. Chống hoàn tiền hai lần / idempotency (409 Conflict).
5. Hoàn tiền chuyển khoản thủ công (VietQR / bank transfer) không giả lập tự động chuyển.
6. MoMo / VNPay: Bắt buộc cấu hình hợp lệ và mã giao dịch gốc; xử lý thành công và thất bại rõ ràng.
7. Hiển thị đúng trạng thái (refund_pending, refunded, refund_failed) cho khách hàng và admin.
"""
import uuid
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import db_service, get_db_connection
from tests.conftest import CUSTOMER, item

client = TestClient(app)


def get_admin_headers():
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def create_and_pay_order(payment_method="vietqr", trans_code=None):
    """Tạo đơn và chuyển sang trạng thái đã thanh toán (paid) với transaction_code duy nhất"""
    req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": payment_method,
    }
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200, r.text
    order_data = r.json()
    order_id = order_data["order_id"]
    total = order_data["quote"]["total"]

    unique_tx = f"{trans_code}_{uuid.uuid4().hex[:8]}" if trans_code else f"TRANS_{uuid.uuid4().hex[:12]}"
    # Đánh dấu đã thanh toán và lưu payment_transaction
    db_service.confirm_payment(
        order_id,
        amount=total,
        transaction_code=unique_tx,
        payment_channel=payment_method,
    )
    return order_id, total


class TestRefundFlow:

    def test_cancel_paid_order_creates_refund_request_record(self):
        """Khi khách hủy đơn đã thanh toán, ghi nhận refund_requests với status pending."""
        order_id, total = create_and_pay_order("vietqr", trans_code="VQR_CANCEL")

        cancel_req = {
            "order_id": order_id,
            "phone": CUSTOMER["customer_phone"],
            "reason": "Đổi ý không mua nữa",
            "refund_bank_name": "Vietcombank",
            "refund_account_number": "1234567890",
            "refund_account_holder": "NGUYEN VAN A",
        }
        res = client.post(f"/api/orders/{order_id}/cancel", json=cancel_req)
        assert res.status_code == 200, res.text

        order = db_service.get_order_by_id(order_id)
        assert order["status"] == "cancelled"
        assert order["payment_status"] == "refund_pending"

        # Kiểm tra bảng refund_requests
        refund_req = db_service.get_refund_request(order_id)
        assert refund_req is not None
        assert refund_req["order_id"] == order_id
        assert refund_req["amount"] == total
        assert refund_req["payment_channel"] == "vietqr"
        assert refund_req["status"] == "pending"
        assert refund_req["refund_bank_name"] == "Vietcombank"
        assert refund_req["refund_account_number"] == "1234567890"
        assert refund_req["refund_account_holder"] == "NGUYEN VAN A"
        assert refund_req["reason"] == "Đổi ý không mua nữa"

    def test_refund_unpaid_order_rejected(self):
        """Không thể hoàn tiền cho đơn chưa thanh toán (400 Bad Request)."""
        admin_headers = get_admin_headers()
        req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "cod"}
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200
        order_id = r.json()["order_id"]

        res = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": True}
        )
        assert res.status_code == 400
        assert "chưa thanh toán" in res.json()["detail"].lower()

    def test_refund_exceeding_amount_rejected(self):
        """Không cho phép hoàn số tiền lớn hơn số tiền đã thanh toán hoặc <= 0 (400)."""
        admin_headers = get_admin_headers()
        order_id, total = create_and_pay_order("vietqr")
        # Huỷ đơn để thành refund_pending
        client.post(f"/api/orders/{order_id}/cancel", json={
            "order_id": order_id,
            "phone": CUSTOMER["customer_phone"],
            "refund_bank_name": "VCB",
            "refund_account_number": "123456",
            "refund_account_holder": "AAA"
        })

        # Hoàn tiền <= 0
        r_zero = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": True, "amount": 0}
        )
        assert r_zero.status_code == 400

        # Hoàn tiền vượt quá total
        r_excess = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": True, "amount": total + 50000}
        )
        assert r_excess.status_code == 400
        assert "vượt quá" in r_excess.json()["detail"].lower()

    def test_double_refund_rejected_with_409(self):
        """Chống hoàn tiền lặp / hai lần: sau khi đã hoàn thành công, yêu cầu tiếp theo trả về 409 Conflict."""
        admin_headers = get_admin_headers()
        order_id, _ = create_and_pay_order("vietqr")
        client.post(f"/api/orders/{order_id}/cancel", json={
            "order_id": order_id,
            "phone": CUSTOMER["customer_phone"],
            "refund_bank_name": "VCB",
            "refund_account_number": "123456",
            "refund_account_holder": "AAA"
        })

        # Lần hoàn 1 -> thành công
        r1 = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": True, "refund_trans_id": f"REF_{uuid.uuid4().hex[:8]}"}
        )
        assert r1.status_code == 200
        order_after_r1 = db_service.get_order_by_id(order_id)
        assert order_after_r1["payment_status"] == "refunded"

        # Lần hoàn 2 -> bị từ chối 409
        r2 = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": True}
        )
        assert r2.status_code == 409
        assert "đã được hoàn tiền" in r2.json()["detail"].lower()

    def test_manual_bank_transfer_refund_flow(self):
        """Chuyển khoản thủ công: Admin xác nhận đã chuyển tiền, lưu processed_by và mã đối soát."""
        admin_headers = get_admin_headers()
        order_id, total = create_and_pay_order("vietqr", trans_code="VQR_ORIGINAL")
        client.post(f"/api/orders/{order_id}/cancel", json={
            "order_id": order_id,
            "phone": CUSTOMER["customer_phone"],
            "refund_bank_name": "MBBank",
            "refund_account_number": "99998888",
            "refund_account_holder": "TRAN THI B"
        })

        ref_id = f"MB_REFUND_{uuid.uuid4().hex[:6]}"
        res = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": True, "refund_trans_id": ref_id}
        )
        assert res.status_code == 200
        assert res.json()["payment_status"] == "refunded"
        assert res.json()["refund_trans_id"] == ref_id

        refund_req = db_service.get_refund_request(order_id)
        assert refund_req["status"] == "success"
        assert refund_req["refund_trans_id"] == ref_id
        assert refund_req["processed_by"] == "admin"
        assert refund_req["processed_at"] is not None

    def test_vnpay_refund_missing_trans_code_or_unconfigured(self):
        """VNPay: Báo lỗi nếu thiếu mã giao dịch gốc hoặc chưa cấu hình gateway."""
        admin_headers = get_admin_headers()
        req = {
            **CUSTOMER,
            "items": [item("prod_001", qty=1)],
            "payment_method": "vnpay",
        }
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200
        order_id = r.json()["order_id"]
        # Đánh dấu refund_pending nhưng không có payment_transactions hay refund_requests
        with get_db_connection(db_service.db_path) as conn:
            conn.execute("UPDATE orders SET payment_status = 'refund_pending', order_status = 'cancelled' WHERE order_id = ?", (order_id,))
            conn.commit()

        res = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": False}
        )
        # Bị từ chối vì thiếu mã giao dịch gốc từ VNPay
        assert res.status_code == 400
        assert "thiếu mã giao dịch" in res.json()["detail"].lower()

    def test_momo_refund_failure_marks_refund_failed(self):
        """MoMo: Khi gateway phản hồi thất bại, trạng thái đơn chuyển thành refund_failed và lưu error_message."""
        admin_headers = get_admin_headers()
        order_id, _ = create_and_pay_order("momo", trans_code="MOMO_TRANS_FAIL")
        client.post(f"/api/orders/{order_id}/cancel", json={
            "order_id": order_id,
            "phone": CUSTOMER["customer_phone"],
            "refund_bank_name": "MoMo",
            "refund_account_number": "0987654321",
            "refund_account_holder": "NGUYEN VAN A",
        })

        with patch("app.routers.admin.momo_service.is_configured", return_value=True), \
             patch("app.routers.admin.momo_service.refund", return_value={"success": False, "message": "Số dư tài khoản doanh nghiệp không đủ"}):

            res = client.post(
                f"/api/admin/orders/{order_id}/process-refund",
                headers=admin_headers,
                json={"is_manual": False}
            )
            assert res.status_code == 400
            assert "số dư tài khoản doanh nghiệp không đủ" in res.json()["detail"].lower()

        # Kiểm tra trạng thái trong DB
        order = db_service.get_order_by_id(order_id)
        assert order["payment_status"] == "refund_failed"

        refund_req = db_service.get_refund_request(order_id)
        assert refund_req["status"] == "failed"
        assert "số dư tài khoản doanh nghiệp không đủ" in refund_req["error_message"].lower()

    def test_momo_refund_success_flow(self):
        """MoMo: Khi gateway hoàn tiền thành công, trạng thái chuyển sang refunded và lưu refund_trans_id."""
        admin_headers = get_admin_headers()
        order_id, _ = create_and_pay_order("momo", trans_code="MOMO_TRANS_OK")
        client.post(f"/api/orders/{order_id}/cancel", json={
            "order_id": order_id,
            "phone": CUSTOMER["customer_phone"],
            "refund_bank_name": "MoMo",
            "refund_account_number": "0987654321",
            "refund_account_holder": "NGUYEN VAN A",
        })

        success_ref_id = f"MOMO_REF_{uuid.uuid4().hex[:8]}"
        with patch("app.routers.admin.momo_service.is_configured", return_value=True), \
             patch("app.routers.admin.momo_service.refund", return_value={"success": True, "refund_trans_id": success_ref_id, "message": "Thành công"}):

            res = client.post(
                f"/api/admin/orders/{order_id}/process-refund",
                headers=admin_headers,
                json={"is_manual": False}
            )
            assert res.status_code == 200
            assert res.json()["payment_status"] == "refunded"
            assert res.json()["refund_trans_id"] == success_ref_id

        order = db_service.get_order_by_id(order_id)
        assert order["payment_status"] == "refunded"
        assert order["refund_trans_id"] == success_ref_id

        refund_req = db_service.get_refund_request(order_id)
        assert refund_req["status"] == "success"
        assert refund_req["refund_trans_id"] == success_ref_id
        assert refund_req["processed_by"] == "admin"

    def test_status_visibility_in_tracking_and_admin(self):
        """Khách hàng và admin đều thấy chính xác trạng thái chờ hoàn tiền, thất bại và đã hoàn tiền."""
        admin_headers = get_admin_headers()
        order_id, _ = create_and_pay_order("vietqr")
        phone = CUSTOMER["customer_phone"]

        # 1. Hủy -> Chờ hoàn tiền
        r_cancel = client.post(f"/api/orders/{order_id}/cancel", json={
            "order_id": order_id,
            "phone": phone,
            "refund_bank_name": "ACB",
            "refund_account_number": "12345678",
            "refund_account_holder": "NGUYEN VAN A"
        })
        assert r_cancel.status_code == 200, r_cancel.text

        # Tra cứu tracking
        r_track1 = client.get(f"/api/orders/{order_id}?phone={phone}")
        assert r_track1.status_code == 200
        assert r_track1.json()["payment_status"] == "refund_pending"

        # Tra cứu admin
        r_admin_orders = client.get("/api/admin/orders", headers=admin_headers)
        assert r_admin_orders.status_code == 200
        target = next((x for x in r_admin_orders.json() if x["order_id"] == order_id), None)
        assert target is not None
        assert target["payment_status"] == "refund_pending"

        # 2. Đánh dấu refund_failed
        db_service.mark_order_refund_failed(order_id, "Lỗi kết nối cổng thanh toán", "admin")
        r_track2 = client.get(f"/api/orders/{order_id}?phone={phone}")
        assert r_track2.status_code == 200
        assert r_track2.json()["payment_status"] == "refund_failed"

        # 3. Hoàn tiền thành công
        ref_id = f"FINAL_REF_{uuid.uuid4().hex[:6]}"
        r_refund = client.post(
            f"/api/admin/orders/{order_id}/process-refund",
            headers=admin_headers,
            json={"is_manual": True, "refund_trans_id": ref_id}
        )
        assert r_refund.status_code == 200
        r_track3 = client.get(f"/api/orders/{order_id}?phone={phone}")
        assert r_track3.status_code == 200
        assert r_track3.json()["payment_status"] == "refunded"
        assert r_track3.json()["refund_trans_id"] == ref_id
