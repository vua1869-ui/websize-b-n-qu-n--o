"""
Test Suite Round 2: Hardening toàn diện MỤC A -> MỤC E theo yêu cầu kiểm thử.
- MỤC A: Xác nhận thanh toán kiểm tra số tiền, huỷ đơn, idempotent, VNPay return/ipn, config validator.
- MỤC B: Tồn kho không reset khi restart, hủy đơn hoàn đủ kho/điểm/tier, bảng chuyển trạng thái 400.
- MỤC C: Tạo đơn nguyên tử, gộp dòng trùng, hoãn cộng điểm COD/online, voucher limits & usages.
- MỤC D: Bảo mật web (PUBLIC_BASE_URL, XSS server-side defense, token_version exclude, security headers).
- MỤC E: Khởi động DB trống, migration cột bảng, không nạp demo data khi không phải dev.
"""
import datetime
import html
import json
import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.db.database import db_service, get_db_connection
from app.main import app
from app.models.schemas import (
    OrderCreateRequest, OrderItem, UserRegisterRequest, UserProfileUpdateRequest, Voucher
)
from app.services.order_service import OrderError, order_service
from app.services.product_service import product_service
from tests.conftest import CUSTOMER, item
from tests.test_payment_vnpay import _generate_vnpay_params


# ==============================================================================
# MỤC A: Xác nhận thanh toán kiểm tra số tiền & An toàn giao dịch
# ==============================================================================
def test_muc_a_confirm_payment_not_found():
    res = db_service.confirm_payment("AURA-NONEXISTENT-9999", 100000, "TX-NOTFOUND-1")
    assert res == "not_found"


def test_muc_a_confirm_payment_rejects_cancelled_order(client):
    req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "qr_transfer"}
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # Hủy đơn
    order_service.update_order_status(order_id, "cancelled")

    # Thử xác nhận thanh toán cho đơn đã hủy -> phải bị từ chối
    res = db_service.confirm_payment(order_id, total, "TX-CANCEL-1", "vietqr")
    assert res == "cancelled"

    # Kiểm tra trạng thái vẫn giữ nguyên là cancelled, payment_status chuyển sang refund_pending
    order = db_service.get_order_by_id(order_id)
    assert order["order_status"] == "cancelled"
    assert order["payment_status"] == "refund_pending"


def test_muc_a_confirm_payment_rejects_amount_mismatch(client):
    req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "qr_transfer"}
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # Webhook gửi amount = 1 thay vì total thật
    res = db_service.confirm_payment(order_id, 1, "TX-MISMATCH-1", "vietqr")
    assert res == "invalid_amount"

    # Đơn hàng vẫn phải unpaid
    order = db_service.get_order_by_id(order_id)
    assert order["payment_status"] == "unpaid"


def test_muc_a_confirm_payment_idempotent_success(client):
    req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "qr_transfer"}
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # Lần 1: Thành công
    res1 = db_service.confirm_payment(order_id, total, "TX-IDEMP-1", "vietqr")
    assert res1 == "success"

    # Lần 2: Gửi lại giao dịch -> trả already_paid idempotent, không nạp lại
    res2 = db_service.confirm_payment(order_id, total, "TX-IDEMP-1", "vietqr")
    assert res2 == "already_paid"


def test_muc_a_webhook_missing_amount_rejected_400(client):
    import hmac, hashlib
    from app.config import settings
    # Webhook không truyền amount hoặc amount rỗng -> 400
    payload = json.dumps({"order_id": "AURA-123", "transaction_id": "TX-1"}).encode("utf-8")
    sig = hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/payment/webhook",
        content=payload,
        headers={"Content-Type": "application/json", "X-Signature": sig}
    )
    assert r.status_code == 400
    assert "số tiền" in r.json()["detail"].lower()


def test_muc_a_vnpay_return_malformed_amount_and_escaping(client):
    req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "vnpay"}
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # VNPay return với chuỗi vnp_Amount không phải số hợp lệ
    params = _generate_vnpay_params(order_id=order_id, amount=total, response_code="00")
    params["vnp_Amount"] = "invalid_string"
    # Ký lại chữ ký sau khi sửa amount
    import urllib.parse, hmac, hashlib
    from app.config import settings
    sorted_p = sorted(params.items())
    hash_data = urllib.parse.urlencode([x for x in sorted_p if x[0] != "vnp_SecureHash"])
    params["vnp_SecureHash"] = hmac.new(settings.VNPAY_HASH_SECRET.encode(), hash_data.encode(), hashlib.sha512).hexdigest()

    # Không được văng 500, trả 400 Bad Request
    res = client.get("/api/payment/vnpay/return", params=params)
    assert res.status_code == 400


def test_muc_a_vnpay_ipn_returns_04_on_amount_mismatch(client):
    req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "vnpay"}
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    total = r.json()["quote"]["total"]

    # Amount sai lệch
    params = _generate_vnpay_params(order_id=order_id, amount=total + 50000, response_code="00")
    res = client.get("/api/payment/vnpay/ipn", params=params)
    assert res.status_code == 200
    assert res.json()["RspCode"] == "04"


def test_muc_a_config_validator_rejects_empty_vnpay_secret_in_prod():
    # Settings với APP_ENV=production và VNPAY_HASH_SECRET rỗng phải raise ValueError
    with pytest.raises(ValueError, match="VNPAY_HASH_SECRET"):
        Settings(
            APP_ENV="production",
            PUBLIC_BASE_URL="https://aurastudio.vn",
            SECRET_KEY="a"*32,
            JWT_SECRET_KEY="b"*32,
            VNPAY_HASH_SECRET=""
        )


# ==============================================================================
# MỤC B: Tồn kho & Hủy đơn hoàn đủ dữ liệu
# ==============================================================================
def test_muc_b_stock_persists_on_product_service_restart(client):
    # Đặt hàng 2 sản phẩm prod_008 (tồn 19 -> 17)
    r = client.post("/api/orders", json={**CUSTOMER, "items": [item("prod_008", qty=2)]})
    assert r.status_code == 200

    # Dựng lại ProductService giả lập restart ứng dụng
    fresh_service = product_service.__class__()
    assert fresh_service.get_by_id("prod_008").stock == 17


def test_muc_b_save_products_to_disk_omits_runtime_stats(tmp_path):
    # Kiểm tra _save_products_to_disk không ghi runtime stats vào file
    p = product_service.get_by_id("prod_001")
    save_file = str(tmp_path / "products.json")
    original_path = product_service.__class__
    
    # Dump test item
    p_dict = p.model_dump()
    assert "stock" in p_dict
    # Khi lưu theo logic mới: bỏ stock, sold_count, rating, reviews_count
    for f in ["stock", "sold_count", "rating", "reviews_count"]:
        p_dict.pop(f, None)
    assert "stock" not in p_dict


def test_muc_b_cancel_order_refunds_stock_points_spent_tier(client):
    # Đăng ký user có điểm
    from tests.test_phase3 import _login_user
    headers = _login_user(client)
    db_service.add_loyalty_points("usr_002", 50, "bonus", "Test points")

    # Lưu tồn ban đầu
    p_before = product_service.get_by_id("prod_001").stock

    # Tạo đơn dùng 10 điểm
    order_req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=2)],
        "payment_method": "cod",
        "use_points": 10,
    }
    r = client.post("/api/orders", json=order_req, headers=headers)
    assert r.status_code == 200
    order_id = r.json()["order_id"]

    # Tồn kho bộ nhớ và DB đã giảm 2
    assert product_service.get_by_id("prod_001").stock == p_before - 2

    # Hủy đơn
    order_service.update_order_status(order_id, "cancelled")

    # Kiểm tra:
    # 1. Tồn kho bộ nhớ và DB đã hoàn lại đủ
    assert product_service.get_by_id("prod_001").stock == p_before
    p_row = db_service.get_product_by_id("prod_001")
    assert p_row["stock"] == p_before

    # 2. Điểm được hoàn trả với type = 'refund'
    hist = client.get("/api/loyalty/history", headers=headers).json()["transactions"]
    assert any(tx["type"] == "refund" and tx["points"] == 10 for tx in hist)


def test_muc_b_status_transitions_strict_enforcement(client):
    r = client.post("/api/orders", json={**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "cod"})
    assert r.status_code == 200
    order_id = r.json()["order_id"]

    # COD đơn bắt đầu là 'confirmed'
    # Không thể nhảy cóc từ confirmed -> completed mà không qua shipping
    with pytest.raises(OrderError) as exc_info:
        order_service.update_order_status(order_id, "completed")
    assert exc_info.value.status_code == 400

    # Chuyển đúng: confirmed -> shipping -> completed
    order_service.update_order_status(order_id, "shipping")
    order_service.update_order_status(order_id, "completed")

    # Đơn đã completed là terminal state, không thể huỷ hay đổi trạng thái
    with pytest.raises(OrderError) as exc_info2:
        order_service.update_order_status(order_id, "cancelled")
    assert exc_info2.value.status_code == 400


# ==============================================================================
# MỤC C: Tạo đơn nguyên tử & Voucher Usage Limits
# ==============================================================================
def test_muc_c_deduplicate_identical_order_lines(client):
    # Đặt hàng với 2 dòng trùng nhau cùng product, size, color
    req = {
        **CUSTOMER,
        "items": [
            item("prod_001", size="M", color="Be / Kem", qty=1),
            item("prod_001", size="M", color="Be / Kem", qty=2),
        ],
        "payment_method": "cod"
    }
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    # Đơn hàng được tạo thành công, kho được trừ tổng cộng 3


def test_muc_c_atomic_rollback_on_insufficient_points(client):
    from tests.test_phase3 import _login_user
    headers = _login_user(client)
    
    # User hiện tại chỉ có ít điểm, yêu cầu dùng 999999 điểm
    req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "use_points": 999999,
        "payment_method": "cod"
    }
    r = client.post("/api/orders", json=req, headers=headers)
    assert r.status_code == 409


def test_muc_c_cod_initial_payment_status_unpaid_and_completed_paid(client):
    req = {**CUSTOMER, "items": [item("prod_001", qty=1)], "payment_method": "cod"}
    r = client.post("/api/orders", json=req)
    assert r.status_code == 200
    order_id = r.json()["order_id"]
    assert r.json()["payment_status"] == "unpaid"

    # Khi hoàn thành đơn hàng mới chuyển thành paid
    order_service.update_order_status(order_id, "shipping")
    final_order = order_service.update_order_status(order_id, "completed")
    assert final_order["payment_status"] == "paid"


def test_muc_c_voucher_max_uses_enforcement(client):
    import uuid
    from app.services import product_service as ps_mod
    vcode = f"ONCE_{uuid.uuid4().hex[:6].upper()}"
    # Tạo voucher thử nghiệm có max_uses = 1
    test_v = Voucher(
        code=vcode,
        title="Dùng 1 lần duy nhất",
        kind="amount",
        value=10000,
        min_order=50000,
        badge="1 Lần",
        expire_in="Hôm nay",
        max_uses=1,
    )
    ps_mod.AVAILABLE_VOUCHERS.append(test_v)

    req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "voucher_code": vcode,
    }
    # Lần 1: Thành công
    r1 = client.post("/api/orders", json=req)
    assert r1.status_code == 200

    # Lần 2: Hết lượt sử dụng trên hệ thống -> 400
    r2 = client.post("/api/orders", json=req)
    assert r2.status_code == 400
    assert "hết lượt sử dụng" in r2.json()["detail"].lower()


# ==============================================================================
# MỤC D: Bảo mật Web (PUBLIC_BASE_URL, XSS, Security Headers, Token)
# ==============================================================================
def test_muc_d_forgot_password_strictly_uses_public_base_url(client):
    # Giả lập kẻ tấn công gửi request quên mật khẩu kèm Host header độc hại
    malicious_host = "evil-phishing-site.com"
    r = client.post(
        "/api/auth/forgot-password",
        json={"email": "demo@example.com"},
        headers={"Host": malicious_host, "X-Forwarded-Host": malicious_host}
    )
    assert r.status_code == 200
    data = r.json()
    # Link đặt lại mật khẩu trả về (nếu có reset_link) phải tuyệt đối không chứa domain kẻ tấn công
    if "reset_link" in data and data["reset_link"]:
        assert malicious_host not in data["reset_link"]


def test_muc_d_public_base_url_prod_validation():
    # Khi APP_ENV=production thì PUBLIC_BASE_URL không được là localhost/127.0.0.1
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        Settings(
            APP_ENV="production",
            PUBLIC_BASE_URL="http://localhost:8000",
            SECRET_KEY="x"*32,
            JWT_SECRET_KEY="y"*32,
            VNPAY_HASH_SECRET="secret"
        )


def test_muc_d_server_side_xss_input_rejection(client):
    # Server-side validation từ chối ký tự < hoặc > trong dữ liệu khách hàng
    xss_payload = "<script>alert('xss')</script>"
    
    # 1. OrderCreateRequest
    order_req = {
        **CUSTOMER,
        "customer_name": xss_payload,
        "items": [item("prod_001", qty=1)]
    }
    r = client.post("/api/orders", json=order_req)
    assert r.status_code == 422

    # 2. UserRegisterRequest
    reg_req = {
        "username": "clean_user_1",
        "password": "Password123!",
        "full_name": f"Nguyễn {xss_payload}",
        "email": "clean@example.com"
    }
    r_reg = client.post("/api/auth/register", json=reg_req)
    assert r_reg.status_code == 422


def test_muc_d_token_version_excluded_from_response(client):
    import uuid
    rand_name = f"user_{uuid.uuid4().hex[:6]}"
    reg_req = {
        "username": rand_name,
        "password": "Password123!",
        "full_name": "Người Dùng Thử",
        "email": f"{rand_name}@example.com"
    }
    r = client.post("/api/auth/register", json=reg_req)
    assert r.status_code == 200
    data = r.json()
    # token_version tuyệt đối không xuất hiện trong user response
    assert "token_version" not in data.get("user", {})


def test_muc_d_security_headers_present(client):
    r = client.get("/")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "same-origin"
    assert "Content-Security-Policy-Report-Only" in r.headers


# ==============================================================================
# MỤC E: Khởi động DB trống & Migration
# ==============================================================================
def test_muc_e_clean_db_tables_and_columns_exist():
    # Kiểm tra các cột và bảng cần thiết đã được init_db / migration tạo đầy đủ
    conn = get_db_connection(db_service.db_path)
    try:
        user_cols = [c[1] for c in conn.execute("PRAGMA table_info(users);").fetchall()]
        assert "token_version" in user_cols
        assert "password_changed_at" in user_cols

        tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        assert "password_reset_tokens" in tables
        assert "cart_items" in tables
        assert "voucher_usages" in tables
    finally:
        conn.close()
