import datetime
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "aura_store.db")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_lock = threading.RLock()


def get_db_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Tạo kết nối SQLite có hỗ trợ row_factory và bật WAL mode cho hiệu năng cao."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_db_transaction(db_path: str = DB_PATH):
    """Context manager đảm bảo Transaction ACID (Rollback nếu lỗi, Commit nếu thành công)."""
    conn = get_db_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    return get_db_connection(db_path)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT,
    full_name TEXT,
    phone TEXT,
    address TEXT,
    province TEXT,
    district TEXT,
    ward TEXT,
    role TEXT DEFAULT 'user',
    status TEXT DEFAULT 'active',
    avatar TEXT,
    points_balance INTEGER DEFAULT 0,
    total_spent INTEGER DEFAULT 0,
    tier TEXT DEFAULT 'Silver',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    category_name TEXT,
    gender TEXT,
    price INTEGER NOT NULL,
    original_price INTEGER NOT NULL,
    flash_sale INTEGER DEFAULT 0,
    flash_sale_price INTEGER,
    sold_count INTEGER DEFAULT 0,
    stock INTEGER DEFAULT 50,
    stock_total INTEGER DEFAULT 100,
    rating REAL DEFAULT 5.0,
    reviews_count INTEGER DEFAULT 0,
    location TEXT DEFAULT 'TP. Hồ Chí Minh',
    images TEXT,
    sizes TEXT,
    colors TEXT,
    description TEXT,
    material TEXT,
    style TEXT,
    occasions TEXT,
    tags TEXT,
    is_hot INTEGER DEFAULT 0,
    is_new INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS product_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    color TEXT NOT NULL,
    color_hex TEXT,
    size TEXT NOT NULL,
    stock INTEGER NOT NULL DEFAULT 10,
    sku TEXT,
    created_at TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    UNIQUE(product_id, color, size)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT,
    customer_name TEXT NOT NULL,
    customer_phone TEXT NOT NULL,
    customer_address TEXT NOT NULL,
    province TEXT,
    district TEXT,
    ward TEXT,
    specific_address TEXT,
    customer_note TEXT,
    payment_method TEXT NOT NULL,
    payment_status TEXT DEFAULT 'unpaid',
    order_status TEXT DEFAULT 'pending_payment',
    carrier TEXT DEFAULT 'Giao Hàng Nhanh (GHN)',
    tracking_code TEXT,
    shipping_status TEXT DEFAULT 'ready_to_pick',
    estimated_delivery TEXT,
    total_amount INTEGER NOT NULL,
    subtotal INTEGER NOT NULL,
    shipping_fee INTEGER DEFAULT 0,
    discount_amount INTEGER DEFAULT 0,
    voucher_code TEXT,
    quote_json TEXT,
    paid_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    product_name TEXT,
    color TEXT,
    size TEXT,
    quantity INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    line_total INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payment_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    transaction_code TEXT UNIQUE NOT NULL,
    amount INTEGER NOT NULL,
    payment_channel TEXT DEFAULT 'vietqr',
    status TEXT DEFAULT 'success',
    payload_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    user_id TEXT,
    user_name TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    comment TEXT NOT NULL,
    height_cm REAL,
    weight_kg REAL,
    purchased_size TEXT,
    purchased_color TEXT,
    fit_feedback TEXT DEFAULT 'Vừa vặn',
    is_verified_buyer INTEGER DEFAULT 1,
    likes_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_variants_pid ON product_variants(product_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);

CREATE TABLE IF NOT EXISTS loyalty_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    order_id TEXT,
    points INTEGER NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    balance_after INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_loyalty_user ON loyalty_transactions(user_id);
"""


def init_db(db_path: str = DB_PATH):
    """Khởi tạo cấu trúc bảng SQLite và tự động import dữ liệu từ JSON nếu DB mới."""
    with _lock:
        conn = get_db_connection(db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

            # Tự động cập nhật cột cho users nếu DB đã tạo từ phase trước
            user_cols = [c["name"] for c in conn.execute("PRAGMA table_info(users);").fetchall()]
            if "points_balance" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN points_balance INTEGER DEFAULT 0;")
            if "total_spent" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN total_spent INTEGER DEFAULT 0;")
            if "tier" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'Silver';")
            conn.commit()

            # Tự động cập nhật cột cho orders nếu DB đã tạo từ phase trước
            order_cols = [c["name"] for c in conn.execute("PRAGMA table_info(orders);").fetchall()]
            if "carrier" not in order_cols:
                conn.execute("ALTER TABLE orders ADD COLUMN carrier TEXT DEFAULT 'Giao Hàng Nhanh (GHN)';")
            if "tracking_code" not in order_cols:
                conn.execute("ALTER TABLE orders ADD COLUMN tracking_code TEXT;")
            if "shipping_status" not in order_cols:
                conn.execute("ALTER TABLE orders ADD COLUMN shipping_status TEXT DEFAULT 'ready_to_pick';")
            if "estimated_delivery" not in order_cols:
                conn.execute("ALTER TABLE orders ADD COLUMN estimated_delivery TEXT;")
            conn.commit()

            conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_tracking ON orders(tracking_code);")
            conn.commit()

            # Kiểm tra xem đã có dữ liệu chưa
            cursor = conn.cursor()
            user_count = cursor.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
            prod_count = cursor.execute("SELECT COUNT(*) FROM products;").fetchone()[0]
            review_count = cursor.execute("SELECT COUNT(*) FROM reviews;").fetchone()[0]

            if user_count == 0:
                _migrate_users(conn)
            if prod_count == 0:
                _migrate_products(conn)
            if review_count == 0:
                _migrate_reviews(conn)

            # Khởi tạo điểm thưởng demo cho user usr_002 (Tiến Anh) nếu chưa có giao dịch
            loyalty_count = conn.execute("SELECT COUNT(*) FROM loyalty_transactions WHERE user_id = 'usr_002';").fetchone()[0]
            if loyalty_count == 0:
                conn.execute("""
                    UPDATE users 
                    SET points_balance = 120, total_spent = 6450000, tier = 'Gold' 
                    WHERE id = 'usr_002';
                """)
                now_demo = datetime.datetime.now()
                t1 = (now_demo - datetime.timedelta(days=7)).strftime("%d/%m/%Y %H:%M")
                t2 = (now_demo - datetime.timedelta(days=2)).strftime("%d/%m/%Y %H:%M")
                conn.execute("""
                    INSERT INTO loyalty_transactions (user_id, order_id, points, type, description, balance_after, created_at)
                    VALUES ('usr_002', 'AURA-260315-DEMO1', 150, 'earn', 'Tích điểm từ đơn hàng AURA-260315-DEMO1', 150, ?);
                """, (t1,))
                conn.execute("""
                    INSERT INTO loyalty_transactions (user_id, order_id, points, type, description, balance_after, created_at)
                    VALUES ('usr_002', 'AURA-260320-DEMO2', -30, 'redeem', 'Dùng 30 điểm giảm giá đơn hàng AURA-260320-DEMO2', 120, ?);
                """, (t2,))
                conn.commit()
        finally:
            conn.close()


def _migrate_users(conn: sqlite3.Connection):
    users_file = os.path.join(DATA_DIR, "users.json")
    if not os.path.exists(users_file):
        return
    try:
        with open(users_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for u in data:
            conn.execute(
                """
                INSERT OR IGNORE INTO users 
                (id, username, email, password_hash, name, full_name, phone, address, role, status, avatar, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    u.get("id"),
                    u.get("username"),
                    u.get("email"),
                    u.get("password_hash"),
                    u.get("name"),
                    u.get("full_name") or u.get("name"),
                    u.get("phone"),
                    u.get("address"),
                    u.get("role", "user"),
                    u.get("status", "active"),
                    u.get("avatar"),
                    u.get("created_at") or datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                ),
            )
        conn.commit()
    except Exception as e:
        print(f"Warning: Failed migrating users to SQLite: {e}")


def _migrate_products(conn: sqlite3.Connection):
    products_file = os.path.join(DATA_DIR, "products.json")
    if not os.path.exists(products_file):
        return
    try:
        with open(products_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        now_str = datetime.datetime.now().isoformat()
        for p in data:
            pid = p.get("id")
            colors = p.get("colors") or []
            sizes = p.get("sizes") or []
            total_stock = p.get("stock", 50)

            conn.execute(
                """
                INSERT OR IGNORE INTO products
                (id, name, category, category_name, gender, price, original_price, flash_sale, flash_sale_price,
                 sold_count, stock, stock_total, rating, reviews_count, location, images, sizes, colors,
                 description, material, style, occasions, tags, is_hot, is_new, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    p.get("name"),
                    p.get("category"),
                    p.get("category_name"),
                    p.get("gender"),
                    p.get("price", 0),
                    p.get("original_price", 0),
                    1 if p.get("flash_sale") else 0,
                    p.get("flash_sale_price"),
                    p.get("sold_count", 0),
                    total_stock,
                    p.get("stock_total", 100),
                    p.get("rating", 5.0),
                    p.get("reviews_count", 0),
                    p.get("location", "TP. Hồ Chí Minh"),
                    json.dumps(p.get("images") or [], ensure_ascii=False),
                    json.dumps(sizes, ensure_ascii=False),
                    json.dumps(colors, ensure_ascii=False),
                    p.get("description", ""),
                    p.get("material", ""),
                    p.get("style", ""),
                    json.dumps(p.get("occasions") or [], ensure_ascii=False),
                    json.dumps(p.get("tags") or [], ensure_ascii=False),
                    1 if p.get("is_hot") else 0,
                    1 if p.get("is_new") else 0,
                    now_str,
                ),
            )

            for c_idx, c in enumerate(colors):
                c_name = c.get("name") if isinstance(c, dict) else str(c)
                c_hex = c.get("hex", "#000000") if isinstance(c, dict) else "#000000"
                for s_idx, s in enumerate(sizes):
                    # Giả lập thực tế: một số phân loại size hiếm (như màu phụ hoặc size cuối) hết hàng để test UI
                    is_sold_out = (c_idx == len(colors) - 1 and s_idx == len(sizes) - 1 and len(sizes) > 2)
                    v_stock = 0 if is_sold_out else total_stock
                    v_sku = f"{pid}-{c_name[:2].upper()}-{s}".replace(" ", "")

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO product_variants
                        (product_id, color, color_hex, size, stock, sku, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (pid, c_name, c_hex, s, v_stock, v_sku, now_str),
                    )

        conn.commit()
    except Exception as e:
        print(f"Warning: Failed migrating products to SQLite: {e}")


def _migrate_reviews(conn: sqlite3.Connection):
    """Tạo bộ đánh giá thực tế chân thực có số đo khách hàng (chiều cao, cân nặng, fit feedback)."""
    now = datetime.datetime.now()
    demo_reviews = [
        # prod_001: Áo Blazer Linen Phom Rộng
        ("prod_001", "Nguyễn Thu Hà", 5, "Áo đứng form cực kỳ, chất linen pha cotton thoáng khí không bị nhăn nhúm khi ngồi xe. Mặc đi làm ai cũng khen sang, màu be phối với quần tây hay chân váy đều chuẩn!", 162.0, 48.0, "S", "Be / Kem", "Vừa vặn", 1, 14, 2),
        ("prod_001", "Trần Minh Thư", 5, "Đệm vai mỏng tự nhiên không bị thô, tay áo dài vừa phải. Form oversize vừa vặn chuẩn style Hàn Quốc. Đóng gói hộp rất chỉn chu kèm túi chống ẩm.", 168.0, 54.0, "M", "Nâu Đất", "Vừa vặn", 1, 9, 5),
        ("prod_001", "Đặng Thảo Linh", 4, "Chất vải đẹp, đường may kĩ, lớp lót mát mẻ. Nếu bạn nào người nhỏ xương mảnh nên chọn size nhỏ nhất để đỡ bị thụng quá nhé.", 158.0, 46.0, "S", "Be / Kem", "Hơi rộng nhẹ", 1, 4, 8),
        ("prod_001", "Hoàng Bích Phương", 5, "Mua tặng sinh nhật chị gái, chị thích mê luôn. Chất linen cao cấp sờ mướt tay, mặc hè không bị bí.", 165.0, 52.0, "M", "Đen Tuyển", "Vừa vặn", 1, 6, 12),

        # prod_002: Sơ Mi Lụa Cổ V Thanh Lịch
        ("prod_002", "Hoàng Ngọc Lan", 5, "Chất lụa mềm mịn mướt mát, cổ V vừa phải thanh lịch không bị hở. Mình giặt máy bỏ túi giặt không xước chỉ chút nào.", 165.0, 52.0, "M", "Trắng Ngọc Trai", "Vừa vặn", 1, 18, 3),
        ("prod_002", "Lê Bảo Ngọc", 5, "Màu sắc nhã nhặn tôn da cực kỳ. Mình 50kg mặc size S ôm nhẹ phần eo rất tôn dáng, đi họp hay đi dạy học đều trang nhã.", 160.0, 50.0, "S", "Hồng Pastel", "Vừa vặn", 1, 11, 6),
        ("prod_002", "Vũ Mai Khanh", 4, "Áo đẹp y hình, khuy xà cừ sáng bóng. Điểm cộng là giao hàng siêu nhanh mới 1 ngày đã nhận được.", 157.0, 47.0, "S", "Xanh Mint", "Vừa vặn", 1, 3, 9),

        # prod_003: Váy Maxi Dáng Suông Tơ Tằm
        ("prod_003", "Vũ Thanh Trúc", 5, "Dáng váy thướt tha, đi biển hay chụp ảnh sống ảo đều xuất sắc. Có lớp lót trong dày dặn không lo bị lộ dưới nắng gắt!", 164.0, 53.0, "M", "Xanh Mint", "Vừa vặn", 1, 22, 1),
        ("prod_003", "Phạm Kiều Oanh", 5, "Chất tơ bay bổng nhẹ tênh, eo may thun nhẹ co giãn thoải mái khi ăn no. Rất đáng đồng tiền bát gạo!", 160.0, 49.0, "S", "Be Tự Nhiên", "Vừa vặn", 1, 8, 4),

        # prod_004: Đầm Dự Tiệc Lụa Satin Lệch Vai
        ("prod_004", "Nguyễn Quỳnh Anh", 5, "Mặc đi tiệc cưới ai cũng hỏi mua ở đâu. Lụa satin ánh nhẹ sang trọng, đường xếp nếp khéo léo giấu bụng tốt.", 167.0, 55.0, "M", "Đỏ Rượu Vang", "Vừa vặn", 1, 15, 2),
        ("prod_004", "Đỗ Hương Giang", 5, "Váy ôm trọn đường cong cơ thể, tà xẻ tà quyến rũ vừa phải. Shop tư vấn size rất có tâm!", 163.0, 51.0, "S", "Vàng Champange", "Vừa vặn", 1, 7, 7),

        # prod_005: Áo Thun Cotton Compact 100%
        ("prod_005", "Phạm Tuấn Kiệt", 5, "Vải 280gsm dày dặn đứng form, cổ áo bo tròn dệt 2 lớp kháng gião cực xịn. Mặc giặt nhiều lần vẫn giữ nguyên form áo.", 176.0, 70.0, "L", "Đen Tuyền", "Vừa vặn", 1, 25, 1),
        ("prod_005", "Trần Đình Khang", 5, "Áo cotton mát thấm mồ hôi tốt. Mình 64kg mặc size M dáng regular vừa khít vai, sẽ ủng hộ thêm các màu khác.", 172.0, 64.0, "M", "Trắng Basic", "Vừa vặn", 1, 12, 3),

        # prod_006: Quần Jeans Ống Suông Vintage
        ("prod_006", "Đỗ Mai Chi", 5, "Cạp cao qua rốn che bụng dưới cực tốt, hack chân dài miên man. Denim chuẩn dày dặn không pha thun nhão, màu wash vintage rất Tây!", 163.0, 50.0, "27", "Xanh Vintage Wash", "Vừa vặn", 1, 31, 2),
        ("prod_006", "Bùi Thanh Hằng", 4, "Quần đẹp tôn mông, ống suông rộng vừa phải. Chiều dài ống vừa với giày thể thao hoặc guốc 5 phân.", 159.0, 47.0, "26", "Xanh Vintage Wash", "Vừa vặn", 1, 6, 8),
    ]

    for pid, uname, star, cmt, h, w, sz, col, fit, verified, likes, days_ago in demo_reviews:
        created_time = (now - datetime.timedelta(days=days_ago, hours=days_ago * 2)).strftime("%d/%m/%Y %H:%M")
        conn.execute(
            """
            INSERT INTO reviews 
            (product_id, user_name, rating, comment, height_cm, weight_kg, purchased_size, purchased_color, fit_feedback, is_verified_buyer, likes_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (pid, uname, star, cmt, h, w, sz, col, fit, verified, likes, created_time)
        )

    # Cập nhật rating và reviews_count tương ứng cho các sản phẩm đã có review
    prods = conn.execute("SELECT DISTINCT product_id FROM reviews;").fetchall()
    for row in prods:
        pid = row["product_id"]
        stats = conn.execute("SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM reviews WHERE product_id = ?;", (pid,)).fetchone()
        if stats and stats["cnt"] > 0:
            conn.execute(
                "UPDATE products SET rating = ?, reviews_count = reviews_count + ? WHERE id = ?;",
                (round(float(stats["avg_r"]), 1), stats["cnt"], pid)
            )

    conn.commit()


class DatabaseService:
    """Tập trung các hàm thao tác CSDL chuẩn hóa (ACID, chống Race Condition)."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        init_db(self.db_path)

    def reset_stock(self):
        """Khôi phục lại tồn kho chuẩn từ products.json (dùng khi khởi động hoặc test suite)."""
        products_file = os.path.join(DATA_DIR, "products.json")
        if not os.path.exists(products_file):
            return
        try:
            with open(products_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            with get_db_transaction(self.db_path) as conn:
                for p in data:
                    pid = p.get("id")
                    total_stock = p.get("stock", 50)
                    colors = p.get("colors") or []
                    sizes = p.get("sizes") or []
                    conn.execute(
                        "UPDATE products SET stock = ?, sold_count = ? WHERE id = ?;",
                        (total_stock, p.get("sold_count", 0), pid),
                    )
                    for c_idx, c in enumerate(colors):
                        c_name = c.get("name") if isinstance(c, dict) else str(c)
                        for s_idx, s in enumerate(sizes):
                            is_sold_out = (c_idx == len(colors) - 1 and s_idx == len(sizes) - 1 and len(sizes) > 2)
                            v_stock = 0 if is_sold_out else total_stock
                            conn.execute(
                                "UPDATE product_variants SET stock = ? WHERE product_id = ? AND color = ? AND size = ?;",
                                (v_stock, pid, c_name, s),
                            )
        except Exception as e:
            print(f"Warning: Failed reset_stock: {e}")

    # ==================== VARIANTS & INVENTORY ====================

    def get_product_variants(self, product_id: str) -> List[Dict[str, Any]]:
        """Lấy danh sách ma trận biến thể tồn kho của 1 sản phẩm."""
        conn = get_db_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, product_id, color, color_hex, size, stock, sku FROM product_variants WHERE product_id = ? ORDER BY id ASC;",
                (product_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_variant_stock(self, product_id: str, color: str, size: str) -> Optional[int]:
        """Lấy số lượng tồn kho chính xác của cặp Màu + Size."""
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT stock FROM product_variants WHERE product_id = ? AND color = ? AND size = ?;",
                (product_id, color, size),
            ).fetchone()
            if row:
                return row["stock"]
            # Nếu chưa có bản ghi biến thể riêng, fallback về tồn kho sản phẩm cha
            prod = conn.execute("SELECT stock FROM products WHERE id = ?;", (product_id,)).fetchone()
            return prod["stock"] if prod else None
        finally:
            conn.close()

    def check_and_deduct_variants_stock(self, items: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Trừ tồn kho nguyên tử (Atomic / ACID Transaction) cho toàn bộ giỏ hàng theo Màu + Size.
        Chống Overselling (Bán âm kho) tuyệt đối bằng 'BEGIN IMMEDIATE' và kiểm tra số dư.
        """
        with get_db_transaction(self.db_path) as conn:
            # 1. Khóa và kiểm tra tồn kho cho từng item
            for it in items:
                pid = it["product_id"]
                color = it["color"]
                size = it["size"]
                qty = it["quantity"]

                v = conn.execute(
                    "SELECT stock, sku FROM product_variants WHERE product_id = ? AND color = ? AND size = ?;",
                    (pid, color, size),
                ).fetchone()

                if v is not None:
                    if v["stock"] < qty:
                        return False, f"Phân loại '{color} - Size {size}' chỉ còn {v['stock']} sản phẩm (không đủ cho {qty})"
                else:
                    # Kiểm tra tồn kho sản phẩm cha nếu không có bản ghi biến thể
                    p = conn.execute("SELECT name, stock FROM products WHERE id = ?;", (pid,)).fetchone()
                    if not p:
                        return False, f"Sản phẩm {pid} không tồn tại"
                    if p["stock"] < qty:
                        return False, f"Sản phẩm '{p['name']}' chỉ còn {p['stock']} sản phẩm"

            # 2. Khi tất cả đều đủ, tiến hành trừ tồn kho
            for it in items:
                pid = it["product_id"]
                color = it["color"]
                size = it["size"]
                qty = it["quantity"]

                # Trừ tồn kho biến thể
                conn.execute(
                    "UPDATE product_variants SET stock = stock - ? WHERE product_id = ? AND color = ? AND size = ?;",
                    (qty, pid, color, size),
                )
                # Cập nhật tổng tồn kho và lượt bán của sản phẩm cha
                conn.execute(
                    "UPDATE products SET stock = MAX(0, stock - ?), sold_count = sold_count + ? WHERE id = ?;",
                    (qty, qty, pid),
                )

            return True, None

    # ==================== ORDERS & PAYMENTS ====================

    def save_order(self, order_data: Dict[str, Any]) -> str:
        """Lưu đơn hàng và chi tiết các món vào CSDL với transaction ACID."""
        with get_db_transaction(self.db_path) as conn:
            quote = order_data.get("quote", {})
            customer = order_data.get("customer", {})
            carrier = order_data.get("carrier") or "Giao Hàng Nhanh (GHN Express)"
            order_id = order_data["order_id"]
            tracking_code = order_data.get("tracking_code") or f"GHN-VN-{order_id[-6:]}"
            status = order_data.get("status", "pending_payment")
            
            # Ước tính ngày giao hàng (2-3 ngày sau)
            now = datetime.datetime.now()
            est_date = (now + datetime.timedelta(days=3)).strftime("%d/%m/%Y")
            estimated_delivery = order_data.get("estimated_delivery") or est_date
            shipping_status = order_data.get("shipping_status") or ("ready_to_pick" if status == "confirmed" else "pending_confirm")

            conn.execute(
                """
                INSERT INTO orders 
                (order_id, user_id, customer_name, customer_phone, customer_address,
                 province, district, ward, specific_address, customer_note,
                 payment_method, payment_status, order_status, carrier, tracking_code, shipping_status, estimated_delivery,
                 total_amount, subtotal, shipping_fee, discount_amount, voucher_code, quote_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    order_data.get("user_id"),
                    customer.get("name"),
                    customer.get("phone"),
                    customer.get("address"),
                    customer.get("province"),
                    customer.get("district"),
                    customer.get("ward"),
                    customer.get("specific_address"),
                    customer.get("note"),
                    order_data.get("payment_method"),
                    "paid" if status == "confirmed" and order_data.get("payment_method") != "cod" else "unpaid",
                    status,
                    carrier,
                    tracking_code,
                    shipping_status,
                    estimated_delivery,
                    quote.get("total", 0),
                    quote.get("subtotal", 0),
                    quote.get("shipping_fee", 0),
                    quote.get("discount_amount", 0) or quote.get("voucher_discount", 0) + quote.get("combo_discount", 0),
                    quote.get("voucher_code"),
                    json.dumps(quote, ensure_ascii=False),
                    order_data.get("created_at") or now.isoformat(timespec="seconds"),
                ),
            )

            # Lưu từng line item
            for line in quote.get("lines", []):
                conn.execute(
                    """
                    INSERT INTO order_items 
                    (order_id, product_id, product_name, color, size, quantity, unit_price, line_total)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        line["product_id"],
                        line["name"],
                        line["color"],
                        line["size"],
                        line["quantity"],
                        line["unit_price"],
                        line["line_total"],
                    ),
                )

        return order_id

    def confirm_payment(self, order_id: str, amount: int, transaction_code: str, payment_channel: str = "vietqr") -> bool:
        """Xác nhận thanh toán tự động qua Webhook / IPN."""
        with get_db_transaction(self.db_path) as conn:
            order = conn.execute("SELECT order_id, total_amount, payment_status FROM orders WHERE order_id = ?;", (order_id,)).fetchone()
            if not order:
                return False

            now_str = datetime.datetime.now().isoformat()
            
            # Cập nhật trạng thái đơn hàng & logistics sang sẵn sàng đóng gói
            conn.execute(
                """
                UPDATE orders 
                SET payment_status = 'paid', order_status = 'confirmed', shipping_status = 'ready_to_pick', paid_at = ?
                WHERE order_id = ?;
                """,
                (now_str, order_id),
            )

            # Lưu lịch sử giao dịch thanh toán
            conn.execute(
                """
                INSERT OR IGNORE INTO payment_transactions
                (order_id, transaction_code, amount, payment_channel, status, payload_json, created_at)
                VALUES (?, ?, ?, ?, 'success', ?, ?);
                """,
                (
                    order_id,
                    transaction_code,
                    amount,
                    payment_channel,
                    json.dumps({"verified_at": now_str, "amount": amount}, ensure_ascii=False),
                    now_str,
                ),
            )

            return True

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM orders WHERE order_id = ?;", (order_id,)).fetchone()
            if not row:
                return None
            return self._format_order_row(dict(row))
        finally:
            conn.close()

    def get_order_by_tracking_or_id(self, code: str) -> Optional[Dict[str, Any]]:
        """Tra cứu đơn hàng linh hoạt bằng mã đơn (AURA-...), mã vận đơn (GHN-...), hoặc SĐT."""
        conn = get_db_connection(self.db_path)
        try:
            query = code.strip()
            # 1. Tìm theo order_id
            row = conn.execute("SELECT * FROM orders WHERE order_id = ? COLLATE NOCASE;", (query,)).fetchone()
            if not row:
                # 2. Tìm theo tracking_code
                row = conn.execute("SELECT * FROM orders WHERE tracking_code = ? COLLATE NOCASE;", (query,)).fetchone()
            if not row:
                # 3. Tìm đơn mới nhất theo SĐT người nhận
                clean_phone = "".join(filter(str.isdigit, query))
                if len(clean_phone) >= 9:
                    row = conn.execute(
                        "SELECT * FROM orders WHERE REPLACE(REPLACE(customer_phone, ' ', ''), '-', '') LIKE ? ORDER BY created_at DESC LIMIT 1;",
                        (f"%{clean_phone[-9:]}%",)
                    ).fetchone()
            if not row:
                return None
            return self._format_order_row(dict(row))
        finally:
            conn.close()

    def _format_order_row(self, res: Dict[str, Any]) -> Dict[str, Any]:
        if res.get("quote_json"):
            res["quote"] = json.loads(res["quote_json"])
        res["customer"] = {
            "name": res["customer_name"],
            "phone": res["customer_phone"],
            "address": res["customer_address"],
            "province": res.get("province"),
            "district": res.get("district"),
            "ward": res.get("ward"),
            "note": res.get("customer_note"),
        }
        res["status"] = res["order_status"]
        if not res.get("carrier"):
            res["carrier"] = "Giao Hàng Nhanh (GHN Express)"
        if not res.get("tracking_code"):
            res["tracking_code"] = f"GHN-VN-{res['order_id'][-6:]}"
        if not res.get("shipping_status"):
            res["shipping_status"] = "ready_to_pick" if res["status"] in ["confirmed", "completed"] else "pending_confirm"
        if not res.get("estimated_delivery"):
            res["estimated_delivery"] = "2 - 3 ngày tới"
        return res

    def get_order_payment_status(self, order_id: str) -> Dict[str, Any]:
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT order_id, payment_status, order_status, total_amount, paid_at FROM orders WHERE order_id = ?;",
                (order_id,),
            ).fetchone()
            if not row:
                return {"exists": False}
            return {
                "exists": True,
                "order_id": row["order_id"],
                "payment_status": row["payment_status"],
                "status": row["payment_status"],
                "order_status": row["order_status"],
                "is_paid": row["payment_status"] == "paid",
                "paid_at": row["paid_at"],
            }
        finally:
            conn.close()

    # ==================== REVIEWS & SOCIAL PROOF ====================

    def get_product_reviews(self, product_id: str, rating_filter: Optional[int] = None, limit: int = 50) -> Dict[str, Any]:
        """Lấy danh sách đánh giá kèm tóm tắt số sao (rating breakdown) và số đo người mua."""
        conn = get_db_connection(self.db_path)
        try:
            # 1. Thống kê số sao
            stats_rows = conn.execute(
                "SELECT rating, COUNT(*) as cnt FROM reviews WHERE product_id = ? GROUP BY rating;",
                (product_id,)
            ).fetchall()
            
            breakdown = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
            total_reviews = 0
            total_stars = 0
            for r in stats_rows:
                rating_val = int(r["rating"])
                count = int(r["cnt"])
                if 1 <= rating_val <= 5:
                    breakdown[rating_val] = count
                    total_reviews += count
                    total_stars += rating_val * count

            avg_rating = round(total_stars / total_reviews, 1) if total_reviews > 0 else 5.0

            # 2. Truy vấn danh sách review chi tiết
            query = "SELECT * FROM reviews WHERE product_id = ?"
            params: List[Any] = [product_id]
            if rating_filter and 1 <= rating_filter <= 5:
                query += " AND rating = ?"
                params.append(rating_filter)
            query += " ORDER BY id DESC LIMIT ?;"
            params.append(limit)

            rows = conn.execute(query, tuple(params)).fetchall()
            reviews_list = [dict(r) for r in rows]

            return {
                "summary": {
                    "product_id": product_id,
                    "average_rating": avg_rating,
                    "total_reviews": total_reviews,
                    "rating_breakdown": breakdown,
                    "fit_feedback_summary": "96% khách hàng đánh giá đúng kích cỡ"
                },
                "reviews": reviews_list
            }
        finally:
            conn.close()

    def add_product_review(self, product_id: str, review_data: Dict[str, Any]) -> Dict[str, Any]:
        """Thêm đánh giá mới từ khách hàng và tự động cập nhật lại rating tổng sản phẩm."""
        with get_db_transaction(self.db_path) as conn:
            now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            user_name = review_data.get("user_name", "Khách hàng AURA").strip()
            rating = max(1, min(5, int(review_data.get("rating", 5))))
            comment = review_data.get("comment", "").strip()
            height_cm = review_data.get("height_cm")
            weight_kg = review_data.get("weight_kg")
            purchased_size = review_data.get("purchased_size")
            purchased_color = review_data.get("purchased_color")
            fit_feedback = review_data.get("fit_feedback", "Vừa vặn")

            cursor = conn.execute(
                """
                INSERT INTO reviews 
                (product_id, user_name, rating, comment, height_cm, weight_kg, purchased_size, purchased_color, fit_feedback, is_verified_buyer, likes_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?);
                """,
                (product_id, user_name, rating, comment, height_cm, weight_kg, purchased_size, purchased_color, fit_feedback, now_str)
            )
            new_id = cursor.lastrowid

            # Cập nhật lại rating và reviews_count của sản phẩm cha
            stats = conn.execute(
                "SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM reviews WHERE product_id = ?;",
                (product_id,)
            ).fetchone()
            if stats:
                conn.execute(
                    "UPDATE products SET rating = ?, reviews_count = ? WHERE id = ?;",
                    (round(float(stats["avg_r"]), 1), stats["cnt"], product_id)
                )

            return {
                "id": new_id,
                "product_id": product_id,
                "user_name": user_name,
                "rating": rating,
                "comment": comment,
                "height_cm": height_cm,
                "weight_kg": weight_kg,
                "purchased_size": purchased_size,
                "purchased_color": purchased_color,
                "fit_feedback": fit_feedback,
                "created_at": now_str,
                "is_verified_buyer": 1
            }

    # ==================== LOGISTICS TIMELINE ====================

    def get_tracking_timeline(self, order: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Sinh ra 6 mốc hành trình vận đơn chuyên nghiệp khớp với thời gian thực tế:
        1. ordered -> 2. confirmed -> 3. picking -> 4. in_transit -> 5. delivering -> 6. delivered
        """
        st = order.get("order_status", "pending_payment")
        created_at_raw = order.get("created_at") or datetime.datetime.now().isoformat()
        try:
            # Parse created_at hỗ trợ cả ISO lẫn '%d/%m/%Y %H:%M'
            if "T" in created_at_raw:
                t0 = datetime.datetime.fromisoformat(created_at_raw)
            else:
                t0 = datetime.datetime.strptime(created_at_raw, "%d/%m/%Y %H:%M")
        except Exception:
            t0 = datetime.datetime.now() - datetime.timedelta(hours=2)

        district = order.get("district") or "Quận trung tâm"
        province = order.get("province") or "TP. Hồ Chí Minh"

        # Định nghĩa các mốc
        steps = [
            {
                "key": "ordered",
                "title": "Đặt hàng thành công",
                "description": f"Hệ thống đã tiếp nhận đơn hàng {order.get('order_id')}",
                "location": "AURA Studio Online",
                "time": t0.strftime("%d/%m/%Y %H:%M"),
                "status": "completed"
            },
            {
                "key": "confirmed",
                "title": "Shop xác nhận & Soạn hàng",
                "description": "AURA Studio đã in phiếu xuất kho và kiểm tra chất lượng sản phẩm",
                "location": "Kho tổng AURA (Tân Bình, TP.HCM)",
                "time": (t0 + datetime.timedelta(minutes=35)).strftime("%d/%m/%Y %H:%M"),
                "status": "completed" if st in ["confirmed", "completed"] else "current" if st == "pending_payment" else "pending"
            },
            {
                "key": "picking",
                "title": "Bàn giao Bưu cục vận chuyển",
                "description": f"Bưu tá {order.get('carrier', 'GHN Express')} đã nhận kiện hàng và quét mã vạch",
                "location": "Bưu cục GHN Hub Tân Bình",
                "time": (t0 + datetime.timedelta(hours=3, minutes=10)).strftime("%d/%m/%Y %H:%M"),
                "status": "completed" if st in ["confirmed", "completed"] else "pending"
            },
            {
                "key": "in_transit",
                "title": "Trung chuyển qua kho tổng (SOC)",
                "description": f"Kiện hàng đang luân chuyển qua Trung tâm Phân loại hàng hóa tự động",
                "location": f"Kho trung chuyển liên tỉnh GHN SOC - {province}",
                "time": (t0 + datetime.timedelta(hours=14, minutes=45)).strftime("%d/%m/%Y %H:%M"),
                "status": "completed" if st == "completed" else "current" if st == "confirmed" else "pending"
            },
            {
                "key": "delivering",
                "title": "Shipper đang giao hàng",
                "description": f"Bưu tá đang trên đường giao hàng đến {district}, {province}. Quý khách vui lòng giữ máy!",
                "location": f"Bưu cục phát {district}",
                "time": (t0 + datetime.timedelta(days=1, hours=4)).strftime("%d/%m/%Y %H:%M"),
                "status": "completed" if st == "completed" else "pending"
            },
            {
                "key": "delivered",
                "title": "Giao hàng thành công",
                "description": "Kiện hàng đã được giao thành công đến tay người nhận. Cảm ơn quý khách đã tin chọn AURA Studio!",
                "location": order.get("customer_address") or "Địa chỉ khách hàng",
                "time": (t0 + datetime.timedelta(days=1, hours=6, minutes=20)).strftime("%d/%m/%Y %H:%M"),
                "status": "completed" if st == "completed" else "pending"
            },
        ]
        return steps

    # ==================== LOYALTY & REWARDS (Phase 3) ====================

    def get_user_loyalty(self, user_id: str) -> Dict[str, Any]:
        """Lấy thông tin hạng thành viên, điểm tích lũy, tiến trình thăng hạng và đặc quyền."""
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT id, name, full_name, username, points_balance, total_spent, tier FROM users WHERE id = ?;",
                (user_id,)
            ).fetchone()
            if not row:
                return {
                    "user_id": user_id,
                    "user_name": "Khách hàng",
                    "tier": "Silver",
                    "tier_name": "Hạng Bạc",
                    "tier_badge": "🥈 Bạc",
                    "points_balance": 0,
                    "points_value_vnd": 0,
                    "total_spent": 0,
                    "earn_rate_percent": 3,
                    "free_shipping_all_orders": False,
                    "next_tier": "Gold",
                    "next_tier_name": "Hạng Vàng",
                    "next_tier_spent_needed": 5000000,
                    "progress_percent": 0,
                    "benefits": ["Tích lũy 3% cho mọi đơn hàng", "Voucher ưu đãi tháng sinh nhật"],
                }

            user_name = row["full_name"] or row["name"] or row["username"]
            points = row["points_balance"] or 0
            spent = row["total_spent"] or 0

            # Xác định thông số thăng hạng
            if spent < 5_000_000:
                tier = "Silver"
                tier_name = "Hạng Bạc"
                tier_badge = "🥈 Bạc"
                earn_rate = 3
                free_ship = False
                next_tier = "Gold"
                next_tier_name = "Hạng Vàng"
                needed = max(0, 5_000_000 - spent)
                prog = min(100, int((spent / 5_000_000) * 100))
                benefits = [
                    "Tích lũy 3% giá trị mọi đơn hàng (1 điểm = 1.000đ)",
                    "Voucher 50K ngày sinh nhật",
                    "Miễn phí vận chuyển cho đơn từ 299.000đ",
                ]
            elif spent < 15_000_000:
                tier = "Gold"
                tier_name = "Hạng Vàng"
                tier_badge = "🥇 Vàng"
                earn_rate = 5
                free_ship = True
                next_tier = "Diamond"
                next_tier_name = "Hạng Kim Cương"
                needed = max(0, 15_000_000 - spent)
                prog = min(100, int(((spent - 5_000_000) / 10_000_000) * 100))
                benefits = [
                    "Tích lũy 5% giá trị mọi đơn hàng (1 điểm = 1.000đ)",
                    "Miễn phí vận chuyển toàn quốc cho 100% đơn hàng",
                    "Voucher 150K ngày sinh nhật & Quà tri ân độc quyền",
                    "Ưu tiên đặt trước các bộ sưu tập mới",
                ]
            else:
                tier = "Diamond"
                tier_name = "Hạng Kim Cương"
                tier_badge = "💎 Kim Cương"
                earn_rate = 8
                free_ship = True
                next_tier = None
                next_tier_name = None
                needed = 0
                prog = 100
                benefits = [
                    "Tích lũy 8% giá trị mọi đơn hàng (1 điểm = 1.000đ)",
                    "Miễn phí vận chuyển toàn quốc cho 100% đơn hàng",
                    "Trợ lý AI Stylist thiết kế Lookbook độc bản riêng",
                    "Hộp quà tri ân VIP giới hạn hàng quý",
                    "Ưu tiên kết nối Hotline CSKH VIP 1-1",
                ]

            return {
                "user_id": row["id"],
                "user_name": user_name,
                "tier": tier,
                "tier_name": tier_name,
                "tier_badge": tier_badge,
                "points_balance": points,
                "points_value_vnd": points * 1000,
                "total_spent": spent,
                "earn_rate_percent": earn_rate,
                "free_shipping_all_orders": free_ship,
                "next_tier": next_tier,
                "next_tier_name": next_tier_name,
                "next_tier_spent_needed": needed,
                "progress_percent": prog,
                "benefits": benefits,
            }
        finally:
            conn.close()

    def add_loyalty_points(self, user_id: str, points: int, p_type: str, description: str, order_id: Optional[str] = None) -> int:
        """Cộng điểm thưởng vào tài khoản người dùng và ghi lịch sử biến động."""
        with get_db_transaction(self.db_path) as conn:
            conn.execute("UPDATE users SET points_balance = points_balance + ? WHERE id = ?;", (points, user_id))
            row = conn.execute("SELECT points_balance FROM users WHERE id = ?;", (user_id,)).fetchone()
            new_bal = row["points_balance"] if row else points
            now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            conn.execute(
                """
                INSERT INTO loyalty_transactions (user_id, order_id, points, type, description, balance_after, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (user_id, order_id, points, p_type, description, new_bal, now_str)
            )
            return new_bal

    def deduct_loyalty_points(self, user_id: str, points: int, order_id: str) -> bool:
        """Trừ điểm thưởng khi thanh toán đơn hàng."""
        if points <= 0:
            return True
        with get_db_transaction(self.db_path) as conn:
            row = conn.execute("SELECT points_balance FROM users WHERE id = ?;", (user_id,)).fetchone()
            if not row or row["points_balance"] < points:
                return False
            new_bal = row["points_balance"] - points
            conn.execute("UPDATE users SET points_balance = ? WHERE id = ?;", (new_bal, user_id))
            now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            conn.execute(
                """
                INSERT INTO loyalty_transactions (user_id, order_id, points, type, description, balance_after, created_at)
                VALUES (?, ?, ?, 'redeem', ?, ?, ?);
                """,
                (user_id, order_id, -points, f"Dùng {points} điểm cho đơn hàng {order_id}", new_bal, now_str)
            )
            return True

    def update_user_total_spent_and_tier(self, user_id: str, spent_amount: int) -> Tuple[int, str]:
        """Cộng dồn chi tiêu và tự động thăng hạng thẻ VIP."""
        with get_db_transaction(self.db_path) as conn:
            conn.execute("UPDATE users SET total_spent = total_spent + ? WHERE id = ?;", (spent_amount, user_id))
            row = conn.execute("SELECT total_spent FROM users WHERE id = ?;", (user_id,)).fetchone()
            total_spent = row["total_spent"] if row else spent_amount
            new_tier = "Silver"
            if total_spent >= 15_000_000:
                new_tier = "Diamond"
            elif total_spent >= 5_000_000:
                new_tier = "Gold"
            conn.execute("UPDATE users SET tier = ? WHERE id = ?;", (new_tier, user_id))
            return total_spent, new_tier

    def get_loyalty_history(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Lấy danh sách lịch sử biến động điểm thưởng."""
        conn = get_db_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT id, user_id, order_id, points, type, description, balance_after, created_at 
                FROM loyalty_transactions 
                WHERE user_id = ? 
                ORDER BY id DESC LIMIT ?;
                """,
                (user_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


db_service = DatabaseService()

