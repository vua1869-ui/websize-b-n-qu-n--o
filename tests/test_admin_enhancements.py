"""
Kiểm thử các tính năng quản trị nâng cao vừa được phát triển:
1. /admin/orders/{id}/print khổ A6 bằng CSS @media print, KHÔNG che SĐT, nổi bật tiền COD.
2. GET /api/admin/orders/export (CSV UTF-8 BOM, lọc trạng thái/ngày, chống formula injection bằng cách thêm ' trước =, +, -, @).
3. Cột internal_note (lưu, lấy, cập nhật ghi chú nội bộ cho đơn hàng).
4. Bảng audit_log (admin_id, hành động, đối tượng, trước/sau, IP, thời gian) cho:
   - Đổi giá sản phẩm
   - Xoá sản phẩm
   - Huỷ đơn hàng
   - Đổi trạng thái đơn hàng
   - Khoá tài khoản user
5. Báo cáo lợi nhuận hỗ trợ lọc from_date / to_date.
"""
import codecs
import uuid
import datetime
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import db_service
from app.services.order_service import order_service
from app.services.product_service import product_service
from app.services.user_service import user_service

client = TestClient(app)


@pytest.fixture(scope="module")
def admin_auth():
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    token = res.json()["token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "cookies": {"aura_session": token},
    }


@pytest.fixture(scope="module")
def user_auth():
    res = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert res.status_code == 200
    token = res.json()["token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "cookies": {"aura_session": token},
    }


def create_test_order(
    order_id=None,
    customer_name="Nguyen Van A",
    customer_phone="0987654321",
    customer_address="123 Tran Hung Dao, Q1, HCM",
    total_amount=900000,
    payment_method="cod",
    status="pending",
    internal_note="Giao sau 18h",
    items=None,
    created_at=None,
):
    if not order_id:
        order_id = f"ord_test_{uuid.uuid4().hex[:8]}"
    if items is None:
        items = [
            {
                "product_id": "prod_print_1",
                "name": "Ao Blazer Form Rong",
                "color": "Den",
                "size": "L",
                "quantity": 2,
                "unit_price": total_amount // 2,
                "line_total": total_amount,
            }
        ]
    order_data = {
        "order_id": order_id,
        "user_id": "test_user_print",
        "customer": {
            "name": customer_name,
            "phone": customer_phone,
            "address": customer_address,
            "note": "",
        },
        "payment_method": payment_method,
        "payment_status": "unpaid" if payment_method == "cod" else "paid",
        "status": status,
        "internal_note": internal_note,
        "created_at": created_at or datetime.datetime.now().isoformat(timespec="seconds"),
        "quote": {
            "total": total_amount,
            "subtotal": total_amount,
            "shipping_fee": 0,
            "lines": items,
        },
    }
    return db_service.save_order(order_data)


# ============================================================================
# 1. TEST IN ĐƠN HÀNG KHỔ A6, KHÔNG CHE SĐT, NỔI BẬT TIỀN COD
# ============================================================================

def test_order_print_a6_unmasked_phone_and_cod(admin_auth, user_auth):
    """
    Kiểm tra /admin/orders/{id}/print:
    - Yêu cầu quyền admin (user thường bị chặn 403).
    - Khổ A6 với CSS @page { size: A6 portrait; } và @media print.
    - SĐT KHÔNG bị che giấu (unmasked đầy đủ số).
    - Nổi bật số tiền COD rõ ràng.
    """
    raw_phone = "0987654321"
    order_id = create_test_order(
        customer_name="Nguyen Van A",
        customer_phone=raw_phone,
        total_amount=900000,
        payment_method="cod",
        internal_note="Giao sau 18h",
    )

    # 1. User thường không được xem trang in admin
    res_forbidden = client.get(
        f"/admin/orders/{order_id}/print",
        headers=user_auth["headers"],
        cookies=user_auth["cookies"],
    )
    assert res_forbidden.status_code == 403

    # 2. Admin xem trang in
    res = client.get(
        f"/admin/orders/{order_id}/print",
        headers=admin_auth["headers"],
        cookies=admin_auth["cookies"],
    )
    assert res.status_code == 200
    html = res.text

    # Kiểm tra quy cách in A6 và CSS print
    assert "@media print" in html
    assert "A6" in html
    assert "size: A6 portrait" in html

    # Kiểm tra SĐT KHÔNG bị che (hiển thị nguyên vẹn 0987654321, không có ***)
    assert raw_phone in html
    assert "098***" not in html

    # Kiểm tra nổi bật tiền COD
    assert "COD" in html
    assert "900.000" in html
    assert "TIỀN THU HỘ" in html or "THU TIỀN TẬN NƠI" in html


# ============================================================================
# 2. TEST XUẤT CSV ĐƠN HÀNG (UTF-8 BOM, LỌC TRẠNG THÁI/NGÀY, FORMULA INJECTION)
# ============================================================================

def test_admin_orders_export_csv_and_formula_injection(admin_auth):
    """
    Kiểm tra GET /api/admin/orders/export:
    - File trả về UTF-8 BOM (3 bytes đầu: EF BB BF).
    - Chống formula injection: thêm ký tự ' trước các giá trị bắt đầu bằng =, +, -, @.
    - Lọc theo status và date range.
    """
    # Tạo 2 đơn hàng, trong đó 1 đơn chứa các chuỗi formula injection nguy hiểm
    ord1_id = create_test_order(
        customer_name="=SUM(1+2)",
        customer_phone="+84912345678",
        customer_address="-123 Duong Nguy Hiem",
        internal_note="@internal_calc",
        status="pending",
        items=[
            {
                "product_id": "prod_csv_1",
                "name": "@HackProduct",
                "color": "Trang",
                "size": "M",
                "quantity": 1,
                "unit_price": 200000,
                "line_total": 200000,
            }
        ],
        total_amount=200000,
    )

    ord2_id = create_test_order(
        customer_name="Tran Thi B",
        customer_phone="0901234567",
        customer_address="456 Le Loi, Da Nang",
        internal_note="Khach quen",
        status="delivered",
        items=[
            {
                "product_id": "prod_csv_2",
                "name": "Dam Da Hoi",
                "color": "Do",
                "size": "S",
                "quantity": 1,
                "unit_price": 500000,
                "line_total": 500000,
            }
        ],
        total_amount=500000,
    )

    # 1. Xuất toàn bộ đơn hàng
    res = client.get("/api/admin/orders/export", headers=admin_auth["headers"])
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    content_bytes = res.content

    # Kiểm tra UTF-8 BOM
    assert content_bytes.startswith(codecs.BOM_UTF8), "CSV phải bắt đầu bằng UTF-8 BOM (\\xef\\xbb\\xbf)"

    # Giải mã nội dung CSV
    csv_text = content_bytes.decode("utf-8-sig")

    # Kiểm tra chống Formula Injection: các ô có ký tự =, +, -, @ phải được gắn dấu '
    assert "'=SUM(1+2)" in csv_text, "Ký tự '=' đầu ô phải được thêm '"
    assert "'+84912345678" in csv_text, "Ký tự '+' đầu ô phải được thêm '"
    assert "'-123 Duong Nguy Hiem" in csv_text, "Ký tự '-' đầu ô phải được thêm '"
    assert "'@internal_calc" in csv_text or "'@HackProduct" in csv_text, "Ký tự '@' đầu ô phải được thêm '"

    # 2. Kiểm tra lọc theo status: lọc 'delivered'
    res_filter_status = client.get("/api/admin/orders/export?status=delivered", headers=admin_auth["headers"])
    assert res_filter_status.status_code == 200
    csv_delivered = res_filter_status.content.decode("utf-8-sig")
    assert ord2_id in csv_delivered
    assert ord1_id not in csv_delivered

    # 3. Kiểm tra lọc theo date
    today_str = datetime.date.today().isoformat()
    res_filter_date = client.get(
        f"/api/admin/orders/export?from_date={today_str}&to_date={today_str}",
        headers=admin_auth["headers"],
    )
    assert res_filter_date.status_code == 200
    csv_today = res_filter_date.content.decode("utf-8-sig")
    assert ord1_id in csv_today


# ============================================================================
# 3. TEST CỘT INTERNAL_NOTE (LƯU, ĐỌC, CẬP NHẬT)
# ============================================================================

def test_order_internal_note_column_and_api(admin_auth):
    """
    Kiểm tra cột internal_note:
    - Lưu khi tạo đơn.
    - Cập nhật qua API PUT /api/admin/orders/{id}/internal-note.
    - Đọc từ danh sách / chi tiết đơn hàng trả về đúng giá trị.
    """
    ord_id = create_test_order(
        customer_name="Le Hoang",
        customer_phone="0911223344",
        customer_address="789 CMT8, Tan Binh",
        internal_note="Ghi chú ban đầu",
    )
    initial_order = order_service.get_order_by_id(ord_id)
    assert initial_order["internal_note"] == "Ghi chú ban đầu"

    # Cập nhật internal_note qua API quản trị
    updated_note = "Đã gọi xác nhận - KH hẹn giao vào thứ Hai"
    res_update = client.put(
        f"/api/admin/orders/{ord_id}/internal-note",
        headers=admin_auth["headers"],
        json={"internal_note": updated_note},
    )
    assert res_update.status_code == 200
    assert res_update.json()["internal_note"] == updated_note

    # Lấy lại đơn hàng từ service để xác nhận đã lưu vào DB
    refreshed_order = order_service.get_order_by_id(ord_id)
    assert refreshed_order["internal_note"] == updated_note


# ============================================================================
# 4. TEST BẢNG AUDIT_LOG (ĐỔI GIÁ, XOÁ SP, HUỶ ĐƠN, ĐỔI TRẠNG THÁI, KHOÁ USER)
# ============================================================================

def test_audit_logs_for_admin_actions(admin_auth):
    """
    Kiểm tra bảng audit_log ghi nhận đủ 5 nghiệp vụ:
    1. Đổi giá sản phẩm (change_price)
    2. Xoá sản phẩm (delete_product)
    3. Đổi trạng thái đơn hàng (update_status)
    4. Huỷ đơn hàng (cancel_order)
    5. Khoá tài khoản user (lock_user)
    """
    # 1. Tạo sản phẩm để test đổi giá và xoá
    p_data = {
        "name": f"Sản Phẩm Audit {uuid.uuid4().hex[:6]}",
        "category": "ao_so_mi",
        "gender": "nam",
        "price": 300000,
        "sizes": ["M", "L"],
        "colors": [{"name": "Trắng", "hex": "#ffffff"}],
        "stock": 20,
    }
    res_p = client.post("/api/admin/products", headers=admin_auth["headers"], json=p_data)
    assert res_p.status_code == 200
    p_id = res_p.json()["id"]

    # Hành động 1: Đổi giá sản phẩm
    res_price = client.put(
        f"/api/admin/products/{p_id}",
        headers=admin_auth["headers"],
        json={"price": 380000},
    )
    assert res_price.status_code == 200

    # Hành động 2: Xoá sản phẩm
    res_del_p = client.delete(f"/api/admin/products/{p_id}", headers=admin_auth["headers"])
    assert res_del_p.status_code == 200

    # Tạo đơn hàng để test cập nhật trạng thái và huỷ
    ord_id = create_test_order(
        customer_name="Pham Van C",
        customer_phone="0933445566",
        customer_address="12 Phan Dang Luu, PN",
        status="pending",
        total_amount=600000,
    )

    # Hành động 3: Đổi trạng thái đơn hàng (từ pending sang confirmed)
    res_st = client.put(
        f"/api/admin/orders/{ord_id}/status",
        headers=admin_auth["headers"],
        json={"status": "confirmed"},
    )
    assert res_st.status_code == 200

    # Hành động 4: Huỷ đơn hàng (status = cancelled)
    res_cancel = client.put(
        f"/api/admin/orders/{ord_id}/status",
        headers=admin_auth["headers"],
        json={"status": "cancelled", "internal_note": "Khách yêu cầu huỷ"},
    )
    assert res_cancel.status_code == 200

    # Hành động 5: Khoá tài khoản user
    temp_username = f"lockuser_{uuid.uuid4().hex[:6]}"
    user_created = user_service.register_user(
        name="User Can Khoa",
        username=temp_username,
        email=f"{temp_username}@test.com",
        password="Password123!",
    )
    res_lock = client.put(
        f"/api/admin/users/{user_created.id}/status",
        headers=admin_auth["headers"],
        json={"is_active": False},
    )
    assert res_lock.status_code == 200

    # Lấy danh sách audit log từ API /api/admin/audit-logs
    res_logs = client.get("/api/admin/audit-logs?limit=100", headers=admin_auth["headers"])
    assert res_logs.status_code == 200
    res_data = res_logs.json()
    logs = res_data if isinstance(res_data, list) else res_data.get("items", [])

    actions = [log.get("action") for log in logs]
    assert "change_price" in actions, "Thiếu audit log đổi giá sản phẩm (change_price)"
    assert "delete_product" in actions, "Thiếu audit log xoá sản phẩm (delete_product)"
    assert "update_status" in actions, "Thiếu audit log đổi trạng thái đơn (update_status)"
    assert "cancel_order" in actions, "Thiếu audit log huỷ đơn hàng (cancel_order)"
    assert "lock_user" in actions, "Thiếu audit log khoá user (lock_user)"

    # Kiểm tra chi tiết 1 bản ghi audit log có đầy đủ trường
    price_log = next(l for l in logs if l.get("action") == "change_price" and l.get("target_id") == p_id)
    assert price_log["admin_id"] is not None
    assert price_log["target_type"] == "product"
    assert "300000" in str(price_log["before_state"])
    assert "380000" in str(price_log["after_state"])
    assert price_log["created_at"] is not None


# ============================================================================
# 5. TEST BÁO CÁO LỢI NHUẬN VỚI FROM_DATE / TO_DATE
# ============================================================================

def test_profit_report_date_filtering(admin_auth):
    """
    Kiểm tra GET /api/admin/reports/profit:
    - Nhận tham số from_date và to_date.
    - Trả về đúng cấu trúc và lọc đơn hàng theo khoảng ngày.
    """
    # 1. Gọi không truyền tham số (lấy toàn bộ)
    res_all = client.get("/api/admin/reports/profit", headers=admin_auth["headers"])
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert "total_revenue" in data_all
    assert "gross_profit" in data_all
    assert "products_breakdown" in data_all

    # 2. Gọi có from_date và to_date
    res_filter = client.get(
        "/api/admin/reports/profit?from_date=2026-01-01&to_date=2026-12-31",
        headers=admin_auth["headers"],
    )
    assert res_filter.status_code == 200
    data_filter = res_filter.json()
    assert data_filter["from_date"] == "2026-01-01"
    assert data_filter["to_date"] == "2026-12-31"

    # 3. Lọc khoảng ngày không có đơn (ví dụ năm 2020)
    res_empty = client.get(
        "/api/admin/reports/profit?from_date=2020-01-01&to_date=2020-01-02",
        headers=admin_auth["headers"],
    )
    assert res_empty.status_code == 200
    data_empty = res_empty.json()
    assert data_empty["total_revenue"] == 0
    assert data_empty["gross_profit"] == 0
