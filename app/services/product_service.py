import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.schemas import Category, Product, ProductVariant, VideoItem, Voucher
from app.services.text_utils import has_any, normalize, tokens

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "products.json")

# Tên danh mục chuẩn (dữ liệu gốc đặt category_name không nhất quán giữa các sản phẩm)
CATEGORY_META: Dict[str, Tuple[str, str]] = {
    "ao_thun": ("Áo Thun & Polo", "👕"),
    "ao_so_mi": ("Áo Sơ Mi", "👔"),
    "ao_khoac": ("Áo Khoác & Len", "🧥"),
    "quan_tay": ("Quần Tây & Short", "👖"),
    "quan_jeans": ("Quần Jeans", "👖"),
    "chan_vay": ("Chân Váy", "🎀"),
    "vay_dam": ("Váy & Đầm", "👗"),
    "set_do": ("Set Đồ", "🏖️"),
    "phu_kien": ("Phụ Kiện", "👜"),
}

# Từ khóa (đã bỏ dấu) -> mã dịp trong dữ liệu sản phẩm
OCCASION_SYNONYMS: Dict[str, List[str]] = {
    "du_tiec": ["tiec", "da hoi", "sang trong", "quy phai", "party", "su kien"],
    "dam_cuoi": ["dam cuoi", "cuoi", "tiec cuoi"],
    "di_bien": ["bien", "di bien", "resort", "nghi duong"],
    "mua_he": ["mua he", "mat", "nong", "nang"],
    "nghi_duong": ["nghi duong", "resort"],
    "cong_so": ["cong so", "di lam", "van phong", "lich su"],
    "phong_van": ["phong van", "gap doi tac", "hop"],
    "di_lam": ["di lam", "van phong"],
    "thu_dong": ["dong", "thu dong", "lanh", "am", "ret"],
    "mua_dong": ["dong", "mua dong", "lanh", "am", "ret"],
    "hen_ho": ["hen ho", "date", "nguoi yeu"],
    "hen_ho_cao_cap": ["hen ho", "date", "sang trong"],
    "du_lich": ["du lich", "di choi xa"],
    "du_lich_da_lat": ["da lat", "du lich"],
    "dao_pho": ["dao pho", "di choi", "cuoi tuan"],
    "di_choi": ["di choi", "dao pho"],
    "cafe": ["cafe", "ca phe"],
    "hang_ngay": ["hang ngay", "thuong ngay"],
    "di_hoc": ["di hoc", "sinh vien"],
    "the_thao": ["the thao", "gym"],
}

STOPWORDS = {
    "cho", "toi", "minh", "ban", "can", "muon", "tim", "mot", "va", "la", "co",
    "de", "di", "do", "nhung", "cac", "shop", "oi", "nhe", "voi", "cua", "mac",
}

AVAILABLE_VOUCHERS = [
    Voucher(code="FREESHIP", title="Miễn phí vận chuyển toàn quốc", kind="shipping",
            min_order=0, badge="Freeship", expire_in="Còn hiệu lực"),
    Voucher(code="AURA50K", title="Ưu đãi đơn từ 299K", kind="amount", value=50000,
            min_order=299000, badge="AURA STUDIO", expire_in="Trong tháng này"),
    Voucher(code="LIVE20", title="Độc quyền từ phòng Live AI", kind="percent", value=20,
            max_discount=100000, min_order=199000, badge="Live AI", expire_in="Trong tháng này"),
    Voucher(code="AURA10", title="Khách mới trải nghiệm AI Stylist", kind="percent", value=10,
            max_discount=50000, min_order=150000, badge="Khách mới", expire_in="30 ngày"),
    Voucher(code="STAYWITHUS", title="Quà tặng giữ chân khách hàng - Giảm 5%", kind="percent", value=5,
            max_discount=100000, min_order=100000, badge="Tri ân 5%", expire_in="Hôm nay"),
]

VN_UTC_OFFSET_MS = 7 * 3600 * 1000
FLASH_SLOT_MS = 3 * 3600 * 1000  # mỗi khung flash sale kéo dài 3 giờ (theo giờ Việt Nam)


def flash_sale_window(now_ms: Optional[int] = None) -> Tuple[int, int]:
    """Trả (now_ms, ends_at_ms). Khung Flash Sale đổi lúc 0h, 3h, 6h... giờ VN."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    local = now_ms + VN_UTC_OFFSET_MS
    slot_end_local = (local // FLASH_SLOT_MS + 1) * FLASH_SLOT_MS
    return now_ms, slot_end_local - VN_UTC_OFFSET_MS


class ProductService:
    def __init__(self):
        self._lock = threading.RLock()
        self._products: List[Product] = []
        self._by_id: Dict[str, Product] = {}
        self._words: Dict[str, List[str]] = {}
        self._load_products()

    def _load_products(self):
        # Cố ý KHÔNG nuốt lỗi: catalog hỏng thì phải báo rõ thay vì chạy với cửa hàng trống.
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._products = [Product(**item) for item in data]
        self._by_id = {p.id: p for p in self._products}
        
        # Đồng bộ ma trận biến thể tồn kho (Màu x Size) và số lượng tồn từ CSDL SQLite
        try:
            from app.db.database import db_service
            raw_db_prods = db_service.get_all_products_db()
            db_prods = raw_db_prods if isinstance(raw_db_prods, dict) else {row["id"]: row for row in raw_db_prods}
            for p in self._products:
                if p.id in db_prods:
                    db_p = db_prods[p.id]
                    if db_p.get("stock") is not None:
                        p.stock = db_p["stock"]
                    if db_p.get("stock_total") is not None:
                        p.stock_total = db_p["stock_total"]
                    if db_p.get("sold_count") is not None:
                        p.sold_count = db_p["sold_count"]
                    if db_p.get("rating") is not None:
                        p.rating = db_p["rating"]
                    if db_p.get("reviews_count") is not None:
                        p.reviews_count = db_p["reviews_count"]

                v_rows = db_service.get_product_variants(p.id)
                if v_rows:
                    p.variants = [ProductVariant(**v) for v in v_rows]
                else:
                    p.variants = [
                        ProductVariant(color=c.name, color_hex=c.hex, size=s, stock=p.stock)
                        for c in p.colors for s in p.sizes
                    ]
        except Exception as e:
            logger.warning("[ProductService] Fallback variants nếu CSDL chưa nạp: %s", e)
            for p in self._products:
                p.variants = [
                    ProductVariant(color=c.name, color_hex=c.hex, size=s, stock=p.stock)
                    for c in p.colors for s in p.sizes
                ]

        for p in self._products:
            parts = [p.name, p.description, p.style, p.material, p.category_name,
                     CATEGORY_META.get(p.category, ("",))[0], " ".join(p.tags),
                     " ".join(p.occasions)]
            self._words[p.id] = sorted(set(normalize(" ".join(parts)).split()))

    # ---------- Truy vấn ----------
    def get_all(self, category: Optional[str] = None, gender: Optional[str] = None,
                min_price: Optional[int] = None, max_price: Optional[int] = None,
                sort: Optional[str] = "popular", search: Optional[str] = None,
                flash_sale_only: bool = False, size: Optional[str] = None,
                color: Optional[str] = None, occasion: Optional[str] = None,
                material: Optional[str] = None, include_inactive: bool = False) -> List[Product]:
        results = [p for p in self._products if (include_inactive or (getattr(p, "is_active", True) and not getattr(p, "deleted_at", None)))]

        if flash_sale_only or category == "flash_sale":
            results = [p for p in results if p.is_in_flash_sale]
        if category and category not in ("all", "flash_sale"):
            results = [p for p in results if p.category == category]
        if gender and gender != "all":
            results = [p for p in results if p.gender in (gender, "unisex")]
        if min_price is not None:
            results = [p for p in results if p.final_price >= min_price]
        if max_price is not None:
            results = [p for p in results if p.final_price <= max_price]
        if size and size.strip():
            s_upper = size.strip().upper()
            results = [p for p in results if p.sizes and any(s.upper() == s_upper for s in p.sizes)]
        if color and color.strip():
            c_norm = normalize(color.strip())
            results = [p for p in results if p.colors and any(c_norm in normalize(c.name) for c in p.colors)]
        if occasion and occasion.strip():
            occ_norm = normalize(occasion.strip())
            results = [p for p in results if p.occasions and any(occ_norm in normalize(o) for o in p.occasions)]
        if material and material.strip():
            mat_norm = normalize(material.strip())
            results = [p for p in results if p.material and mat_norm in normalize(p.material)]

        if search and search.strip():
            toks = [t for t in tokens(search) if t not in STOPWORDS]
            if toks:
                # khớp theo tiền tố của TỪ ('ao' khớp 'ao thun' nhưng không khớp 'cao cap';
                # 'bla' khớp 'blazer' để gõ tới đâu tìm tới đó)
                results = [p for p in results
                           if all(any(w.startswith(t) for w in self._words[p.id]) for t in toks)]

        if sort == "price_asc":
            results.sort(key=lambda x: x.final_price)
        elif sort == "price_desc":
            results.sort(key=lambda x: x.final_price, reverse=True)
        elif sort == "rating":
            results.sort(key=lambda x: (x.rating, x.reviews_count), reverse=True)
        elif sort == "newest":
            results.sort(key=lambda x: (x.is_new, x.rating), reverse=True)
        elif sort == "top_sales":
            results.sort(key=lambda x: x.sold_count, reverse=True)
        else:
            results.sort(key=lambda x: (x.is_hot, x.sold_count), reverse=True)
        return results

    def get_by_id(self, product_id: Optional[str], include_inactive: bool = False) -> Optional[Product]:
        if not product_id:
            return None
        p = self._by_id.get(product_id)
        if not p:
            return None
        if not include_inactive and (not getattr(p, "is_active", True) or getattr(p, "deleted_at", None)):
            return None
        return p

    def get_by_ids(self, product_ids: List[str], include_inactive: bool = False) -> List[Product]:
        """Giữ nguyên thứ tự truyền vào, bỏ trùng và mã không tồn tại."""
        seen, out = set(), []
        for pid in product_ids:
            p = self.get_by_id(pid, include_inactive=include_inactive)
            if p and pid not in seen:
                seen.add(pid)
                out.append(p)
        return out

    def semantic_search(self, query: str, limit: int = 30) -> List[Product]:
        """
        Tìm kiếm ngữ nghĩa thông minh phục vụ AI Stylist và gợi ý sản phẩm:
        - Chuẩn hóa văn bản, trích xuất từ khóa, dịp sử dụng (occasions), chất liệu, danh mục.
        - Chấm điểm độ tương đồng ngữ nghĩa kết hợp với độ phổ biến (is_hot, sold_count).
        - Chỉ trả về các sản phẩm còn hàng (in_stock=True), tối đa limit phần tử.
        """
        q_norm = normalize(query.strip()) if query else ""
        if not q_norm:
            return [p for p in self._products if p.in_stock][:limit]

        # Ưu tiên các sản phẩm đang bắt trend nếu người dùng tìm "trend", "hot trend", "xu huong", "dang hot", "thinh hanh"
        if has_any(q_norm, ["trend", "hot trend", "xu huong", "dang hot", "thinh hanh"]):
            try:
                from app.services.trend_service import trend_service
                trending_prods = [tp.product for tp in trend_service.get_trending_products(limit=limit)]
                if trending_prods:
                    return trending_prods
            except Exception:
                pass

        q_tokens = [t for t in tokens(q_norm) if t not in STOPWORDS]

        matched_occasions: Set[str] = set()
        for occ, syns in OCCASION_SYNONYMS.items():
            if any(syn in q_norm for syn in syns):
                matched_occasions.add(occ)

        target_gender = None
        if any(w in q_norm for w in ["nu", "con gai", "phu nu", "vay", "dam", "croptop"]):
            target_gender = "nu"
        elif any(w in q_norm for w in ["nam", "con trai", "dan ong"]):
            target_gender = "nam"

        candidates = [p for p in self._products if p.in_stock]

        def compute_score(p: Product) -> float:
            score = 0.0
            p_words = set(self._words.get(p.id, []))
            
            for qt in q_tokens:
                if qt in p_words:
                    score += 10.0
                elif any(w.startswith(qt) for w in p_words):
                    score += 5.0
                if qt in normalize(p.name):
                    score += 8.0

            for occ in matched_occasions:
                if occ in p.occasions:
                    score += 15.0

            if target_gender:
                if p.gender == target_gender:
                    score += 5.0
                elif p.gender == "unisex":
                    score += 2.0
                else:
                    score -= 10.0

            if p.is_hot:
                score += 3.0
            score += min(5.0, (p.sold_count or 0) / 200.0)
            return score

        if q_tokens or matched_occasions or target_gender:
            scored = [(p, compute_score(p)) for p in candidates]
            scored.sort(key=lambda x: (x[1], x[0].is_hot, x[0].sold_count), reverse=True)
            results = [p for p, sc in scored if sc > 0]
            if len(results) < limit:
                seen = {p.id for p in results}
                for p in candidates:
                    if p.id not in seen:
                        results.append(p)
                        if len(results) >= limit:
                            break
            return results[:limit]
        else:
            candidates.sort(key=lambda p: (p.is_hot, p.sold_count), reverse=True)
            return candidates[:limit]

    def get_flash_sale_products(self) -> List[Product]:
        return [p for p in self._products if (getattr(p, "is_active", True) and not getattr(p, "deleted_at", None)) and p.is_in_flash_sale]

    def get_categories(self) -> List[Category]:
        counts: Dict[str, int] = {}
        for p in self._products:
            counts[p.category] = counts.get(p.category, 0) + 1
        return [
            Category(id=cid, name=CATEGORY_META.get(cid, (cid, "🛍️"))[0],
                     icon=CATEGORY_META.get(cid, (cid, "🛍️"))[1], count=n)
            for cid, n in counts.items()
        ]

    def get_related(self, product_id: str, limit: int = 4) -> List[Product]:
        base = self.get_by_id(product_id)
        if not base:
            return self._products[:limit]

        def score(p: Product) -> float:
            s = 0.0
            if p.category == base.category:
                s += 2
            if p.style == base.style:
                s += 3
            s += len(set(p.tags) & set(base.tags)) * 1.5
            s += len(set(p.occasions) & set(base.occasions)) * 1.0
            if p.gender in (base.gender, "unisex") or base.gender == "unisex":
                s += 1
            return s

        candidates = [p for p in self._products if p.id != product_id]
        candidates.sort(key=score, reverse=True)
        return candidates[:limit]

    # ---------- Voucher ----------
    def get_vouchers(self) -> List[Voucher]:
        return AVAILABLE_VOUCHERS

    def get_voucher(self, code: Optional[str]) -> Optional[Voucher]:
        if not code:
            return None
        code = code.strip().upper()
        return next((v for v in AVAILABLE_VOUCHERS if v.code == code), None)

    # ---------- Kho hàng ----------
    def reserve_stock(self, quantities: Dict[str, int]) -> Optional[str]:
        """Trừ kho nguyên tử. Trả None nếu OK, hoặc thông báo lỗi nếu không đủ hàng."""
        with self._lock:
            for pid, qty in quantities.items():
                p = self._by_id[pid]
                if qty > p.stock:
                    return (f"'{p.name}' chỉ còn {p.stock} sản phẩm" if p.stock > 0
                            else f"'{p.name}' đã hết hàng")
            for pid, qty in quantities.items():
                p = self._by_id[pid]
                p.stock -= qty
                p.sold_count += qty
        return None

    def release_stock(self, quantities: Dict[str, int]):
        with self._lock:
            for pid, qty in quantities.items():
                p = self._by_id.get(pid)
                if p:
                    p.stock += qty
                    p.sold_count = max(0, p.sold_count - qty)

    def apply_historical_sale(self, pid: str, qty: int):
        """Dùng khi khởi động: trừ lại kho theo các đơn đã lưu."""
        with self._lock:
            p = self._by_id.get(pid)
            if p:
                p.stock = max(0, p.stock - qty)
                p.sold_count += qty

    # ---------- Video lookbook ----------
    def get_videos(self) -> List[VideoItem]:
        # Ảnh dưới đây là dữ liệu mẫu: thay bằng ảnh/video của bạn khi triển khai thật.
        specs = [
            ("vid_001", "Blazer Linen đi làm & dạo phố cực sang ✨ #outfit #blazer", "AURA Official",
             "photo-1534528741775-53994a69daeb", "24.8K", "1.2K", "3.5K",
             "photo-1591047139829-d91aecb6caea", "prod_001"),
            ("vid_002", "Quần tây ống suông hack chân cho hội nấm lùn 👖", "Stylist Linh Đan",
             "photo-1517841905240-472988babdf9", "48.2K", "3.1K", "8.9K",
             "photo-1594633312681-425c7b97ccd1", "prod_003"),
            ("vid_003", "Đầm lụa maxi hở lưng đi tiệc cưới, ai cũng ngoái nhìn 💃", "AURA Runway",
             "photo-1544005313-94ddf0286df2", "62.5K", "4.6K", "12.1K",
             "photo-1572804013309-59a88b7e92f1", "prod_004"),
            ("vid_004", "Streetwear: áo thun 260GSM boxy + jeans baggy retro 🔥", "Minh Stylist",
             "photo-1507003211169-0a1dd7228f2d", "31.4K", "1.8K", "5.2K",
             "photo-1521572267360-ee0c2909d518", "prod_005"),
            ("vid_005", "Áo khoác măng tô dáng dài thu đông phong cách Hàn Quốc ❄️", "Hà My Lookbook",
             "photo-1534528741775-53994a69daeb", "54.1K", "2.9K", "7.8K",
             "photo-1539533018447-63fcce667823", "prod_064"),
            ("vid_006", "Set dạ Tweed tiểu thư sang chảnh đi tiệc hay gặp đối tác ✨", "AURA Runway",
             "photo-1544005313-94ddf0286df2", "38.7K", "2.2K", "6.4K",
             "photo-1529139574466-a303027c1d8b", "prod_083"),
        ]
        out = []
        for vid, title, author, av, likes, cm, sh, poster, pid in specs:
            prod = self.get_by_id(pid)
            if not prod:
                continue
            out.append(VideoItem(
                id=vid, title=title, author=author,
                author_avatar=f"https://images.unsplash.com/{av}?q=80&w=200&auto=format&fit=crop",
                likes=likes, comments=cm, shares=sh,
                video_url="",  # để trống = chỉ hiển thị ảnh bìa; điền URL .mp4 của bạn để phát video
                poster_image=f"https://images.unsplash.com/{poster}?q=80&w=600&auto=format&fit=crop",
                tagged_product=prod,
            ))
        return out

    # ---------- Quản trị Admin (CRUD) ----------
    def _save_products_to_disk(self):
        raw_keys = [
            "id", "name", "category", "category_name", "gender", "price", "original_price",
            "flash_sale", "flash_sale_price", "sold_count", "stock_total", "stock", "rating",
            "reviews_count", "location", "images", "sizes", "colors", "description", "material",
            "style", "occasions", "tags", "is_hot", "is_new"
        ]
        disk_products = {}
        if os.path.exists(DATA_PATH):
            try:
                with open(DATA_PATH, "r", encoding="utf-8") as f:
                    disk_products = {item["id"]: item for item in json.load(f)}
            except Exception as e:
                logger.warning("[ProductService] Không thể đọc %s để đồng bộ: %s", DATA_PATH, e)

        out = []
        for p in self._products:
            d = p.model_dump()
            rec = {k: d[k] for k in raw_keys if k in d}
            if not rec.get("flash_sale"):
                rec.pop("flash_sale_price", None)
            # Không ghi lại runtime stock, sold_count, rating, reviews_count vào products.json
            if p.id in disk_products:
                orig = disk_products[p.id]
                for field in ("stock", "sold_count", "rating", "reviews_count"):
                    if field in orig:
                        rec[field] = orig[field]
            out.append(rec)
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        try:
            import datetime

            from app.db.database import get_db_transaction
            now_str = datetime.datetime.now().isoformat()
            with get_db_transaction() as conn:
                for p in self._products:
                    colors = [c.model_dump() for c in p.colors] if p.colors else []
                    conn.execute(
                        """
                        INSERT INTO products
                        (id, name, category, category_name, gender, price, original_price, flash_sale, flash_sale_price,
                         sold_count, stock, stock_total, rating, reviews_count, location, images, sizes, colors,
                         description, material, style, occasions, tags, is_hot, is_new, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name=excluded.name,
                            category=excluded.category,
                            category_name=excluded.category_name,
                            gender=excluded.gender,
                            price=excluded.price,
                            original_price=excluded.original_price,
                            flash_sale=excluded.flash_sale,
                            flash_sale_price=excluded.flash_sale_price,
                            sold_count=excluded.sold_count,
                            stock=excluded.stock,
                            stock_total=excluded.stock_total,
                            rating=excluded.rating,
                            reviews_count=excluded.reviews_count,
                            location=excluded.location,
                            images=excluded.images,
                            sizes=excluded.sizes,
                            colors=excluded.colors,
                            description=excluded.description,
                            material=excluded.material,
                            style=excluded.style,
                            occasions=excluded.occasions,
                            tags=excluded.tags,
                            is_hot=excluded.is_hot,
                            is_new=excluded.is_new;
                        """,
                        (
                            p.id, p.name, p.category, p.category_name, p.gender,
                            p.price, p.original_price, 1 if p.flash_sale else 0, p.flash_sale_price,
                            p.sold_count, p.stock, p.stock_total, p.rating, p.reviews_count,
                            p.location, json.dumps(p.images, ensure_ascii=False),
                            json.dumps(p.sizes, ensure_ascii=False),
                            json.dumps(colors, ensure_ascii=False),
                            p.description, p.material, p.style,
                            json.dumps(p.occasions, ensure_ascii=False),
                            json.dumps(p.tags, ensure_ascii=False),
                            1 if p.is_hot else 0, 1 if p.is_new else 0, now_str
                        )
                    )
        except Exception as e:
            logger.warning("[ProductService] Lỗi upsert SQLite khi lưu sản phẩm: %s", e)

    def create_product(self, data: dict) -> Product:
        with self._lock:
            if not data.get("id") or data["id"] in self._by_id:
                existing_nums = []
                for p in self._products:
                    if p.id.startswith("prod_"):
                        try:
                            existing_nums.append(int(p.id.split("_")[1]))
                        except ValueError:
                            pass
                next_num = (max(existing_nums) + 1) if existing_nums else 1
                data["id"] = f"prod_{next_num:03d}"

            if not data.get("category_name"):
                data["category_name"] = CATEGORY_META.get(data.get("category"), ("Sản phẩm", ""))[0]

            if not data.get("gender"):
                data["gender"] = "unisex"

            if not data.get("images") and data.get("image"):
                data["images"] = [data["image"]]
            elif not data.get("images"):
                data["images"] = ["https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?w=600&auto=format&fit=crop&q=80"]

            if not data.get("sizes"):
                data["sizes"] = ["S", "M", "L", "XL"]

            if not data.get("colors"):
                data["colors"] = [{"name": "Tiêu chuẩn", "hex": "#27272a"}]

            if not data.get("material"):
                data["material"] = "Cotton & Linen thoáng khí cao cấp"

            if not data.get("style"):
                data["style"] = "Hiện đại"

            if not data.get("description"):
                data["description"] = data.get("name", "Sản phẩm thời trang cao cấp AURA Studio")

            if not data.get("original_price"):
                data["original_price"] = data.get("price", 0)

            if not data.get("stock_total"):
                data["stock_total"] = max(100, data.get("stock", 50))

            if "is_flash_sale" in data and "flash_sale" not in data:
                data["flash_sale"] = bool(data["is_flash_sale"])

            if "rating" not in data or data["rating"] is None:
                data["rating"] = 0.0

            if "reviews_count" not in data or data["reviews_count"] is None:
                data["reviews_count"] = 0

            if "location" not in data or data["location"] is None:
                data["location"] = "TP. Hồ Chí Minh"

            if "sold_count" not in data or data["sold_count"] is None:
                data["sold_count"] = 0

            if "occasions" not in data or not data["occasions"]:
                data["occasions"] = [data["occasion"]] if data.get("occasion") else []

            if "tags" not in data or not data["tags"]:
                data["tags"] = []

            new_prod = Product(**data)
            self._products.append(new_prod)
            self._by_id[new_prod.id] = new_prod

            parts = [new_prod.name, new_prod.description, new_prod.style, new_prod.material, new_prod.category_name,
                     CATEGORY_META.get(new_prod.category, ("",))[0], " ".join(new_prod.tags),
                     " ".join(new_prod.occasions)]
            self._words[new_prod.id] = sorted(set(normalize(" ".join(parts)).split()))

            self._save_products_to_disk()
            return new_prod

    def update_product(self, product_id: str, data: dict) -> Product:
        with self._lock:
            p = self._by_id.get(product_id)
            if not p:
                raise ValueError(f"Không tìm thấy sản phẩm với mã '{product_id}'")

            merged = p.model_dump()
            for k, v in data.items():
                if v is not None:
                    merged[k] = v

            merged["id"] = product_id
            if "is_flash_sale" in data:
                merged["flash_sale"] = bool(data["is_flash_sale"])
            if "image" in data and data["image"]:
                merged["images"] = [data["image"]]
            if "occasion" in data and data["occasion"]:
                merged["occasions"] = [data["occasion"]]

            if not merged.get("category_name"):
                merged["category_name"] = CATEGORY_META.get(merged.get("category", p.category), (p.category_name, ""))[0]

            updated_prod = Product(**merged)

            for i, item in enumerate(self._products):
                if item.id == product_id:
                    self._products[i] = updated_prod
                    break
            self._by_id[product_id] = updated_prod

            parts = [updated_prod.name, updated_prod.description, updated_prod.style, updated_prod.material,
                     updated_prod.category_name, CATEGORY_META.get(updated_prod.category, ("",))[0],
                     " ".join(updated_prod.tags), " ".join(updated_prod.occasions)]
            self._words[product_id] = sorted(set(normalize(" ".join(parts)).split()))

            self._save_products_to_disk()
            return updated_prod

    def delete_product(self, product_id: str) -> bool:
        with self._lock:
            if product_id not in self._by_id:
                return False
            prod = self._by_id[product_id]
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            prod.is_active = False
            prod.deleted_at = now_iso
            self._save_products_to_disk()
            try:
                from app.db.database import get_db_transaction
                with get_db_transaction() as conn:
                    conn.execute("UPDATE products SET is_active = 0, deleted_at = ? WHERE id = ?;", (now_iso, product_id))
            except Exception as e:
                logger.warning("Lỗi cập nhật xóa mềm sản phẩm %s trong CSDL: %s", product_id, e)
            return True

    def batch_import_products(self, items: List[Dict[str, Any]]) -> List[Product]:
        """
        Nhập hoặc cập nhật hàng loạt sản phẩm:
        - Cập nhật catalog trong bộ nhớ.
        - Ghi lại products.json MỘT LẦN duy nhất ở cuối.
        - Thực thi một transaction SQLite duy nhất cho toàn bộ sản phẩm.
        """
        with self._lock:
            results: List[Product] = []
            db_updates: List[Product] = []
            for norm_data in items:
                target_id = norm_data.get("id")
                existing = self._by_id.get(target_id) if target_id else None
                if existing:
                    merged = existing.model_dump()
                    for k, v in norm_data.items():
                        if v is not None:
                            merged[k] = v
                    prod = Product(**merged)
                    for i, it in enumerate(self._products):
                        if it.id == target_id:
                            self._products[i] = prod
                            break
                    self._by_id[target_id] = prod
                else:
                    if not norm_data.get("id"):
                        prefix = "prod_"
                        max_num = 0
                        for p in self._products:
                            if p.id.startswith(prefix):
                                try:
                                    num = int(p.id[len(prefix):])
                                    if num > max_num:
                                        max_num = num
                                except ValueError:
                                    pass
                        norm_data["id"] = f"{prefix}{max_num + 1:03d}"
                    prod = Product(**norm_data)
                    self._products.append(prod)
                    self._by_id[prod.id] = prod

                parts = [prod.name, prod.description, prod.style, prod.material,
                         prod.category_name, CATEGORY_META.get(prod.category, ("",))[0],
                         " ".join(prod.tags), " ".join(prod.occasions)]
                self._words[prod.id] = sorted(set(normalize(" ".join(parts)).split()))
                results.append(prod)
                db_updates.append(prod)

            self._save_products_to_disk()

            # Lưu vào SQLite trong 1 transaction duy nhất
            try:
                from app.db.database import get_db_transaction
                with get_db_transaction() as conn:
                    for p in db_updates:
                        conn.execute(
                            """
                            INSERT INTO products 
                            (id, name, category, category_name, gender, price, original_price,
                             flash_sale, flash_sale_price, sold_count, stock, stock_total, rating, reviews_count,
                             location, images, sizes, colors, description, material, style, occasions, tags,
                             is_hot, is_new, is_active, deleted_at, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                name=excluded.name, category=excluded.category, category_name=excluded.category_name,
                                gender=excluded.gender, price=excluded.price, original_price=excluded.original_price,
                                stock=excluded.stock, stock_total=excluded.stock_total,
                                description=excluded.description, material=excluded.material,
                                style=excluded.style, is_active=1, deleted_at=NULL;
                            """,
                            (
                                p.id, p.name, p.category, p.category_name, p.gender, p.price, p.original_price,
                                1 if p.flash_sale else 0, p.flash_sale_price, p.sold_count, p.stock, p.stock_total,
                                p.rating, p.reviews_count, p.location, json.dumps(p.images, ensure_ascii=False),
                                json.dumps(p.sizes, ensure_ascii=False),
                                json.dumps([c.model_dump() if hasattr(c, "model_dump") else c for c in p.colors], ensure_ascii=False),
                                p.description, p.material, p.style, json.dumps(p.occasions, ensure_ascii=False),
                                json.dumps(p.tags, ensure_ascii=False), 1 if p.is_hot else 0, 1 if p.is_new else 0,
                                datetime.datetime.now().isoformat(),
                            )
                        )
            except Exception as e:
                logger.warning("Lỗi cập nhật lô sản phẩm vào CSDL: %s", e)

            return results


product_service = ProductService()
