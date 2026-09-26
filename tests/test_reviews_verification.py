"""
Test Suite xác minh điều kiện đánh giá sản phẩm (Verified Purchase Review):
1. Yêu cầu đăng nhập: 401 khi chưa đăng nhập.
2. Kiểm tra mua hàng: 403 với thông báo "Bạn cần mua sản phẩm này trước khi đánh giá." khi:
   - User chưa từng mua sản phẩm.
   - User chỉ mua sản phẩm khác.
   - Đơn hàng chứa sản phẩm ở trạng thái pending_payment (chưa thanh toán).
3. Đánh giá hợp lệ: 200 khi user có đơn hàng trạng thái 'paid' hoặc 'completed' chứa sản phẩm đó.
4. Badge '✓ Đã mua hàng' (is_verified_buyer):
   - Review hợp lệ gắn is_verified_buyer = True.
   - Review chưa xác minh (is_verified_buyer = 0 trong DB) trả is_verified_buyer = False, không gắn nhãn giả.
"""
import uuid
import pytest
from app.db.database import db_service, get_db_connection
from tests.conftest import CUSTOMER, item


def _register_and_login(client, prefix="user_rev"):
    uid = uuid.uuid4().hex[:6]
    username = f"{prefix}_{uid}"
    pwd = "UserPass123!"
    reg = client.post("/api/auth/register", json={
        "username": username,
        "password": pwd,
        "full_name": f"Họ Tên {uid}",
        "email": f"{username}@example.com"
    })
    assert reg.status_code == 200
    token = reg.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_review_unauthenticated_rejected_401(client):
    """Từ chối gửi review khi chưa đăng nhập -> trả 401."""
    review_data = {
        "rating": 5,
        "comment": "Chất lượng áo rất tốt, vải đanh mịn!",
        "fit_feedback": "Vừa vặn"
    }
    res = client.post("/api/products/prod_001/reviews", json=review_data)
    assert res.status_code == 401
    assert "đăng nhập" in res.json()["detail"].lower()


def test_review_not_purchased_rejected_403(client):
    """User đã đăng nhập nhưng chưa từng mua sản phẩm -> trả 403."""
    headers = _register_and_login(client, "newbie")
    review_data = {
        "rating": 5,
        "comment": "Áo blazer rất đẹp nhưng tôi chưa mua!",
        "fit_feedback": "Vừa vặn"
    }
    res = client.post("/api/products/prod_001/reviews", json=review_data, headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"] == "Bạn cần mua sản phẩm này trước khi đánh giá."


def test_review_purchased_other_product_rejected_403(client):
    """User chỉ mua prod_002, nhưng cố gắng review prod_001 -> trả 403."""
    headers = _register_and_login(client, "buyer_prod2")

    # Mua prod_002 và thanh toán thành công
    order_req = {
        **CUSTOMER,
        "payment_method": "qr_transfer",
        "items": [item("prod_002", qty=1)]
    }
    order_res = client.post("/api/orders", json=order_req, headers=headers)
    assert order_res.status_code == 200
    order_id = order_res.json()["order_id"]
    order_total = order_res.json()["quote"]["total"]
    db_service.confirm_payment(order_id, order_total, "TX-OTHER-PROD", "test")

    # Review prod_001 (chưa từng mua)
    review_data = {
        "rating": 4,
        "comment": "Áo prod_001 chưa mua thử đánh giá",
        "fit_feedback": "Vừa vặn"
    }
    res = client.post("/api/products/prod_001/reviews", json=review_data, headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"] == "Bạn cần mua sản phẩm này trước khi đánh giá."


def test_review_order_pending_payment_rejected_403(client):
    """User đặt prod_001 nhưng chưa thanh toán (pending_payment/unpaid) -> trả 403."""
    headers = _register_and_login(client, "unpaid_user")

    # Đặt hàng qua chuyển khoản nhưng chưa xác nhận thanh toán
    order_req = {
        **CUSTOMER,
        "payment_method": "qr_transfer",
        "items": [item("prod_001", qty=1)]
    }
    order_res = client.post("/api/orders", json=order_req, headers=headers)
    assert order_res.status_code == 200
    assert order_res.json()["status"] == "pending_payment"

    # Review khi chưa thanh toán
    review_data = {
        "rating": 5,
        "comment": "Chưa thanh toán đã cố gửi review",
        "fit_feedback": "Vừa vặn"
    }
    res = client.post("/api/products/prod_001/reviews", json=review_data, headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"] == "Bạn cần mua sản phẩm này trước khi đánh giá."


def test_review_paid_order_accepted_200_and_verified_badge(client):
    """User có đơn hàng 'paid' chứa sản phẩm -> 200 và gắn badge is_verified_buyer=True."""
    headers = _register_and_login(client, "verified_buyer")

    # 1. Đặt hàng prod_001
    order_req = {
        **CUSTOMER,
        "payment_method": "qr_transfer",
        "items": [item("prod_001", qty=1)]
    }
    order_res = client.post("/api/orders", json=order_req, headers=headers)
    assert order_res.status_code == 200
    order_id = order_res.json()["order_id"]
    order_total = order_res.json()["quote"]["total"]

    # 2. Xác nhận thanh toán 'paid'
    db_service.confirm_payment(order_id, order_total, "TX-VERIFIED-1", "vietqr")

    # 3. Gửi đánh giá
    review_data = {
        "user_name": "Người mua hàng thực tế",
        "rating": 5,
        "comment": "Áo đẹp xuất sắc, đúng kích cỡ người mặc.",
        "height_cm": 165.0,
        "weight_kg": 52.0,
        "purchased_size": "M",
        "purchased_color": "Nâu Đất",
        "fit_feedback": "Vừa vặn"
    }
    res = client.post("/api/products/prod_001/reviews", json=review_data, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "review" in data
    assert data["review"]["is_verified_buyer"] is True

    # 4. Kiểm tra trong danh sách GET reviews
    get_res = client.get("/api/products/prod_001/reviews")
    assert get_res.status_code == 200
    all_revs = get_res.json()["reviews"]
    verified_rev = next((r for r in all_revs if r["comment"] == "Áo đẹp xuất sắc, đúng kích cỡ người mặc."), None)
    assert verified_rev is not None
    assert verified_rev["is_verified_buyer"] is True


def test_review_completed_order_accepted_200(client):
    """User có đơn hàng trạng thái 'completed' -> chấp nhận review."""
    headers = _register_and_login(client, "completed_buyer")

    # 1. Tạo đơn hàng prod_003
    order_req = {
        **CUSTOMER,
        "payment_method": "cod",
        "items": [item("prod_003", qty=1)]
    }
    order_res = client.post("/api/orders", json=order_req, headers=headers)
    assert order_res.status_code == 200
    order_id = order_res.json()["order_id"]

    # 2. Cập nhật trạng thái đơn thành 'completed'
    from app.services.order_service import order_service
    order_service.update_order_status(order_id, "completed")

    # 3. Gửi review prod_003
    review_data = {
        "rating": 5,
        "comment": "Váy maxi lụa tơ tằm mặc rất đẹp và sang!",
        "fit_feedback": "Vừa vặn"
    }
    res = client.post("/api/products/prod_003/reviews", json=review_data, headers=headers)
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["review"]["is_verified_buyer"] is True


def test_unverified_review_in_db_has_no_badge(client):
    """Review chưa xác minh (is_verified_buyer=0) trả về False, không gắn nhãn giả."""
    # Giả lập 1 review chưa xác minh chèn trực tiếp vào CSDL
    conn = get_db_connection(db_service.db_path)
    inserted_id = None
    try:
        cursor = conn.execute(
            """
            INSERT INTO reviews 
            (product_id, user_name, rating, comment, is_verified_buyer, created_at)
            VALUES (?, ?, ?, ?, 0, '26/09/2026 12:00');
            """,
            ("prod_001", "Khách Chưa Mua", 3, "Bình luận chưa xác minh đơn hàng")
        )
        conn.commit()
        inserted_id = cursor.lastrowid

        get_res = client.get("/api/products/prod_001/reviews")
        assert get_res.status_code == 200
        all_revs = get_res.json()["reviews"]
        unverified_rev = next((r for r in all_revs if r["comment"] == "Bình luận chưa xác minh đơn hàng"), None)
        assert unverified_rev is not None
        assert unverified_rev["is_verified_buyer"] is False
    finally:
        if inserted_id:
            conn.execute("DELETE FROM reviews WHERE id = ?;", (inserted_id,))
            conn.commit()
        conn.close()
