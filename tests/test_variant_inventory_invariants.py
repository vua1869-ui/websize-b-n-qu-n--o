import pytest
import sqlite3
import datetime
from app.db.database import db_service, get_db_connection, get_db_transaction
from app.services.product_service import product_service
from app.services.order_service import OrderService, OrderError
from app.models.schemas import OrderItem


def get_product_stock_and_variant_sum(product_id: str):
    """Truy vấn trực tiếp từ SQLite để kiểm tra tính bất biến."""
    conn = get_db_connection(db_service.db_path)
    try:
        p_row = conn.execute("SELECT stock FROM products WHERE id = ?;", (product_id,)).fetchone()
        v_sum_row = conn.execute("SELECT SUM(stock) as total FROM product_variants WHERE product_id = ?;", (product_id,)).fetchone()
        p_stock = p_row["stock"] if p_row else None
        v_sum = v_sum_row["total"] if v_sum_row and v_sum_row["total"] is not None else 0
        return p_stock, v_sum
    finally:
        conn.close()


def test_invariant_init_migration_syncs_stock():
    """Kiểm tra mọi sản phẩm trong DB đều có products.stock == SUM(product_variants.stock)."""
    conn = get_db_connection(db_service.db_path)
    try:
        rows = conn.execute("""
            SELECT p.id, p.name, p.stock as p_stock, COALESCE(SUM(pv.stock), 0) as v_sum
            FROM products p
            LEFT JOIN product_variants pv ON p.id = pv.product_id
            GROUP BY p.id;
        """).fetchall()

        assert len(rows) > 0, "Phải có sản phẩm trong cơ sở dữ liệu"
        for r in rows:
            assert r["p_stock"] == r["v_sum"], (
                f"Sản phẩm {r['id']} ({r['name']}) vi phạm tính bất biến: "
                f"p.stock={r['p_stock']} != SUM(variants)={r['v_sum']}"
            )
    finally:
        conn.close()


def test_invariant_after_create_and_cancel_order():
    """
    Tạo đơn hàng trừ kho biến thể -> Kiểm tra p.stock == SUM(variants).
    Hủy đơn hàng hoàn kho biến thể -> Kiểm tra p.stock == SUM(variants).
    """
    prod_id = "prod_001"
    conn = get_db_connection(db_service.db_path)
    try:
        # Lấy một biến thể còn hàng
        var = conn.execute(
            "SELECT color, size, stock FROM product_variants WHERE product_id = ? AND stock >= 2 LIMIT 1;",
            (prod_id,)
        ).fetchone()
        assert var is not None, "Cần ít nhất 1 biến thể có stock >= 2"
        color = var["color"]
        size = var["size"]
        initial_var_stock = var["stock"]
    finally:
        conn.close()

    p_initial, v_initial = get_product_stock_and_variant_sum(prod_id)
    assert p_initial == v_initial, "Trước khi tạo đơn: p.stock phải bằng SUM(variants)"

    order_id = f"TEST-INV-{datetime.datetime.now().strftime('%H%M%S%f')}"
    order_record = {
        "order_id": order_id,
        "user_id": None,
        "customer": {
            "name": "Test Invariant User",
            "phone": "0987654321",
            "address": "123 Le Loi, Q1",
        },
        "payment_method": "cod",
        "payment_status": "pending",
        "order_status": "pending",
        "status": "pending",
        "quote": {"total": 500000, "subtotal": 500000, "shipping_fee": 0},
    }
    items = [
        {"product_id": prod_id, "color": color, "size": size, "quantity": 2, "price": 250000}
    ]

    # 1. Tạo đơn hàng nguyên tử
    res_id = db_service.create_order_atomic(order_record=order_record, items=items)
    assert res_id == order_id

    # Kiểm tra tồn kho sau tạo đơn
    p_after_create, v_after_create = get_product_stock_and_variant_sum(prod_id)
    assert p_after_create == v_after_create, "Sau tạo đơn: p.stock phải bằng SUM(variants)"
    assert p_after_create == p_initial - 2, "Tồn kho cha phải giảm đúng 2 sản phẩm"
    assert v_after_create == v_initial - 2, "Tổng biến thể phải giảm đúng 2 sản phẩm"

    # Kiểm tra biến thể cụ thể
    conn = get_db_connection(db_service.db_path)
    try:
        cur_v = conn.execute(
            "SELECT stock FROM product_variants WHERE product_id = ? AND color = ? AND size = ?;",
            (prod_id, color, size)
        ).fetchone()
        assert cur_v["stock"] == initial_var_stock - 2
    finally:
        conn.close()

    # 2. Hủy đơn hàng nguyên tử
    cancel_ok = db_service.cancel_order_atomic(order_id)
    assert cancel_ok is True

    # Kiểm tra tồn kho sau hủy đơn
    p_after_cancel, v_after_cancel = get_product_stock_and_variant_sum(prod_id)
    assert p_after_cancel == v_after_cancel, "Sau hủy đơn: p.stock phải bằng SUM(variants)"
    assert p_after_cancel == p_initial, "Tồn kho cha phải được hoàn lại nguyên vẹn"
    assert v_after_cancel == v_initial, "Tổng biến thể phải được hoàn lại nguyên vẹn"


def test_invariant_after_add_inventory_batch_variant():
    """
    Nhập lô hàng theo biến thể cụ thể:
    - Biến thể đó tăng đúng quantity.
    - p.stock == SUM(variants).
    - Lịch sử lô ghi nhận đúng color và size.
    """
    prod_id = "prod_002"
    conn = get_db_connection(db_service.db_path)
    try:
        var = conn.execute(
            "SELECT color, size, stock FROM product_variants WHERE product_id = ? LIMIT 1;",
            (prod_id,)
        ).fetchone()
        color = var["color"]
        size = var["size"]
        var_stock_before = var["stock"]
    finally:
        conn.close()

    p_before, v_before = get_product_stock_and_variant_sum(prod_id)
    assert p_before == v_before

    batch = db_service.add_inventory_batch(
        product_id=prod_id,
        quantity=15,
        cost_price=120000,
        color=color,
        size=size,
        note="Nhập test biến thể",
        created_by="tester",
    )

    assert batch["quantity"] == 15
    assert batch["color"] == color
    assert batch["size"] == size

    p_after, v_after = get_product_stock_and_variant_sum(prod_id)
    assert p_after == v_after, "Sau nhập lô biến thể: p.stock phải bằng SUM(variants)"
    assert p_after == p_before + 15
    assert v_after == v_before + 15

    # Kiểm tra biến thể cụ thể đã tăng đúng 15
    conn = get_db_connection(db_service.db_path)
    try:
        cur_v = conn.execute(
            "SELECT stock FROM product_variants WHERE product_id = ? AND color = ? AND size = ?;",
            (prod_id, color, size)
        ).fetchone()
        assert cur_v["stock"] == var_stock_before + 15

        # Kiểm tra bảng inventory_batches lưu đúng color, size
        ib_row = conn.execute(
            "SELECT color, size FROM inventory_batches WHERE id = ?;",
            (batch["id"],)
        ).fetchone()
        assert ib_row["color"] == color
        assert ib_row["size"] == size
    finally:
        conn.close()


def test_invariant_after_add_inventory_batch_general():
    """
    Nhập lô hàng không chỉ định biến thể (chung):
    - Chia đều cho các biến thể.
    - p.stock == SUM(variants).
    """
    prod_id = "prod_003"
    p_before, v_before = get_product_stock_and_variant_sum(prod_id)
    assert p_before == v_before

    batch = db_service.add_inventory_batch(
        product_id=prod_id,
        quantity=20,
        cost_price=150000,
        color=None,
        size=None,
        note="Nhập tổng chia đều",
        created_by="tester",
    )

    assert batch["quantity"] == 20
    assert batch["color"] is None
    assert batch["size"] is None

    p_after, v_after = get_product_stock_and_variant_sum(prod_id)
    assert p_after == v_after, "Sau nhập lô chung: p.stock phải bằng SUM(variants)"
    assert p_after == p_before + 20
    assert v_after == v_before + 20


def test_quote_fails_when_variant_out_of_stock_even_if_total_has_stock():
    """
    Kiểm tra OrderService.build_quote:
    Nếu biến thể (color, size) hết hàng (stock = 0), quote phải raise OrderError(409)
    ngay cả khi tổng tồn kho của sản phẩm cha (p.stock) vẫn còn > 0.
    """
    prod_id = "prod_001"
    p = product_service.get_by_id(prod_id)
    assert p is not None
    assert p.stock > 0, "Sản phẩm cha phải còn tồn kho"

    # Tìm hoặc tạo 1 biến thể có stock = 0
    conn = get_db_connection(db_service.db_path)
    try:
        zero_var = conn.execute(
            "SELECT color, size FROM product_variants WHERE product_id = ? AND stock = 0 LIMIT 1;",
            (prod_id,)
        ).fetchone()

        if not zero_var:
            # Gán tạm thời 1 biến thể về 0
            any_var = conn.execute(
                "SELECT color, size FROM product_variants WHERE product_id = ? LIMIT 1;",
                (prod_id,)
            ).fetchone()
            conn.execute(
                "UPDATE product_variants SET stock = 0 WHERE product_id = ? AND color = ? AND size = ?;",
                (prod_id, any_var["color"], any_var["size"])
            )
            # Đồng bộ lại p.stock
            conn.execute(
                "UPDATE products SET stock = (SELECT SUM(stock) FROM product_variants WHERE product_id = ?) WHERE id = ?;",
                (prod_id, prod_id)
            )
            conn.commit()
            test_color = any_var["color"]
            test_size = any_var["size"]
        else:
            test_color = zero_var["color"]
            test_size = zero_var["size"]
    finally:
        conn.close()

    # Nạp lại product_service để thấy biến thể hết hàng
    product_service._load_products()
    refreshed_p = product_service.get_by_id(prod_id)
    assert refreshed_p.stock > 0, "Tổng kho cha vẫn phải còn > 0"

    order_service = OrderService()
    items = [
        OrderItem(product_id=prod_id, color=test_color, size=test_size, quantity=1)
    ]

    with pytest.raises(OrderError) as exc_info:
        order_service.build_quote(items)

    assert exc_info.value.status_code == 409
    assert test_color in exc_info.value.message
    assert test_size in exc_info.value.message
    assert "đã hết hàng" in exc_info.value.message


def test_admin_low_stock_variants_api(client):
    """Kiểm tra API /api/admin/variants/low-stock trả về danh sách biến thể đúng ngưỡng."""
    # Đăng nhập với admin
    login_res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert login_res.status_code == 200

    res = client.get("/api/admin/variants/low-stock?threshold=5")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)

    for item in data:
        assert "product_id" in item
        assert "color" in item
        assert "size" in item
        assert "stock" in item
        assert item["stock"] <= 5, f"Biến thể có tồn kho {item['stock']} vượt quá ngưỡng 5"
