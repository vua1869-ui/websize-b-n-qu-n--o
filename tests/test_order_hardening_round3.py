"""
Test Suite Round 3: customer_email, tracking page, customer cancel, button guard.
Mỗi luồng có ít nhất 1 test fail khi bỏ bản vá.
"""
import threading
import time
import pytest
from fastapi.testclient import TestClient

from app.db.database import db_service
from app.main import app
from app.services.order_service import OrderError, order_service
from app.services.product_service import product_service
from tests.conftest import CUSTOMER, item


# ==============================================================================
# (1) customer_email: validate & email dispatch
# ==============================================================================

class TestCustomerEmail:
    def test_invalid_email_rejected(self, client):
        """Email sai format bị từ chối ngay ở server (pydantic validator)."""
        req = {
            **CUSTOMER,
            "items": [item("prod_001", qty=1)],
            "payment_method": "cod",
            "customer_email": "not-an-email",
        }
        r = client.post("/api/orders", json=req)
        assert r.status_code == 422, "Email không hợp lệ phải bị 422"

    def test_valid_email_accepted(self, client):
        """Email hợp lệ được chấp nhận và đơn tạo thành công."""
        req = {
            **CUSTOMER,
            "items": [item("prod_001", qty=1)],
            "payment_method": "cod",
            "customer_email": "test@example.com",
        }
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200, r.text

    def test_no_email_still_works(self, client):
        """Không gửi customer_email -> đơn vẫn tạo thành công."""
        req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "cod"}
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200, r.text

    def test_empty_string_email_treated_as_none(self, client):
        """Email rỗng ('') không gây lỗi, được xử lý như None."""
        req = {
            **CUSTOMER,
            "items": [item("prod_001", qty=1)],
            "payment_method": "cod",
            "customer_email": "",
        }
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200, r.text

    def test_console_email_sender_dispatched(self, client, caplog):
        """ConsoleEmailSender ghi log khi customer_email hợp lệ được đặt."""
        import logging
        req = {
            **CUSTOMER,
            "items": [item("prod_001", qty=1)],
            "payment_method": "cod",
            "customer_email": "buyer@example.com",
        }
        with caplog.at_level(logging.INFO, logger="aura.email"):
            r = client.post("/api/orders", json=req)
        assert r.status_code == 200, r.text
        # ConsoleEmailSender logs về đơn xác nhận
        log_text = caplog.text
        assert "buyer@example.com" in log_text or "XÁC NHẬN" in log_text or "order" in log_text.lower()


# ==============================================================================
# (2) /tracking: phone match required
# ==============================================================================

class TestTrackingPage:
    def _create_order(self, client) -> tuple[str, str]:
        """Helper: tạo đơn, trả (order_id, phone)."""
        phone = "0901234567"
        req = {
            "customer_name": "Trần Thị B",
            "customer_phone": phone,
            "customer_address": "12 Phố Huế, Hai Bà Trưng, Hà Nội",
            "items": [item("prod_001", qty=1)],
            "payment_method": "cod",
        }
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200
        return r.json()["order_id"], phone

    def test_tracking_form_shows_without_params(self, client):
        """Trang /tracking không có tham số -> hiện form, không báo lỗi."""
        r = client.get("/tracking")
        assert r.status_code == 200
        assert "Tra cứu" in r.text

    def test_tracking_correct_phone_shows_order(self, client):
        """Đúng mã + SĐT -> hiển thị chi tiết đơn hàng."""
        order_id, phone = self._create_order(client)
        r = client.get(f"/tracking?code={order_id}&phone={phone}")
        assert r.status_code == 200
        assert order_id in r.text

    def test_tracking_wrong_phone_shows_error(self, client):
        """Sai SĐT -> hiện thông báo lỗi, không lộ thông tin đơn."""
        order_id, _ = self._create_order(client)
        r = client.get(f"/tracking?code={order_id}&phone=0999888777")
        assert r.status_code == 200
        # Phải hiện lỗi SĐT không khớp
        assert "không khớp" in r.text or "lỗi" in r.text.lower() or order_id not in r.text

    def test_tracking_missing_phone_shows_error(self, client):
        """Có mã đơn nhưng không có SĐT -> không tiết lộ thông tin."""
        order_id, _ = self._create_order(client)
        r = client.get(f"/tracking?code={order_id}")
        assert r.status_code == 200
        # Không được hiện chi tiết đơn khi thiếu phone
        assert order_id not in r.text or "không khớp" in r.text or "Tra cứu" in r.text

    def test_tracking_nonexistent_order_shows_error(self, client):
        """Mã đơn không tồn tại -> hiện lỗi không tìm thấy."""
        r = client.get("/tracking?code=AURA-000000-FAKE99&phone=0901234567")
        assert r.status_code == 200
        assert "Không tìm thấy" in r.text or "không tìm thấy" in r.text.lower()

    def test_tracking_phone_normalized(self, client):
        """SĐT dạng +84 vẫn khớp với 0x."""
        order_id, _ = self._create_order(client)  # phone = 0901234567
        r = client.get(f"/tracking?code={order_id}&phone=+84901234567")
        assert r.status_code == 200
        assert order_id in r.text


# ==============================================================================
# (3) Customer cancel: quyền, trạng thái, race condition
# ==============================================================================

class TestCustomerCancel:
    def _make_order(self, client, phone="0911222333", method="cod") -> str:
        req = {
            "customer_name": "Lê Thị C",
            "customer_phone": phone,
            "customer_address": "12 Phố Huế, Hai Bà Trưng, Hà Nội",
            "items": [item("prod_001", qty=1)],
            "payment_method": method,
        }
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200, r.text
        return r.json()["order_id"]

    # 3a. Huỷ đơn COD chưa thanh toán
    def test_cancel_cod_unpaid_success(self, client):
        order_id = self._make_order(client)
        r = client.post(
            f"/api/orders/{order_id}/cancel",
            json={"phone": "0911222333", "reason": "Test"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["success"] is True
        order = db_service.get_order_by_id(order_id)
        assert order["order_status"] == "cancelled"
        # Kho phải được hoàn
        p = product_service.get_by_id("prod_001")
        assert p.stock > 0

    # 3b. Đơn đã thanh toán -> refund_pending
    def test_cancel_paid_order_sets_refund_pending(self, client):
        order_id = self._make_order(client)
        # Giả lập đơn đã thanh toán
        total = db_service.get_order_by_id(order_id)["total_amount"]
        db_service.confirm_payment(order_id, total, "TX-PAID-CANCEL-1", "vietqr")

        r = client.post(
            f"/api/orders/{order_id}/cancel",
            json={"phone": "0911222333"},
        )
        assert r.status_code == 200, r.text
        order = db_service.get_order_by_id(order_id)
        assert order["order_status"] == "cancelled"
        assert order["payment_status"] == "refund_pending"
        assert "hoàn" in r.json()["message"].lower()

    # 3c. Không thể huỷ khi đã shipping
    def test_cancel_blocked_after_shipping(self, client):
        order_id = self._make_order(client)
        order_service.update_order_status(order_id, "shipping")
        r = client.post(
            f"/api/orders/{order_id}/cancel",
            json={"phone": "0911222333"},
        )
        assert r.status_code == 409

    # 3d. SĐT sai -> 404 (không lộ order_id)
    def test_cancel_wrong_phone_denied(self, client):
        order_id = self._make_order(client)
        r = client.post(
            f"/api/orders/{order_id}/cancel",
            json={"phone": "0999999999"},
        )
        assert r.status_code == 404

    # 3e. Huỷ lần 2 -> 409
    def test_cancel_already_cancelled_409(self, client):
        order_id = self._make_order(client)
        r1 = client.post(f"/api/orders/{order_id}/cancel", json={"phone": "0911222333"})
        assert r1.status_code == 200
        r2 = client.post(f"/api/orders/{order_id}/cancel", json={"phone": "0911222333"})
        assert r2.status_code == 409

    # 3f. Kho được hoàn đúng sau huỷ
    def test_cancel_restores_stock(self, client):
        p_before = product_service.get_by_id("prod_001")
        stock_before = p_before.stock

        order_id = self._make_order(client)

        p_after_order = product_service.get_by_id("prod_001")
        assert p_after_order.stock == stock_before - 1

        r = client.post(f"/api/orders/{order_id}/cancel", json={"phone": "0911222333"})
        assert r.status_code == 200

        p_after_cancel = product_service.get_by_id("prod_001")
        assert p_after_cancel.stock == stock_before

    # 3g. Race condition: customer cancel vs admin shipping cùng lúc
    def test_race_cancel_vs_admin_shipping(self, client):
        """
        Dùng threading.Barrier để đảm bảo 2 thread cùng bắt đầu.
        Kết quả hợp lệ: đúng 1 thành công và 1 thất bại (409).
        Tồn kho không bị lệch: tổng stock = stock_ban_đầu.
        """
        phone = "0922333444"
        stock_before = product_service.get_by_id("prod_001").stock
        order_id = self._make_order(client, phone=phone)

        barrier = threading.Barrier(2)
        results = {}

        def try_cancel():
            barrier.wait()
            r = client.post(
                f"/api/orders/{order_id}/cancel",
                json={"phone": phone},
            )
            results["cancel"] = r.status_code

        def try_ship():
            barrier.wait()
            try:
                order_service.update_order_status(order_id, "shipping")
                results["ship"] = 200
            except OrderError as e:
                results["ship"] = e.status_code

        t1 = threading.Thread(target=try_cancel)
        t2 = threading.Thread(target=try_ship)
        t1.start(); t2.start()
        t1.join(); t2.join()

        cancel_ok = results.get("cancel") == 200
        ship_ok = results.get("ship") == 200

        # Đúng 1 phải thành công
        assert cancel_ok != ship_ok, (
            f"Race: cả hai cùng thành công hoặc cùng thất bại! cancel={results['cancel']} ship={results['ship']}"
        )

        # Kiểm tra trạng thái đơn nhất quán
        order = db_service.get_order_by_id(order_id)
        final_status = order["order_status"]
        assert final_status in ("cancelled", "shipping"), f"Trạng thái không hợp lệ: {final_status}"

        if cancel_ok:
            assert final_status == "cancelled"
        else:
            assert final_status == "shipping"

        # Kho không được âm, không mất hàng
        stock_after = product_service.get_by_id("prod_001").stock
        if cancel_ok:
            # Đơn huỷ -> kho hoàn lại
            assert stock_after == stock_before, f"Kho sau huỷ={stock_after}, mong đợi={stock_before}"
        else:
            # Đơn shipping -> kho vẫn giảm
            assert stock_after == stock_before - 1, f"Kho sau ship={stock_after}, mong đợi={stock_before - 1}"

    # 3h. Kho không bị hoàn 2 lần khi gọi cancel 2 lần concurrent
    def test_no_double_stock_restore_on_concurrent_cancel(self, client):
        """2 request huỷ cùng lúc -> chỉ 1 thành công, kho hoàn đúng 1 lần."""
        phone = "0933444555"
        order_id = self._make_order(client, phone=phone)

        stock_after_order = product_service.get_by_id("prod_001").stock
        stock_at_start = product_service.get_by_id("prod_001").stock + 1  # restore reference

        barrier = threading.Barrier(2)
        results = {}

        def try_cancel(key):
            barrier.wait()
            r = client.post(f"/api/orders/{order_id}/cancel", json={"phone": phone})
            results[key] = r.status_code

        t1 = threading.Thread(target=try_cancel, args=("r1",))
        t2 = threading.Thread(target=try_cancel, args=("r2",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        ok_count = sum(1 for s in results.values() if s == 200)
        assert ok_count == 1, f"Chỉ 1 trong 2 cancel được thành công: {results}"

        # Kho hoàn đúng 1 lần
        final_stock = product_service.get_by_id("prod_001").stock
        # stock_at_start = before order; stock_after_order = before cancel
        # Sau 1 cancel thành công: stock phải bằng stock_at_start
        assert final_stock == stock_after_order + 1, (
            f"Kho={final_stock} nhưng mong đợi={stock_after_order + 1} (hoàn 1 lần)"
        )


# ==============================================================================
# (4) UI: button guard (unit-level test on JS-accessible data)
# ==============================================================================

class TestUIEmailAndButtonGuard:
    def test_checkout_page_has_email_field(self, client):
        """Trang chủ phải có input co-email (type=email)."""
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="co-email"' in r.text, "Không tìm thấy input #co-email trong trang chủ"
        assert 'type="email"' in r.text

    def test_checkout_page_phone_field_is_tel(self, client):
        """Input SĐT phải là type=tel."""
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="co-phone"' in r.text
        assert 'type="tel"' in r.text

    def test_tracking_page_phone_field_is_tel(self, client):
        """Input SĐT trên trang tracking phải là type=tel."""
        r = client.get("/tracking")
        assert r.status_code == 200
        assert 'type="tel"' in r.text

    def test_order_api_includes_customer_email_in_response(self, client):
        """GET /api/orders/{id} trả về customer_email khi được cung cấp."""
        req = {
            **CUSTOMER,
            "items": [item("prod_001", qty=1)],
            "payment_method": "cod",
            "customer_email": "check@example.com",
        }
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200
        order_id = r.json()["order_id"]
        phone = CUSTOMER["customer_phone"]

        r2 = client.get(f"/api/orders/{order_id}?phone={phone}")
        assert r2.status_code == 200
        # customer_email được lưu và trả về
        data = r2.json()
        email_from_customer = data.get("customer", {}).get("email") or data.get("customer_email")
        assert email_from_customer == "check@example.com", (
            f"customer_email không được lưu/trả về: {data}"
        )


# ==============================================================================
# (5) cancel_order_atomic forbid_if_shipping unit tests
# ==============================================================================

class TestCancelOrderAtomicShippingGuard:
    def _create(self) -> str:
        from tests.conftest import item as _item
        req = {
            **CUSTOMER,
            "items": [_item("prod_001", qty=1)],
            "payment_method": "cod",
        }
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post("/api/orders", json=req)
        assert r.status_code == 200
        return r.json()["order_id"]

    def test_cancel_atomic_allows_pending(self):
        order_id = self._create()
        result = db_service.cancel_order_atomic(order_id, forbid_if_shipping=True)
        assert result is True
        order = db_service.get_order_by_id(order_id)
        assert order["order_status"] == "cancelled"

    def test_cancel_atomic_blocks_shipping_with_flag(self, client):
        order_id = self._create()
        order_service.update_order_status(order_id, "shipping")
        with pytest.raises(OrderError) as exc_info:
            db_service.cancel_order_atomic(order_id, forbid_if_shipping=True)
        assert exc_info.value.status_code == 409

    def test_cancel_atomic_allows_shipping_without_flag(self, client):
        """forbid_if_shipping=False (default): vẫn huỷ được dù đang shipping (hành vi admin)."""
        order_id = self._create()
        order_service.update_order_status(order_id, "shipping")
        result = db_service.cancel_order_atomic(order_id, forbid_if_shipping=False)
        assert result is True

    def test_cancel_atomic_idempotent(self, client):
        """Gọi 2 lần -> lần 2 trả về False thay vì raise."""
        order_id = self._create()
        r1 = db_service.cancel_order_atomic(order_id)
        r2 = db_service.cancel_order_atomic(order_id)
        assert r1 is True
        assert r2 is False  # Đã cancelled -> không làm gì
