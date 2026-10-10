import datetime
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import log_money_event

logger = logging.getLogger("aura.database")


class PaymentConfirmResult(str):
    """Chuỗi kết quả xác nhận thanh toán, hỗ trợ đánh giá Boolean cho tương thích ngược."""
    def __bool__(self):
        return self in ("success", "already_paid")


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "aura_store.db")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_lock = threading.RLock()


def normalize_phone(p: Optional[str]) -> str:
    """Chuẩn hóa số điện thoại về dạng số thuần túy (loại bỏ khoảng trắng, dấu cộng, v.v.)."""
    if not p:
        return ""
    digits = "".join(c for c in str(p) if c.isdigit())
    if digits.startswith("84") and len(digits) > 9:
        digits = "0" + digits[2:]
    return digits


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
    token_version INTEGER DEFAULT 1,
    password_changed_at REAL,
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
    customer_email TEXT,
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
    loyalty_awarded INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS inventory_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    color TEXT,
    size TEXT,
    quantity INTEGER NOT NULL,
    cost_price INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    note TEXT,
    created_by TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_inventory_batches_pid ON inventory_batches(product_id);

CREATE TABLE IF NOT EXISTS vouchers (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    max_discount INTEGER,
    min_order INTEGER NOT NULL DEFAULT 0,
    badge TEXT NOT NULL DEFAULT 'AURA',
    expire_in TEXT NOT NULL DEFAULT 'Còn hiệu lực',
    expires_at TEXT,
    max_uses INTEGER,
    max_uses_per_user INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voucher_usages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voucher_code TEXT NOT NULL,
    user_id TEXT,
    phone TEXT,
    order_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voucher_usages_code ON voucher_usages(voucher_code);
CREATE INDEX IF NOT EXISTS idx_voucher_usages_user ON voucher_usages(user_id);
CREATE INDEX IF NOT EXISTS idx_voucher_usages_order ON voucher_usages(order_id);
"""


def init_db(db_path: str = DB_PATH):
    """Khởi tạo cấu trúc bảng SQLite và tự động import dữ liệu từ JSON nếu DB mới."""
    try:
        from app.db.models import Base
        from app.db.session import engine
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass

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
            if "token_version" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 1;")
            if "password_changed_at" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN password_changed_at REAL;")
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
            if "loyalty_awarded" not in order_cols:
                conn.execute("ALTER TABLE orders ADD COLUMN loyalty_awarded INTEGER DEFAULT 0;")
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

            # Tự động cập nhật cột phone cho voucher_usages nếu thiếu
            vu_cols = [c["name"] for c in conn.execute("PRAGMA table_info(voucher_usages);").fetchall()]
            if "phone" not in vu_cols:
                conn.execute("ALTER TABLE voucher_usages ADD COLUMN phone TEXT;")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voucher_usages_phone ON voucher_usages(phone);")
            conn.commit()

            # Seed bảng vouchers nếu chưa có dữ liệu
            voucher_count = conn.execute("SELECT COUNT(*) FROM vouchers;").fetchone()[0]
            if voucher_count == 0:
                _migrate_vouchers(conn)

            # Chỉ khởi tạo điểm thưởng demo cho user usr_002 (Tiến Anh) khi ở môi trường dev
            from app.config import settings
            if getattr(settings, "APP_ENV", "dev").strip().lower() == "dev":
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

            # Đồng bộ cấu trúc lô hàng và biến thể tồn kho (Single Source of Truth)
            _migrate_sync_variants_and_products_stock(conn)
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
        logger.warning("Failed migrating users to SQLite: %s", e)


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

            def_color_name = (colors[0].get("name") if isinstance(colors[0], dict) else str(colors[0])) if colors else ""
            has_s = "S" in sizes
            has_m = "M" in sizes
            s_alloc = min(3, max(1, total_stock - 1)) if (has_s and has_m and total_stock > 1) else 0

            for c_idx, c in enumerate(colors):
                c_name = c.get("name") if isinstance(c, dict) else str(c)
                c_hex = c.get("hex", "#000000") if isinstance(c, dict) else "#000000"
                for s_idx, s in enumerate(sizes):
                    if c_name == def_color_name:
                        if has_s and has_m:
                            if s == "S":
                                v_stock = s_alloc
                            elif s == "M":
                                v_stock = total_stock - s_alloc
                            else:
                                v_stock = 0
                        else:
                            v_stock = total_stock if (s == sizes[0]) else 0
                    else:
                        v_stock = 0
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
        logger.warning("Failed migrating products to SQLite: %s", e)


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


def _migrate_vouchers(conn: sqlite3.Connection):
    """Seed danh sách voucher chuẩn từ AVAILABLE_VOUCHERS vào bảng vouchers trong CSDL."""
    now_str = datetime.datetime.now().isoformat()
    default_vouchers = [
        ("FREESHIP", "Miễn phí vận chuyển toàn quốc", "shipping", 0, None, 0, "Freeship", "Còn hiệu lực", None, 1000, 5, 1, now_str),
        ("AURA50K", "Ưu đãi đơn từ 299K", "amount", 50000, None, 299000, "AURA STUDIO", "Trong tháng này", None, 500, 1, 1, now_str),
        ("LIVE20", "Độc quyền từ phòng Live AI", "percent", 20, 100000, 199000, "Live AI", "Trong tháng này", None, 200, 1, 1, now_str),
        ("AURA10", "Khách mới trải nghiệm AI Stylist", "percent", 10, 50000, 150000, "Khách mới", "30 ngày", None, 500, 1, 1, now_str),
        ("STAYWITHUS", "Quà tặng giữ chân khách hàng - Giảm 5%", "percent", 5, 100000, 100000, "Tri ân 5%", "Hôm nay", None, 300, 1, 1, now_str),
    ]
    for row in default_vouchers:
        conn.execute(
            """
            INSERT OR IGNORE INTO vouchers 
            (code, title, kind, value, max_discount, min_order, badge, expire_in, expires_at, max_uses, max_uses_per_user, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            row
        )
    conn.commit()


def _migrate_sync_variants_and_products_stock(conn: sqlite3.Connection):
    """
    Migration & Invariant enforcer:
    1. Đảm bảo bảng inventory_batches có cột color, size.
    2. Đảm bảo mọi sản phẩm đều có các bản ghi biến thể tương ứng trong product_variants.
    3. Thiết lập Single Source of Truth: products.stock = SUM(product_variants.stock).
    """
    try:
        # 1. Cập nhật cột color, size cho inventory_batches và customer_email cho orders nếu thiếu
        ib_cols = [c["name"] for c in conn.execute("PRAGMA table_info(inventory_batches);").fetchall()]
        if "color" not in ib_cols:
            conn.execute("ALTER TABLE inventory_batches ADD COLUMN color TEXT;")
        if "size" not in ib_cols:
            conn.execute("ALTER TABLE inventory_batches ADD COLUMN size TEXT;")

        orders_cols = [c["name"] for c in conn.execute("PRAGMA table_info(orders);").fetchall()]
        if "customer_email" not in orders_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN customer_email TEXT;")

        # 2. Rà soát tất cả sản phẩm
        prods = conn.execute("SELECT id, colors, sizes, stock, stock_total FROM products;").fetchall()
        now_str = datetime.datetime.now().isoformat()

        for p in prods:
            pid = p["id"]
            var_count = conn.execute("SELECT COUNT(*) FROM product_variants WHERE product_id = ?;", (pid,)).fetchone()[0]
            if var_count == 0:
                try:
                    c_list = json.loads(p["colors"]) if p["colors"] else []
                except Exception:
                    c_list = []
                try:
                    s_list = json.loads(p["sizes"]) if p["sizes"] else []
                except Exception:
                    s_list = []

                if not c_list:
                    c_list = [{"name": "Tiêu chuẩn", "hex": "#000000"}]
                if not s_list:
                    s_list = ["Freesize"]

                num_vars = max(1, len(c_list) * len(s_list))
                base_stock = p["stock"] if p["stock"] is not None else 10
                per_var = base_stock // num_vars
                rem = base_stock % num_vars

                var_idx = 0
                for c in c_list:
                    c_name = c.get("name") if isinstance(c, dict) else str(c)
                    c_hex = c.get("hex", "#000000") if isinstance(c, dict) else "#000000"
                    for s in s_list:
                        s_str = str(s)
                        v_stock = per_var + (rem if var_idx == 0 else 0)
                        v_sku = f"{pid}-{c_name[:2].upper()}-{s_str}".replace(" ", "")
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO product_variants 
                            (product_id, color, color_hex, size, stock, sku, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?);
                            """,
                            (pid, c_name, c_hex, s_str, max(0, v_stock), v_sku, now_str),
                        )
                        var_idx += 1

        # 3. Đồng bộ Single Source of Truth: products.stock = SUM(product_variants.stock)
        conn.execute("""
            UPDATE products 
            SET stock = COALESCE((
                SELECT SUM(stock) FROM product_variants WHERE product_id = products.id
            ), 0);
        """)
        conn.commit()
    except Exception as e:
        logger.warning("Lỗi migrate sync variants & stock: %s", e)


class DatabaseService:
    """Tập trung các hàm thao tác CSDL chuẩn hóa (ACID, chống Race Condition)."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        init_db(self.db_path)

    def init_db(self, db_path: Optional[str] = None):
        """Khởi tạo cấu trúc bảng SQLite và seed dữ liệu nếu cần."""
        init_db(db_path or self.db_path)

    def seed_reset_stock(self):
        """Khôi phục lại tồn kho chuẩn từ products.json (chỉ dùng tiện ích cho test suite hoặc script seed, không tự chạy khi khởi động)."""
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
                    def_color_name = (colors[0].get("name") if isinstance(colors[0], dict) else str(colors[0])) if colors else ""
                    has_s = "S" in sizes
                    has_m = "M" in sizes
                    s_alloc = min(3, max(1, total_stock - 1)) if (has_s and has_m and total_stock > 1) else 0

                    for c_idx, c in enumerate(colors):
                        c_name = c.get("name") if isinstance(c, dict) else str(c)
                        for s_idx, s in enumerate(sizes):
                            if c_name == def_color_name:
                                if has_s and has_m:
                                    if s == "S":
                                        v_stock = s_alloc
                                    elif s == "M":
                                        v_stock = total_stock - s_alloc
                                    else:
                                        v_stock = 0
                                else:
                                    v_stock = total_stock if (s == sizes[0]) else 0
                            else:
                                v_stock = 0
                            conn.execute(
                                "UPDATE product_variants SET stock = ? WHERE product_id = ? AND color = ? AND size = ?;",
                                (v_stock, pid, c_name, s),
                            )
                    # Thiết lập products.stock = SUM(product_variants.stock)
                    conn.execute(
                        "UPDATE products SET stock = (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = ?), sold_count = ? WHERE id = ?;",
                        (pid, p.get("sold_count", 0), pid),
                    )
        except Exception as e:
            logger.warning("Failed seed_reset_stock: %s", e)

    def reset_stock(self):
        return self.seed_reset_stock()

    def get_voucher(self, code: str) -> Optional[Any]:
        if not code:
            return None
        code = code.strip().upper()
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM vouchers WHERE code = ? AND is_active = 1;", (code,)).fetchone()
            if not row:
                return None
            from app.models.schemas import Voucher
            data = dict(row)
            if data.get("expires_at"):
                try:
                    data["expires_at"] = datetime.datetime.fromisoformat(data["expires_at"])
                except Exception:
                    pass
            return Voucher(**data)
        finally:
            conn.close()

    def get_all_vouchers(self) -> List[Any]:
        conn = get_db_connection(self.db_path)
        try:
            rows = conn.execute("SELECT * FROM vouchers WHERE is_active = 1;").fetchall()
            from app.models.schemas import Voucher
            out = []
            for r in rows:
                data = dict(r)
                if data.get("expires_at"):
                    try:
                        data["expires_at"] = datetime.datetime.fromisoformat(data["expires_at"])
                    except Exception:
                        pass
                out.append(Voucher(**data))
            return out
        finally:
            conn.close()

    def save_voucher(self, v: Any) -> None:
        conn = get_db_connection(self.db_path)
        try:
            exp_str = v.expires_at.isoformat() if isinstance(getattr(v, "expires_at", None), datetime.datetime) else getattr(v, "expires_at", None)
            now_str = datetime.datetime.now().isoformat()
            code = v.code.strip().upper()
            conn.execute(
                """
                INSERT INTO vouchers 
                (code, title, kind, value, max_discount, min_order, badge, expire_in, expires_at, max_uses, max_uses_per_user, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(code) DO UPDATE SET
                    title=excluded.title, kind=excluded.kind, value=excluded.value,
                    max_discount=excluded.max_discount, min_order=excluded.min_order,
                    badge=excluded.badge, expire_in=excluded.expire_in,
                    expires_at=excluded.expires_at, max_uses=excluded.max_uses,
                    max_uses_per_user=excluded.max_uses_per_user, is_active=1;
                """,
                (
                    code, v.title, v.kind, v.value, v.max_discount, v.min_order,
                    v.badge, v.expire_in, exp_str, v.max_uses, v.max_uses_per_user, now_str
                )
            )
            conn.commit()
        finally:
            conn.close()

    def reset_vouchers(self) -> None:
        conn = get_db_connection(self.db_path)
        try:
            conn.execute("DELETE FROM vouchers;")
            conn.execute("DELETE FROM voucher_usages;")
            conn.commit()
            _migrate_vouchers(conn)
        finally:
            conn.close()

    def get_product_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM products WHERE id = ?;", (product_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_products_db(self) -> Dict[str, Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            rows = conn.execute("SELECT id, stock, sold_count, rating, reviews_count FROM products;").fetchall()
            return {r["id"]: dict(r) for r in rows}
        finally:
            conn.close()

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

    def confirm_payment(self, order_id: str, amount: int, transaction_code: str, payment_channel: str = "vietqr") -> PaymentConfirmResult:
        """Xác nhận thanh toán tự động qua Webhook / IPN trong một transaction BEGIN IMMEDIATE."""
        with get_db_transaction(self.db_path) as conn:
            order = conn.execute(
                "SELECT order_id, user_id, total_amount, payment_status, order_status, quote_json, loyalty_awarded FROM orders WHERE order_id = ?;",
                (order_id,)
            ).fetchone()
            if not order:
                return PaymentConfirmResult("not_found")

            # 1. Kiểm tra tính Idempotent: Giao dịch này cho đơn hàng này đã từng xử lý chưa
            tx_row = conn.execute(
                "SELECT id, order_id, status FROM payment_transactions WHERE transaction_code = ? AND order_id = ?;",
                (transaction_code, order_id)
            ).fetchone()
            if tx_row:
                return PaymentConfirmResult("already_paid")

            # 2. Xử lý đơn cancelled mà nhận tiền
            if order["order_status"] == "cancelled":
                now_str = datetime.datetime.now().isoformat()
                conn.execute(
                    """
                    UPDATE orders 
                    SET payment_status = 'refund_pending'
                    WHERE order_id = ?;
                    """,
                    (order_id,),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO payment_transactions
                    (order_id, transaction_code, amount, payment_channel, status, payload_json, created_at)
                    VALUES (?, ?, ?, ?, 'unmatched_or_cancelled', ?, ?);
                    """,
                    (
                        order_id,
                        transaction_code,
                        amount,
                        payment_channel,
                        json.dumps({"verified_at": now_str, "amount": amount, "reason": "order_cancelled_received_payment"}, ensure_ascii=False),
                        now_str,
                    ),
                )
                logger.warning(
                    "CẢNH BÁO HOÀN TIỀN ADMIN: Đơn hàng '%s' nhận thanh toán %s đ khi đã hủy! Đã ghi nhận transaction '%s' (unmatched_or_cancelled) và chuyển payment_status='refund_pending'",
                    order_id, amount, transaction_code,
                )
                log_money_event(
                    "payment_received_on_cancelled_order",
                    order_id=order_id,
                    user_id=order["user_id"] if "user_id" in order.keys() else None,
                    amount=amount,
                    extra={"transaction_code": transaction_code, "action": "refund_pending", "payment_channel": payment_channel},
                )
                return PaymentConfirmResult("cancelled")

            expected_amount = int(order["total_amount"])
            if int(amount) != expected_amount:
                logger.warning(f"Reject payment confirmation for order '{order_id}': amount mismatch (got {amount}, expected {expected_amount})")
                log_money_event(
                    "payment_rejected_amount_mismatch",
                    order_id=order_id,
                    user_id=order["user_id"] if "user_id" in order.keys() else None,
                    amount=amount,
                    extra={"expected_amount": expected_amount, "transaction_code": transaction_code},
                )
                return PaymentConfirmResult("invalid_amount")

            if order["payment_status"] == "paid":
                return PaymentConfirmResult("already_paid")

            now_str = datetime.datetime.now().isoformat()
            current_status = order["order_status"]

            # 3. Cập nhật trạng thái: Đơn shipping/completed chỉ cập nhật payment_status + paid_at, KHÔNG ghi đè order_status/shipping_status
            if current_status in ("shipping", "completed", "confirmed"):
                conn.execute(
                    """
                    UPDATE orders 
                    SET payment_status = 'paid', paid_at = ?
                    WHERE order_id = ?;
                    """,
                    (now_str, order_id),
                )
            else:
                # Chỉ đơn pending_payment mới chuyển sang confirmed và ready_to_pick
                conn.execute(
                    """
                    UPDATE orders 
                    SET payment_status = 'paid', order_status = 'confirmed', shipping_status = 'ready_to_pick', paid_at = ?
                    WHERE order_id = ?;
                    """,
                    (now_str, order_id),
                )

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

            # Đơn thanh toán online: cộng điểm và total_spent khi chuyển sang paid
            self._award_order_loyalty_and_spent(order_id, conn=conn)

            log_money_event(
                "payment_confirmed",
                order_id=order_id,
                user_id=order["user_id"] if "user_id" in order.keys() else None,
                amount=amount,
                extra={"transaction_code": transaction_code, "payment_channel": payment_channel, "previous_order_status": current_status},
            )

            return PaymentConfirmResult("success")

    def _award_order_loyalty_and_spent(self, order_id: str, conn: Optional[sqlite3.Connection] = None):
        """Cộng điểm thưởng và total_spent cho người dùng khi đơn hàng được thanh toán (chỉ chạy 1 lần duy nhất)."""
        def _do_award(c):
            row = c.execute(
                "SELECT order_id, user_id, quote_json, total_amount, loyalty_awarded FROM orders WHERE order_id = ?;",
                (order_id,)
            ).fetchone()
            if not row or not row["user_id"]:
                return
            if row["loyalty_awarded"] and row["loyalty_awarded"] == 1:
                return

            user_id = row["user_id"]
            points_earned = 0
            net_spent = int(row["total_amount"])
            if row["quote_json"]:
                try:
                    q = json.loads(row["quote_json"])
                    points_earned = q.get("points_earned", 0)
                    net_spent = max(0, q.get("subtotal", 0) - q.get("combo_discount", 0) - q.get("voucher_discount", 0) - q.get("points_discount", 0))
                except Exception:
                    pass

            # Cập nhật total_spent và tính lại tier
            c.execute("UPDATE users SET total_spent = total_spent + ? WHERE id = ?;", (net_spent, user_id))
            u_row = c.execute("SELECT total_spent FROM users WHERE id = ?;", (user_id,)).fetchone()
            if u_row:
                tot = u_row["total_spent"]
                tier = "Diamond" if tot >= 15000000 else "Gold" if tot >= 5000000 else "Silver"
                c.execute("UPDATE users SET tier = ? WHERE id = ?;", (tier, user_id))

            # Cộng điểm thưởng
            if points_earned > 0:
                c.execute("UPDATE users SET points_balance = points_balance + ? WHERE id = ?;", (points_earned, user_id))
                bal_row = c.execute("SELECT points_balance FROM users WHERE id = ?;", (user_id,)).fetchone()
                bal_after = bal_row["points_balance"] if bal_row else 0
                now_dt = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                c.execute(
                    """
                    INSERT INTO loyalty_transactions 
                    (user_id, order_id, points, type, description, balance_after, created_at)
                    VALUES (?, ?, ?, 'earn', ?, ?, ?);
                    """,
                    (user_id, order_id, points_earned, f"Tích điểm từ đơn hàng {order_id}", bal_after, now_dt)
                )

            c.execute("UPDATE orders SET loyalty_awarded = 1 WHERE order_id = ?;", (order_id,))

        if conn:
            _do_award(conn)
        else:
            with get_db_transaction(self.db_path) as c:
                _do_award(c)

    def create_order_atomic(
        self,
        order_record: Dict[str, Any],
        items: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        points_to_deduct: int = 0,
        voucher_code: Optional[str] = None
    ) -> str:
        """
        Tạo đơn hàng nguyên tử (ACID Transaction) trong CSDL:
        - Gộp các dòng trùng (cùng product/color/size).
        - Kiểm tra và trừ tồn kho biến thể + cha.
        - Kiểm tra và trừ điểm thưởng người dùng trực tiếp từ DB.
        - Kiểm tra và ghi nhận lượt dùng voucher.
        - Ghi bản ghi orders và order_items.
        Nếu có lỗi, toàn bộ transaction tự động Rollback.
        """
        from app.services.order_service import OrderError
        order_id = order_record["order_id"]
        now_str = datetime.datetime.now().isoformat()

        # 1. Gộp các dòng trùng (product_id, color, size)
        aggregated_items: Dict[Tuple[str, str, str], int] = {}
        for it in items:
            key = (it["product_id"], it["color"], it["size"])
            aggregated_items[key] = aggregated_items.get(key, 0) + it["quantity"]

        with get_db_transaction(self.db_path) as conn:
            # 2. Kiểm tra và trừ tồn kho từng biến thể và sản phẩm cha
            for (pid, col, sz), qty in aggregated_items.items():
                var_row = conn.execute(
                    "SELECT stock FROM product_variants WHERE product_id = ? AND color = ? AND size = ?;",
                    (pid, col, sz)
                ).fetchone()
                if var_row is not None:
                    if var_row["stock"] < qty:
                        raise OrderError(f"Biến thể '{col} - {sz}' của sản phẩm chỉ còn {var_row['stock']} sản phẩm", 409)
                    conn.execute(
                        "UPDATE product_variants SET stock = stock - ? WHERE product_id = ? AND color = ? AND size = ?;",
                        (qty, pid, col, sz)
                    )
                else:
                    p_row = conn.execute("SELECT stock, name FROM products WHERE id = ?;", (pid,)).fetchone()
                    if not p_row or p_row["stock"] < qty:
                        avail = p_row["stock"] if p_row else 0
                        raise OrderError(f"Sản phẩm '{p_row['name'] if p_row else pid}' chỉ còn {avail} sản phẩm", 409)

                p_row = conn.execute("SELECT stock, name FROM products WHERE id = ?;", (pid,)).fetchone()
                if not p_row or p_row["stock"] < qty:
                    avail = p_row["stock"] if p_row else 0
                    raise OrderError(f"Sản phẩm '{p_row['name'] if p_row else pid}' chỉ còn {avail} sản phẩm", 409)
                conn.execute(
                    "UPDATE products SET stock = (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = ?), sold_count = sold_count + ? WHERE id = ?;",
                    (pid, qty, pid)
                )

            # 3. Trừ điểm thưởng trực tiếp trong DB (nếu có dùng điểm)
            if user_id and points_to_deduct > 0:
                u_row = conn.execute("SELECT points_balance FROM users WHERE id = ?;", (user_id,)).fetchone()
                if not u_row or (u_row["points_balance"] or 0) < points_to_deduct:
                    avail_pts = u_row["points_balance"] if u_row else 0
                    raise OrderError(f"Số điểm tích lũy trong tài khoản không đủ ({avail_pts} < {points_to_deduct})", 409)
                conn.execute("UPDATE users SET points_balance = points_balance - ? WHERE id = ?;", (points_to_deduct, user_id))
                bal_after = (u_row["points_balance"] or 0) - points_to_deduct
                now_dt = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                conn.execute(
                    """
                    INSERT INTO loyalty_transactions 
                    (user_id, order_id, points, type, description, balance_after, created_at)
                    VALUES (?, ?, ?, 'redeem', ?, ?, ?);
                    """,
                    (user_id, order_id, -points_to_deduct, f"Sử dụng điểm cho đơn hàng {order_id}", bal_after, now_dt)
                )

            # 4. Kiểm tra và ghi nhận voucher_usages
            customer = order_record.get("customer", {})
            phone_raw = customer.get("phone")
            norm_phone = normalize_phone(phone_raw) if phone_raw else None

            if voucher_code:
                code_upper = voucher_code.strip().upper()
                from app.services.product_service import product_service
                v = product_service.get_voucher(code_upper)
                if not v:
                    raise OrderError(f"Mã giảm giá '{code_upper}' không tồn tại hoặc đã hết hạn", 400)

                # 4.1 Kiểm tra hạn sử dụng (expires_at)
                if v.expires_at and datetime.datetime.now() > v.expires_at:
                    raise OrderError(f"Mã giảm giá '{code_upper}' đã hết hạn sử dụng", 400)

                # 4.2 Kiểm tra đơn hàng tối thiểu (min_order)
                quote = order_record.get("quote", {})
                after_combo = quote.get("subtotal", 0) - quote.get("combo_discount", 0)
                if v.min_order and after_combo < v.min_order:
                    raise OrderError(f"Mã giảm giá '{code_upper}' chỉ áp dụng cho đơn từ {v.min_order:,}đ", 400)

                # 4.3 Kiểm tra giới hạn lượt dùng toàn hệ thống (max_uses)
                if v.max_uses is not None:
                    total_used = conn.execute("SELECT COUNT(*) FROM voucher_usages WHERE voucher_code = ?;", (code_upper,)).fetchone()[0]
                    if total_used >= v.max_uses:
                        raise OrderError(f"Mã giảm giá '{code_upper}' đã hết lượt sử dụng trên hệ thống", 400)

                # 4.4 Kiểm tra lượt dùng theo user_id HOẶC SĐT chuẩn hoá (guest)
                if v.max_uses_per_user is not None:
                    if user_id and norm_phone:
                        user_used = conn.execute(
                            "SELECT COUNT(*) FROM voucher_usages WHERE voucher_code = ? AND (user_id = ? OR phone = ?);",
                            (code_upper, user_id, norm_phone)
                        ).fetchone()[0]
                    elif user_id:
                        user_used = conn.execute(
                            "SELECT COUNT(*) FROM voucher_usages WHERE voucher_code = ? AND user_id = ?;",
                            (code_upper, user_id)
                        ).fetchone()[0]
                    elif norm_phone:
                        user_used = conn.execute(
                            "SELECT COUNT(*) FROM voucher_usages WHERE voucher_code = ? AND phone = ?;",
                            (code_upper, norm_phone)
                        ).fetchone()[0]
                    else:
                        user_used = 0

                    if user_used >= v.max_uses_per_user:
                        raise OrderError(f"Bạn đã sử dụng hết {v.max_uses_per_user} lượt cho mã giảm giá '{code_upper}'", 400)

                conn.execute(
                    """
                    INSERT INTO voucher_usages (voucher_code, user_id, phone, order_id, created_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (code_upper, user_id, norm_phone, order_id, now_str)
                )

            # 5. Lưu đơn hàng vào bảng orders
            quote = order_record.get("quote", {})
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, customer_name, customer_phone, customer_email, customer_address,
                    province, district, ward, specific_address, customer_note,
                    payment_method, payment_status, order_status, carrier, tracking_code,
                    shipping_status, estimated_delivery, total_amount, subtotal, shipping_fee,
                    discount_amount, voucher_code, quote_json, loyalty_awarded, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, 0, ?
                );
                """,
                (
                    order_id,
                    user_id,
                    customer.get("name"),
                    customer.get("phone"),
                    customer.get("email") or order_record.get("customer_email"),
                    customer.get("address"),
                    customer.get("province"),
                    customer.get("district"),
                    customer.get("ward"),
                    customer.get("specific_address"),
                    customer.get("note"),
                    order_record.get("payment_method"),
                    order_record.get("payment_status", "unpaid"),
                    order_record.get("status", "pending_payment"),
                    order_record.get("carrier"),
                    order_record.get("tracking_code"),
                    order_record.get("shipping_status"),
                    order_record.get("estimated_delivery"),
                    quote.get("total", 0),
                    quote.get("subtotal", 0),
                    quote.get("shipping_fee", 0),
                    (quote.get("voucher_discount", 0) or 0) + (quote.get("combo_discount", 0) or 0) + (quote.get("points_discount", 0) or 0),
                    quote.get("voucher_code"),
                    json.dumps(quote, ensure_ascii=False),
                    now_str,
                )
            )

            # 6. Lưu order_items
            item_lines = quote.get("lines") or items
            for line in item_lines:
                conn.execute(
                    """
                    INSERT INTO order_items (
                        order_id, product_id, product_name, color, size, quantity, unit_price, line_total
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        order_id,
                        line.get("product_id"),
                        line.get("name") or line.get("product_name", ""),
                        line.get("color"),
                        line.get("size"),
                        line.get("quantity", 1),
                        line.get("unit_price") or line.get("price", 0),
                        line.get("line_total") or (line.get("price", 0) * line.get("quantity", 1)),
                    )
                )

        log_money_event(
            "order_created",
            order_id=order_id,
            user_id=user_id,
            amount=quote.get("total", 0),
            extra={
                "points_deducted": points_to_deduct,
                "voucher_code": voucher_code,
                "payment_method": order_record.get("payment_method"),
            },
        )
        return order_id

    def cancel_order_atomic(self, order_id: str, forbid_if_shipping: bool = False) -> bool:
        """
        Hủy đơn hàng nguyên tử (ACID Transaction):
        - Cập nhật order_status = 'cancelled' (và payment_status = 'refund_pending' nếu đã thanh toán).
        - Hoàn tồn kho trong CSDL cho product_variants và products.
        - Hoàn điểm đã dùng (loại 'refund').
        - Thu hồi điểm đã cộng cho đơn đó (nếu đã từng cộng).
        - Trừ total_spent đã cộng và tính lại tier.
        - Hoàn lượt sử dụng voucher.
        - forbid_if_shipping=True: từ chối nếu đơn đã chuyển sang shipping/completed (race guard cho khách tự huỷ).
        """
        # Import locally to avoid circular import; OrderError is defined in order_service
        from app.services.order_service import OrderError  # noqa: PLC0415
        with get_db_transaction(self.db_path) as conn:
            order = conn.execute("SELECT * FROM orders WHERE order_id = ?;", (order_id,)).fetchone()
            if not order:
                return False
            if order["order_status"] == "cancelled":
                return False
            if forbid_if_shipping and order["order_status"] in ("shipping", "completed"):
                raise OrderError(
                    "Đơn hàng đã chuyển sang vận chuyển, không thể hủy lúc này",
                    409,
                )

            # 1. Cập nhật trạng thái
            new_payment_status = "refund_pending" if order["payment_status"] == "paid" else order["payment_status"]
            conn.execute(
                """
                UPDATE orders 
                SET order_status = 'cancelled', payment_status = ?
                WHERE order_id = ?;
                """,
                (new_payment_status, order_id)
            )

            # 2. Hoàn tồn kho trong CSDL cho product_variants và products
            items = conn.execute(
                "SELECT product_id, color, size, quantity FROM order_items WHERE order_id = ?;",
                (order_id,)
            ).fetchall()
            for it in items:
                pid = it["product_id"]
                col = it["color"]
                sz = it["size"]
                qty = int(it["quantity"])
                conn.execute(
                    "UPDATE product_variants SET stock = stock + ? WHERE product_id = ? AND color = ? AND size = ?;",
                    (qty, pid, col, sz)
                )
                conn.execute(
                    "UPDATE products SET stock = (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = ?), sold_count = max(0, sold_count - ?) WHERE id = ?;",
                    (pid, qty, pid)
                )

            # 3. Hoàn điểm đã dùng (loại 'refund')
            user_id = order["user_id"]
            quote_json = order["quote_json"]
            quote = json.loads(quote_json) if quote_json else {}
            points_used = quote.get("points_used", 0)

            if user_id and points_used > 0:
                conn.execute("UPDATE users SET points_balance = points_balance + ? WHERE id = ?;", (points_used, user_id))
                bal_row = conn.execute("SELECT points_balance FROM users WHERE id = ?;", (user_id,)).fetchone()
                bal_after = bal_row["points_balance"] if bal_row else 0
                now_dt = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                conn.execute(
                    """
                    INSERT INTO loyalty_transactions 
                    (user_id, order_id, points, type, description, balance_after, created_at)
                    VALUES (?, ?, ?, 'refund', ?, ?, ?);
                    """,
                    (user_id, order_id, points_used, f"Hoàn điểm từ đơn hủy {order_id}", bal_after, now_dt)
                )

            # 4. Thu hồi điểm đã cộng cho đơn đó (nếu đã từng cộng: loyalty_awarded == 1)
            if user_id and order["loyalty_awarded"] == 1:
                points_earned = quote.get("points_earned", 0)
                net_spent = max(0, quote.get("subtotal", 0) - quote.get("combo_discount", 0) - quote.get("voucher_discount", 0) - quote.get("points_discount", 0))
                if points_earned > 0:
                    conn.execute("UPDATE users SET points_balance = max(0, points_balance - ?) WHERE id = ?;", (points_earned, user_id))
                    bal_row = conn.execute("SELECT points_balance FROM users WHERE id = ?;", (user_id,)).fetchone()
                    bal_after = bal_row["points_balance"] if bal_row else 0
                    now_dt = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                    conn.execute(
                        """
                        INSERT INTO loyalty_transactions 
                        (user_id, order_id, points, type, description, balance_after, created_at)
                        VALUES (?, ?, ?, 'revoke', ?, ?, ?);
                        """,
                        (user_id, order_id, -points_earned, f"Thu hồi điểm từ đơn hủy {order_id}", bal_after, now_dt)
                    )

                conn.execute("UPDATE users SET total_spent = max(0, total_spent - ?) WHERE id = ?;", (net_spent, user_id))
                u_row = conn.execute("SELECT total_spent FROM users WHERE id = ?;", (user_id,)).fetchone()
                if u_row:
                    tot = u_row["total_spent"]
                    tier = "Diamond" if tot >= 15000000 else "Gold" if tot >= 5000000 else "Silver"
                    conn.execute("UPDATE users SET tier = ? WHERE id = ?;", (tier, user_id))

            # 5. Hoàn lượt dùng voucher
            conn.execute("DELETE FROM voucher_usages WHERE order_id = ?;", (order_id,))

        log_money_event(
            "order_cancelled",
            order_id=order_id,
            user_id=order["user_id"] if (order and "user_id" in order.keys()) else None,
            amount=order["total_amount"] if (order and "total_amount" in order.keys()) else 0,
            extra={"refunded_points": points_used if "points_used" in locals() else 0},
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
        """Tra cứu đơn hàng bằng mã đơn (AURA-...) hoặc mã vận đơn (GHN-...). Chỉ khớp chính xác."""
        conn = get_db_connection(self.db_path)
        try:
            query = code.strip()
            # 1. Tìm theo order_id
            row = conn.execute("SELECT * FROM orders WHERE order_id = ? COLLATE NOCASE;", (query,)).fetchone()
            if not row:
                # 2. Tìm theo tracking_code
                row = conn.execute("SELECT * FROM orders WHERE tracking_code = ? COLLATE NOCASE;", (query,)).fetchone()
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
            "email": res.get("customer_email"),
            "address": res["customer_address"],
            "province": res.get("province"),
            "district": res.get("district"),
            "ward": res.get("ward"),
            "note": res.get("customer_note"),
        }
        res["customer_email"] = res.get("customer_email")
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

    def get_refund_pending_orders(self) -> List[Dict[str, Any]]:
        """Lấy danh sách các đơn hàng cần hoàn tiền (payment_status = 'refund_pending')."""
        conn = get_db_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT o.*, pt.transaction_code, pt.amount as paid_amount, pt.payment_channel, pt.status as tx_status
                FROM orders o
                LEFT JOIN payment_transactions pt ON o.order_id = pt.order_id
                WHERE o.payment_status = 'refund_pending'
                ORDER BY o.created_at DESC;
                """
            ).fetchall()
            return [self._format_order_row(dict(r)) for r in rows]
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

            avg_rating = round(total_stars / total_reviews, 1) if total_reviews > 0 else 0.0

            # Tính fit_feedback từ dữ liệu thật (chỉ hiển thị nếu >= 5 đánh giá)
            fit_feedback_summary = None
            fit_rows = conn.execute(
                """
                SELECT fit_feedback, COUNT(*) as cnt
                FROM reviews
                WHERE product_id = ? AND fit_feedback IS NOT NULL AND TRIM(fit_feedback) != ''
                GROUP BY fit_feedback;
                """,
                (product_id,)
            ).fetchall()
            total_fit_votes = sum(int(fr["cnt"]) for fr in fit_rows)
            if total_fit_votes >= 5:
                just_right_cnt = sum(
                    int(fr["cnt"]) for fr in fit_rows
                    if "fit" in str(fr["fit_feedback"]).lower() or "vừa" in str(fr["fit_feedback"]).lower()
                )
                pct = int(round((just_right_cnt / total_fit_votes) * 100))
                fit_feedback_summary = f"{pct}% khách hàng đánh giá đúng kích cỡ"

            # 2. Truy vấn danh sách review chi tiết
            query = "SELECT * FROM reviews WHERE product_id = ?"
            params: List[Any] = [product_id]
            if rating_filter and 1 <= rating_filter <= 5:
                query += " AND rating = ?"
                params.append(rating_filter)
            query += " ORDER BY id DESC LIMIT ?;"
            params.append(limit)

            rows = conn.execute(query, tuple(params)).fetchall()
            reviews_list = []
            for r in rows:
                rev = dict(r)
                rev["is_verified_buyer"] = bool(rev.get("is_verified_buyer", 0))
                reviews_list.append(rev)

            return {
                "summary": {
                    "product_id": product_id,
                    "average_rating": avg_rating,
                    "total_reviews": total_reviews,
                    "rating_breakdown": breakdown,
                    "fit_feedback_summary": fit_feedback_summary
                },
                "reviews": reviews_list
            }
        finally:
            conn.close()

    def check_user_purchased_product(self, user_id: str, product_id: str, username: Optional[str] = None) -> bool:
        """
        Kiểm tra xem user_id có ít nhất 1 đơn hàng trạng thái 'paid' hoặc 'completed'
        chứa product_id hay không (query join orders + order_items).
        """
        conn = get_db_connection(self.db_path)
        try:
            user_ids = [user_id]
            if username and username != user_id:
                user_ids.append(username)
            placeholders = ",".join("?" for _ in user_ids)
            query = f"""
                SELECT 1 
                FROM orders o
                JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.user_id IN ({placeholders})
                  AND oi.product_id = ?
                  AND (
                    o.payment_status = 'paid'
                    OR o.order_status IN ('paid', 'completed')
                  )
                LIMIT 1;
            """
            params = tuple(user_ids + [product_id])
            row = conn.execute(query, params).fetchone()
            return row is not None
        finally:
            conn.close()

    def add_product_review(
        self,
        product_id: str,
        review_data: Dict[str, Any],
        user_id: Optional[str] = None,
        is_verified_buyer: bool = True
    ) -> Dict[str, Any]:
        """Thêm đánh giá mới từ khách hàng hoặc cập nhật đánh giá cũ của chính mình; tính rating từ DB."""
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

            existing = None
            if user_id:
                existing = conn.execute(
                    "SELECT id FROM reviews WHERE product_id = ? AND user_id = ?;",
                    (product_id, user_id)
                ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE reviews
                    SET user_name = ?, rating = ?, comment = ?, height_cm = ?, weight_kg = ?,
                        purchased_size = ?, purchased_color = ?, fit_feedback = ?, is_verified_buyer = ?, created_at = ?
                    WHERE id = ?;
                    """,
                    (user_name, rating, comment, height_cm, weight_kg, purchased_size, purchased_color,
                     fit_feedback, 1 if is_verified_buyer else 0, now_str, existing["id"])
                )
                new_id = existing["id"]
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO reviews 
                    (product_id, user_id, user_name, rating, comment, height_cm, weight_kg, purchased_size, purchased_color, fit_feedback, is_verified_buyer, likes_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?);
                    """,
                    (product_id, user_id, user_name, rating, comment, height_cm, weight_kg, purchased_size, purchased_color, fit_feedback, 1 if is_verified_buyer else 0, now_str)
                )
                new_id = cursor.lastrowid

            # Cập nhật lại rating và reviews_count của sản phẩm cha từ DB
            stats = conn.execute(
                "SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM reviews WHERE product_id = ?;",
                (product_id,)
            ).fetchone()
            avg_r = round(float(stats["avg_r"]), 1) if (stats and stats["avg_r"] is not None) else 0.0
            cnt = int(stats["cnt"]) if (stats and stats["cnt"] is not None) else 0

            conn.execute(
                "UPDATE products SET rating = ?, reviews_count = ? WHERE id = ?;",
                (avg_r, cnt, product_id)
            )

            # Đồng bộ in-memory
            try:
                from app.services.product_service import product_service
                p = product_service.get_by_id(product_id)
                if p:
                    p.rating = avg_r
                    p.reviews_count = cnt
            except Exception:
                pass

            return {
                "id": new_id,
                "product_id": product_id,
                "user_id": user_id,
                "user_name": user_name,
                "rating": rating,
                "comment": comment,
                "height_cm": height_cm,
                "weight_kg": weight_kg,
                "purchased_size": purchased_size,
                "purchased_color": purchased_color,
                "fit_feedback": fit_feedback,
                "created_at": now_str,
                "is_verified_buyer": bool(is_verified_buyer)
            }

    # ==================== LOGISTICS TIMELINE ====================

    def get_tracking_timeline(self, order: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Chỉ hiển thị các mốc hành trình có thật theo order_status/shipping_status thực tế.
        Không sinh mốc cộng giờ giả định. Đơn cancelled hiển thị mốc hủy.
        """
        st = order.get("order_status", "pending")
        ship_st = order.get("shipping_status") or "pending"
        created_at_raw = order.get("created_at") or datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

        steps = []
        # 1. Đặt hàng
        steps.append({
            "key": "ordered",
            "title": "Đặt hàng thành công",
            "description": f"Hệ thống đã tiếp nhận đơn hàng {order.get('order_id')}",
            "location": "AURA Studio Online",
            "time": created_at_raw,
            "status": "completed",
        })

        if st == "cancelled":
            steps.append({
                "key": "cancelled",
                "title": "Đơn hàng đã hủy",
                "description": "Đơn hàng đã được hủy trên hệ thống",
                "location": "AURA Studio Online",
                "time": order.get("updated_at") or created_at_raw,
                "status": "completed",
            })
            return steps

        # 2. Xác nhận đơn
        if st in ["confirmed", "shipping", "completed"]:
            steps.append({
                "key": "confirmed",
                "title": "Đã xác nhận đơn hàng",
                "description": "Đơn hàng đã được xác nhận và chuẩn bị đóng gói",
                "location": "Kho AURA Studio",
                "time": order.get("confirmed_at") or order.get("updated_at") or created_at_raw,
                "status": "completed",
            })
        elif st in ["pending", "pending_payment"]:
            steps.append({
                "key": "confirmed",
                "title": "Chờ xác nhận",
                "description": "Đang chờ thanh toán hoặc nhân viên xác nhận đơn",
                "location": "AURA Studio Online",
                "time": "",
                "status": "pending",
            })

        # 3. Đang giao hàng
        if st in ["shipping", "completed"] or ship_st in ["in_transit", "delivered"]:
            steps.append({
                "key": "shipping",
                "title": "Đang giao hàng",
                "description": "Đơn hàng đã xuất kho và đang trong quá trình vận chuyển",
                "location": order.get("carrier") or "Đơn vị vận chuyển",
                "time": order.get("shipped_at") or order.get("updated_at") or "",
                "status": "completed" if st == "completed" else "current",
            })
        elif st == "confirmed":
            steps.append({
                "key": "shipping",
                "title": "Đang chuẩn bị giao hàng",
                "description": "Kiện hàng đang được đóng gói chờ bàn giao",
                "location": "Kho AURA Studio",
                "time": "",
                "status": "pending",
            })

        # 4. Giao hàng hoàn tất
        if st == "completed" or ship_st == "delivered":
            steps.append({
                "key": "completed",
                "title": "Giao hàng thành công",
                "description": "Đơn hàng đã được giao thành công đến khách hàng",
                "location": order.get("customer_address") or "Địa chỉ nhận hàng",
                "time": order.get("completed_at") or order.get("updated_at") or "",
                "status": "completed",
            })
        elif st in ["confirmed", "shipping"]:
            steps.append({
                "key": "completed",
                "title": "Chờ giao hàng",
                "description": "Chờ người nhận kiểm tra và nhận hàng",
                "location": order.get("customer_address") or "Địa chỉ nhận hàng",
                "time": "",
                "status": "pending",
            })

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
            log_money_event(
                "loyalty_points_changed",
                user_id=user_id,
                order_id=order_id,
                extra={"points_delta": points, "balance_after": new_bal, "type": p_type},
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
            log_money_event(
                "loyalty_points_changed",
                user_id=user_id,
                order_id=order_id,
                extra={"points_delta": -points, "balance_after": new_bal, "type": "redeem"},
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

    # ==================== INVENTORY BATCHES & PROFIT/LOSS ====================

    def add_inventory_batch(
        self,
        product_id: str,
        quantity: int,
        cost_price: int,
        color: Optional[str] = None,
        size: Optional[str] = None,
        note: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Nhập thêm hàng cho sản phẩm:
        - Nguồn sự thật: Tăng tồn kho bảng `product_variants`.
        - Cập nhật tồn kho sản phẩm cha: products.stock = SUM(product_variants.stock).
        - Ghi nhật ký vào `inventory_batches` (có color, size).
        - Đồng bộ in-memory cache product_service.
        """
        if quantity <= 0:
            raise ValueError("Số lượng nhập kho phải lớn hơn 0")
        if cost_price < 0:
            raise ValueError("Giá vốn nhập kho không được âm")

        with get_db_transaction(self.db_path) as conn:
            prod = conn.execute("SELECT id, name, stock, stock_total, colors, sizes FROM products WHERE id = ?;", (product_id,)).fetchone()
            if not prod:
                raise ValueError(f"Không tìm thấy sản phẩm với mã '{product_id}'")

            now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            now_iso = datetime.datetime.now().isoformat()

            # 1. Cập nhật tồn kho biến thể
            if color and size:
                v_row = conn.execute(
                    "SELECT id FROM product_variants WHERE product_id = ? AND color = ? AND size = ?;",
                    (product_id, color, size)
                ).fetchone()
                if v_row:
                    conn.execute(
                        "UPDATE product_variants SET stock = stock + ? WHERE product_id = ? AND color = ? AND size = ?;",
                        (quantity, product_id, color, size)
                    )
                else:
                    sku = f"{product_id}-{color[:2].upper()}-{size}".replace(" ", "")
                    conn.execute(
                        """
                        INSERT INTO product_variants (product_id, color, color_hex, size, stock, sku, created_at)
                        VALUES (?, ?, '#000000', ?, ?, ?, ?);
                        """,
                        (product_id, color, size, quantity, sku, now_iso)
                    )
            else:
                # Không chọn phân loại cụ thể -> phân bổ cho các biến thể hiện có
                variants = conn.execute(
                    "SELECT id FROM product_variants WHERE product_id = ? ORDER BY id ASC;",
                    (product_id,)
                ).fetchall()
                if variants:
                    per_v = quantity // len(variants)
                    rem = quantity % len(variants)
                    for idx, v in enumerate(variants):
                        add_q = per_v + (rem if idx == 0 else 0)
                        conn.execute(
                            "UPDATE product_variants SET stock = stock + ? WHERE id = ?;",
                            (add_q, v["id"])
                        )
                else:
                    sku = f"{product_id}-DEF"
                    conn.execute(
                        """
                        INSERT INTO product_variants (product_id, color, color_hex, size, stock, sku, created_at)
                        VALUES (?, 'Mặc định', '#000000', 'Freesize', ?, ?, ?);
                        """,
                        (product_id, quantity, sku, now_iso)
                    )

            # 2. Đồng bộ tồn kho cha từ tổng biến thể (Single Source of Truth)
            conn.execute(
                """
                UPDATE products 
                SET stock = (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = ?),
                    stock_total = stock_total + ?
                WHERE id = ?;
                """,
                (product_id, quantity, product_id)
            )

            updated_p = conn.execute("SELECT stock, stock_total FROM products WHERE id = ?;", (product_id,)).fetchone()
            new_stock = updated_p["stock"] if updated_p else prod["stock"] + quantity
            new_stock_total = updated_p["stock_total"] if updated_p else prod["stock_total"] + quantity

            # 3. Ghi vào inventory_batches (có color, size)
            cur = conn.execute(
                """
                INSERT INTO inventory_batches (product_id, color, size, quantity, cost_price, received_at, note, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (product_id, color or None, size or None, quantity, cost_price, now_str, note or "", created_by or "admin")
            )
            batch_id = cur.lastrowid

            # 4. Đồng bộ in-memory product_service nếu có
            try:
                from app.services.product_service import product_service
                mem_prod = product_service.get_by_id(product_id)
                if mem_prod:
                    mem_prod.stock = new_stock
                    mem_prod.stock_total = new_stock_total
                    v_rows = conn.execute(
                        "SELECT id, product_id, color, color_hex, size, stock, sku FROM product_variants WHERE product_id = ? ORDER BY id ASC;",
                        (product_id,)
                    ).fetchall()
                    if v_rows:
                        from app.models.schemas import ProductVariant
                        mem_prod.variants = [ProductVariant(**dict(vr)) for vr in v_rows]
            except Exception:
                pass

            return {
                "id": batch_id,
                "product_id": product_id,
                "product_name": prod["name"],
                "color": color or None,
                "size": size or None,
                "quantity": quantity,
                "cost_price": cost_price,
                "received_at": now_str,
                "note": note or "",
                "created_by": created_by or "admin",
                "new_stock": new_stock,
                "new_stock_total": new_stock_total,
            }

    def get_inventory_batches(self, product_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Lấy lịch sử các lô hàng nhập kho (theo sản phẩm hoặc toàn bộ kho)."""
        conn = get_db_connection(self.db_path)
        try:
            if product_id:
                rows = conn.execute(
                    """
                    SELECT b.*, p.name as product_name 
                    FROM inventory_batches b
                    LEFT JOIN products p ON b.product_id = p.id
                    WHERE b.product_id = ?
                    ORDER BY b.id DESC LIMIT ?;
                    """,
                    (product_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT b.*, p.name as product_name 
                    FROM inventory_batches b
                    LEFT JOIN products p ON b.product_id = p.id
                    ORDER BY b.id DESC LIMIT ?;
                    """,
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_low_stock_variants(self, threshold: int = 5) -> List[Dict[str, Any]]:
        """Lấy danh sách các biến thể có tồn kho <= threshold để cảnh báo nhập hàng."""
        conn = get_db_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT pv.id, pv.product_id, pv.color, pv.color_hex, pv.size, pv.stock, pv.sku,
                       p.name as product_name, p.price, p.category, p.stock as total_product_stock
                FROM product_variants pv
                JOIN products p ON pv.product_id = p.id
                WHERE pv.stock <= ?
                ORDER BY pv.stock ASC, p.name ASC;
                """,
                (threshold,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_product_weighted_average_cost(self, product_id: str) -> float:
        """Tính giá vốn bình quân gia quyền (Weighted Average Cost) của 1 sản phẩm."""
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT SUM(quantity) as total_qty, SUM(quantity * cost_price) as total_cost
                FROM inventory_batches
                WHERE product_id = ?;
                """,
                (product_id,)
            ).fetchone()
            if row and row["total_qty"] and row["total_qty"] > 0:
                return round(float(row["total_cost"]) / float(row["total_qty"]), 2)
            return 0.0
        finally:
            conn.close()

    def get_profit_loss_report(self) -> Dict[str, Any]:
        """
        Báo cáo Lãi/Lỗ dựa trên doanh thu thực thu (sau voucher, điểm, combo) từ các đơn hàng
        đã thanh toán ('paid' hoặc 'completed' KHÔNG bị hủy) phân bổ theo dòng,
        và giá vốn bình quân gia quyền (Weighted Average Cost - WAC) từ các lô nhập kho.
        Sản phẩm chưa có lô nhập hiển thị 'chưa có giá vốn' và không tính lãi 100%.
        """
        conn = get_db_connection(self.db_path)
        try:
            # 1. Tính WAC cho tất cả sản phẩm đã có lô nhập
            wac_rows = conn.execute(
                """
                SELECT product_id, SUM(quantity) as total_qty, SUM(quantity * cost_price) as total_cost
                FROM inventory_batches
                GROUP BY product_id;
                """
            ).fetchall()
            wac_map: Dict[str, float] = {}
            for r in wac_rows:
                if r["total_qty"] and r["total_qty"] > 0:
                    wac_map[r["product_id"]] = round(float(r["total_cost"]) / float(r["total_qty"]), 2)

            # 2. Lấy tất cả order_items từ các đơn hàng đã thanh toán hợp lệ (không bị hủy)
            sold_items_rows = conn.execute(
                """
                SELECT 
                    o.order_id,
                    o.payment_status,
                    o.order_status,
                    o.created_at,
                    o.total_amount,
                    o.subtotal,
                    oi.product_id,
                    oi.product_name,
                    oi.quantity,
                    oi.unit_price,
                    oi.line_total
                FROM orders o
                JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.order_status != 'cancelled' 
                  AND o.payment_status NOT IN ('refund_pending', 'refunded') 
                  AND (o.payment_status = 'paid' OR o.order_status IN ('paid', 'completed'))
                ORDER BY o.created_at DESC;
                """
            ).fetchall()

            # Thống kê số đơn hàng đã thanh toán
            paid_orders_count = conn.execute(
                """
                SELECT COUNT(DISTINCT order_id) as cnt, COALESCE(SUM(total_amount), 0) as total_rev
                FROM orders
                WHERE order_status != 'cancelled' 
                  AND payment_status NOT IN ('refund_pending', 'refunded') 
                  AND (payment_status = 'paid' OR order_status IN ('paid', 'completed'));
                """
            ).fetchone()
            total_orders = paid_orders_count["cnt"] if paid_orders_count else 0

            # 3. Phân rã theo từng sản phẩm
            product_stats: Dict[str, Dict[str, Any]] = {}
            total_revenue = 0
            total_cogs = 0
            total_items_sold = 0

            for item in sold_items_rows:
                pid = item["product_id"]
                pname = item["product_name"] or pid
                qty = int(item["quantity"])
                line_total = float(item["line_total"])
                order_subtotal = float(item["subtotal"] or line_total)
                order_total = float(item["total_amount"] or line_total)

                # Phân bổ total_amount thực thu theo tỷ lệ line_total / subtotal
                if order_subtotal > 0:
                    rev = int(round((line_total / order_subtotal) * order_total))
                else:
                    rev = int(round(line_total))

                has_wac = pid in wac_map
                wac = wac_map.get(pid)
                item_cogs = int(round(wac * qty)) if has_wac and wac is not None else None

                total_revenue += rev
                if item_cogs is not None:
                    total_cogs += item_cogs
                total_items_sold += qty

                if pid not in product_stats:
                    product_stats[pid] = {
                        "product_id": pid,
                        "product_name": pname,
                        "sold_quantity": 0,
                        "revenue": 0,
                        "cost_price_wac": wac,
                        "cost_status": "has_cost" if has_wac else "no_cost",
                        "cost_note": "" if has_wac else "Chưa có giá vốn",
                        "cogs": 0 if has_wac else None,
                        "profit": 0 if has_wac else None,
                        "margin_percent": 0.0 if has_wac else None,
                    }

                product_stats[pid]["sold_quantity"] += qty
                product_stats[pid]["revenue"] += rev
                if has_wac and item_cogs is not None:
                    product_stats[pid]["cogs"] = (product_stats[pid]["cogs"] or 0) + item_cogs

            # Tính lợi nhuận và tỷ suất cho từng sản phẩm
            breakdown_list = []
            for pdata in product_stats.values():
                if pdata["cogs"] is not None:
                    pdata["profit"] = pdata["revenue"] - pdata["cogs"]
                    if pdata["revenue"] > 0:
                        pdata["margin_percent"] = round((pdata["profit"] / pdata["revenue"]) * 100, 1)
                else:
                    pdata["profit"] = None
                    pdata["margin_percent"] = None
                breakdown_list.append(pdata)

            # Sắp xếp theo doanh thu giảm dần
            breakdown_list.sort(key=lambda x: x["revenue"], reverse=True)

            # Lợi nhuận gộp tính trên các sản phẩm đã có giá vốn
            rev_with_cost = sum(p["revenue"] for p in breakdown_list if p["profit"] is not None)
            gross_profit = sum(p["profit"] for p in breakdown_list if p["profit"] is not None)
            profit_margin = round((gross_profit / rev_with_cost) * 100, 1) if rev_with_cost > 0 else 0.0

            # 4. Lấy 10 lô nhập kho gần nhất
            recent_batches_rows = conn.execute(
                """
                SELECT b.*, p.name as product_name
                FROM inventory_batches b
                LEFT JOIN products p ON b.product_id = p.id
                ORDER BY b.id DESC LIMIT 10;
                """
            ).fetchall()
            recent_batches = [dict(r) for r in recent_batches_rows]

            return {
                "total_revenue": total_revenue,
                "total_cogs": total_cogs,
                "gross_profit": gross_profit,
                "profit_margin_percent": profit_margin,
                "total_paid_orders": total_orders,
                "total_items_sold": total_items_sold,
                "cost_method_note": "Giá vốn tính theo bình quân gia quyền (Weighted Average Cost - WAC) toàn bộ lô nhập kho.",
                "products_breakdown": breakdown_list,
                "recent_batches": recent_batches,
            }
        finally:
            conn.close()

    def _row_to_voucher(self, row: Optional[sqlite3.Row]):
        if not row:
            return None
        from app.models.schemas import Voucher
        expires_at = None
        if row["expires_at"]:
            try:
                expires_at = datetime.datetime.fromisoformat(row["expires_at"])
            except Exception:
                pass
        return Voucher(
            code=row["code"],
            title=row["title"],
            kind=row["kind"],
            value=row["value"],
            max_discount=row["max_discount"],
            min_order=row["min_order"],
            badge=row["badge"],
            expire_in=row["expire_in"],
            expires_at=expires_at,
            max_uses=row["max_uses"],
            max_uses_per_user=row["max_uses_per_user"],
        )

    def get_voucher(self, code: Optional[str]):
        """Lấy thông tin voucher từ CSDL SQLite."""
        if not code:
            return None
        code = code.strip().upper()
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM vouchers WHERE code = ? AND is_active = 1;", (code,)).fetchone()
            return self._row_to_voucher(row)
        finally:
            conn.close()

    def get_all_vouchers(self) -> List[Any]:
        """Lấy danh sách tất cả voucher đang active từ CSDL SQLite."""
        conn = get_db_connection(self.db_path)
        try:
            rows = conn.execute("SELECT * FROM vouchers WHERE is_active = 1 ORDER BY created_at ASC;").fetchall()
            return [self._row_to_voucher(r) for r in rows if r]
        finally:
            conn.close()

    def save_voucher(self, voucher: Any):
        """Thêm hoặc cập nhật voucher trong CSDL."""
        now_str = datetime.datetime.now().isoformat()
        if hasattr(voucher, "model_dump"):
            v_dict = voucher.model_dump()
        elif isinstance(voucher, dict):
            v_dict = voucher
        else:
            v_dict = voucher.__dict__

        expires_at_val = v_dict.get("expires_at")
        if isinstance(expires_at_val, datetime.datetime):
            expires_at_val = expires_at_val.isoformat()

        with get_db_transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO vouchers 
                (code, title, kind, value, max_discount, min_order, badge, expire_in, expires_at, max_uses, max_uses_per_user, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(code) DO UPDATE SET
                    title = excluded.title,
                    kind = excluded.kind,
                    value = excluded.value,
                    max_discount = excluded.max_discount,
                    min_order = excluded.min_order,
                    badge = excluded.badge,
                    expire_in = excluded.expire_in,
                    expires_at = excluded.expires_at,
                    max_uses = excluded.max_uses,
                    max_uses_per_user = excluded.max_uses_per_user,
                    is_active = excluded.is_active;
                """,
                (
                    v_dict.get("code", "").upper().strip(),
                    v_dict.get("title", ""),
                    v_dict.get("kind", "amount"),
                    v_dict.get("value", 0),
                    v_dict.get("max_discount"),
                    v_dict.get("min_order", 0),
                    v_dict.get("badge", "AURA"),
                    v_dict.get("expire_in", "Còn hiệu lực"),
                    expires_at_val,
                    v_dict.get("max_uses"),
                    v_dict.get("max_uses_per_user"),
                    now_str,
                )
            )

    def reset_vouchers(self):
        """Reset bảng vouchers và seed lại dữ liệu mặc định."""
        with get_db_transaction(self.db_path) as conn:
            conn.execute("DELETE FROM vouchers;")
            _migrate_vouchers(conn)


db_service = DatabaseService()

