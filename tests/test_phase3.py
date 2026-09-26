"""
Kiểm thử Giai đoạn 3 (Phase 3): Giữ chân khách hàng & Đa kênh (AURA Loyalty Club, Chống bỏ giỏ hàng, CSKH Đa kênh)
- AURA Loyalty Club: API hạng thẻ, tích & tiêu điểm (1 điểm = 1.000đ), lịch sử giao dịch.
- Miễn phí vận chuyển VIP 100% mọi đơn cho hội viên Gold/Diamond.
- Trừ điểm khi thanh toán và tích lũy chi tiêu/thăng hạng.
- Voucher độc quyền giữ chân STAYWITHUS (giảm 5%, tối đa 100K).
- Quản trị hiển thị Hạng thẻ & Điểm tích lũy của khách hàng.
"""
import pytest
from app.db.database import db_service
from tests.conftest import CUSTOMER, item


def _login_user(client, username="user", password="user123"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _login_admin(client):
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_loyalty_status_and_history_api(client):
    """Kiểm tra API trạng thái hạng thẻ và lịch sử điểm tích lũy của hội viên."""
    headers = _login_user(client)

    # 1. GET /api/loyalty/status
    r_status = client.get("/api/loyalty/status", headers=headers)
    assert r_status.status_code == 200
    status = r_status.json()

    assert "tier" in status
    assert status["tier"].lower() in ["silver", "gold", "diamond"]
    assert "points_balance" in status
    assert status["points_balance"] >= 0
    assert status["points_value_vnd"] == status["points_balance"] * 1000
    assert "tier_badge" in status
    assert "benefits" in status and len(status["benefits"]) > 0
    assert "progress_percent" in status
    assert 0 <= status["progress_percent"] <= 100

    # 2. GET /api/loyalty/history
    r_hist = client.get("/api/loyalty/history", headers=headers)
    assert r_hist.status_code == 200
    hist = r_hist.json()
    assert "points_balance" in hist
    assert "transactions" in hist
    assert isinstance(hist["transactions"], list)
    if hist["transactions"]:
        tx = hist["transactions"][0]
        assert "points" in tx
        assert "type" in tx
        assert "description" in tx


def test_loyalty_simulate_bonus_api(client):
    """Kiểm tra chức năng tặng/thưởng điểm trải nghiệm sự kiện."""
    headers = _login_user(client)

    # Lấy điểm ban đầu
    initial_pts = client.get("/api/loyalty/status", headers=headers).json()["points_balance"]

    # Cộng 50 điểm
    r_bonus = client.post("/api/loyalty/simulate-earn?points=50", headers=headers)
    assert r_bonus.status_code == 200
    data = r_bonus.json()
    assert data["success"] is True
    assert data["points_added"] == 50
    assert data["new_balance"] == initial_pts + 50

    # Kiểm tra số dư mới
    new_status = client.get("/api/loyalty/status", headers=headers).json()
    assert new_status["points_balance"] == initial_pts + 50

    # Kiểm tra có giao dịch bonus trong lịch sử
    hist = client.get("/api/loyalty/history", headers=headers).json()
    assert any(tx["type"] == "bonus" and tx["points"] == 50 for tx in hist["transactions"])


def test_checkout_quote_with_loyalty_points(client):
    """Kiểm tra báo giá quote khi áp dụng điểm tích lũy AURA Club."""
    headers = _login_user(client)

    # Tặng thêm điểm để chắc chắn có đủ điểm test
    client.post("/api/loyalty/simulate-earn?points=30", headers=headers)

    quote_req = {
        "items": [item("prod_001", qty=1)],
        "use_points": 20,
    }
    r = client.post("/api/orders/quote", json=quote_req, headers=headers)
    assert r.status_code == 200
    q = r.json()

    assert q["points_used"] == 20
    assert q["points_discount"] == 20000
    # Tổng thanh toán phải được trừ đi 20.000đ điểm thưởng
    expected_total = q["subtotal"] - q["combo_discount"] - q["voucher_discount"] - q["points_discount"] + q["shipping_fee"] - q["shipping_discount"]
    assert q["total"] == max(0, expected_total)


def test_vip_free_shipping_for_gold_member(client):
    """Đặc quyền VIP Gold/Diamond: Miễn phí vận chuyển 100% cho mọi đơn hàng (kể cả dưới 299k)."""
    # 1. Khách vãng lai (không đăng nhập) mua đơn nhỏ dưới 299k (áo phông prod_005 199.000đ)
    guest_quote = client.post("/api/orders/quote", json={"items": [item("prod_005", qty=1)]}).json()
    assert guest_quote["subtotal"] < 299000
    assert guest_quote["shipping_fee"] == 30000
    assert guest_quote["shipping_discount"] == 0

    # 2. Thành viên Gold (user Tiến Anh usr_002 đã được seed Gold)
    headers = _login_user(client)
    vip_quote = client.post("/api/orders/quote", json={"items": [item("prod_005", qty=1)]}, headers=headers).json()
    assert vip_quote["subtotal"] < 299000
    assert vip_quote["shipping_fee"] == 30000
    # Nhờ đặc quyền VIP: shipping_discount = shipping_fee
    assert vip_quote["shipping_discount"] == 30000
    assert vip_quote["total"] == vip_quote["subtotal"] - vip_quote["combo_discount"] - vip_quote["points_discount"]


def test_create_order_with_points_and_tier_accumulation(client):
    """Tạo đơn hàng sử dụng điểm, kiểm tra trừ điểm nguyên tử và tích lũy điểm/chi tiêu mới."""
    headers = _login_user(client)

    # Đảm bảo có ít nhất 15 điểm
    client.post("/api/loyalty/simulate-earn?points=20", headers=headers)
    before_status = client.get("/api/loyalty/status", headers=headers).json()
    initial_pts = before_status["points_balance"]
    initial_spent = before_status["total_spent"]

    # Đặt hàng sử dụng 10 điểm
    order_req = {
        **CUSTOMER,
        "items": [item("prod_001", qty=1)],
        "payment_method": "cod",
        "use_points": 10,
    }
    r_order = client.post("/api/orders", json=order_req, headers=headers)
    assert r_order.status_code == 200
    order_data = r_order.json()
    quote = order_data["quote"]
    assert quote["points_used"] == 10
    assert quote["points_discount"] == 10000
    earned = quote["points_earned"]
    assert earned > 0

    # Kiểm tra cập nhật điểm và chi tiêu sau đơn
    after_status = client.get("/api/loyalty/status", headers=headers).json()
    assert after_status["points_balance"] == initial_pts - 10 + earned
    assert after_status["total_spent"] > initial_spent

    # Kiểm tra lịch sử giao dịch điểm có cả giao dịch trừ điểm và tích điểm
    hist = client.get("/api/loyalty/history", headers=headers).json()["transactions"]
    assert any(tx["type"] == "redeem" and abs(tx["points"]) == 10 for tx in hist)
    assert any(tx["type"] == "earn" and tx["points"] == earned for tx in hist)


def test_exit_intent_voucher_staywithus(client):
    """Kiểm tra voucher giữ chân khách hàng STAYWITHUS (giảm 5%, tối đa 100K)."""
    # 1. Đơn đủ điều kiện (>= 100.000đ)
    req = {
        "items": [item("prod_001", qty=1)],
        "voucher_code": "STAYWITHUS"
    }
    r = client.post("/api/orders/quote", json=req)
    assert r.status_code == 200
    q = r.json()
    assert q["voucher_code"] == "STAYWITHUS"
    # Giảm 5% sau combo
    expected_disc = min(q["subtotal"] * 5 // 100, 100000)
    assert q["voucher_discount"] == expected_disc
    assert "Đã áp dụng mã STAYWITHUS" in q["voucher_message"]


def test_admin_users_displays_loyalty_data(client):
    """Quản trị viên xem danh sách khách hàng thấy đầy đủ Hạng thẻ, Điểm tích lũy và Tổng chi tiêu."""
    admin_headers = _login_admin(client)
    r = client.get("/api/admin/users", headers=admin_headers)
    assert r.status_code == 200
    users = r.json()
    assert len(users) >= 2

    # Tìm user demo usr_002
    usr_002 = next((u for u in users if u["id"] == "usr_002"), None)
    assert usr_002 is not None
    assert "tier" in usr_002 and usr_002["tier"].lower() in ["gold", "diamond", "silver"]
    assert "points_balance" in usr_002 and usr_002["points_balance"] >= 0
    assert "total_spent" in usr_002 and usr_002["total_spent"] >= 0
