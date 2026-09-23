import json
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

from app.models.schemas import Category, Product, VideoItem, Voucher
from app.services.text_utils import has_any, has_word, normalize, tokens

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
        for p in self._products:
            parts = [p.name, p.description, p.style, p.material, p.category_name,
                     CATEGORY_META.get(p.category, ("",))[0], " ".join(p.tags),
                     " ".join(p.occasions)]
            self._words[p.id] = sorted(set(normalize(" ".join(parts)).split()))

    # ---------- Truy vấn ----------
    def get_all(self, category: Optional[str] = None, gender: Optional[str] = None,
                min_price: Optional[int] = None, max_price: Optional[int] = None,
                sort: Optional[str] = "popular", search: Optional[str] = None,
                flash_sale_only: bool = False) -> List[Product]:
        results = list(self._products)

        if flash_sale_only or category == "flash_sale":
            results = [p for p in results if p.flash_sale]
        if category and category not in ("all", "flash_sale"):
            results = [p for p in results if p.category == category]
        if gender and gender != "all":
            results = [p for p in results if p.gender in (gender, "unisex")]
        if min_price is not None:
            results = [p for p in results if p.final_price >= min_price]
        if max_price is not None:
            results = [p for p in results if p.final_price <= max_price]

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

    def get_by_id(self, product_id: Optional[str]) -> Optional[Product]:
        return self._by_id.get(product_id) if product_id else None

    def get_by_ids(self, product_ids: List[str]) -> List[Product]:
        """Giữ nguyên thứ tự truyền vào, bỏ trùng và mã không tồn tại."""
        seen, out = set(), []
        for pid in product_ids:
            if pid in self._by_id and pid not in seen:
                seen.add(pid)
                out.append(self._by_id[pid])
        return out

    def get_flash_sale_products(self) -> List[Product]:
        return [p for p in self._products if p.flash_sale]

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

    def semantic_search(self, query: str, limit: int = 8) -> List[Product]:
        """Tìm theo ngữ cảnh tự nhiên, không phân biệt dấu ('do di bien mat me')."""
        q = normalize(query)
        toks = [t for t in tokens(query) if t not in STOPWORDS]
        if not q:
            return self._products[:limit]

        # Ưu tiên các sản phẩm đang bắt trend nếu người dùng tìm "trend", "xu hướng", "đang hot"
        if has_any(q, ["trend", "hot trend", "xu huong", "dang hot", "thinh hanh"]):
            try:
                from app.services.trend_service import trend_service
                trending_prods = [tp.product for tp in trend_service.get_trending_products(limit=limit)]
                if trending_prods:
                    return trending_prods
            except Exception:
                pass

        wanted_occasions = {
            occ for occ, kws in OCCASION_SYNONYMS.items()
            if any(has_word(q, kw) for kw in kws)
        }

        scored: List[Tuple[float, Product]] = []
        for p in self._products:
            name_n = normalize(p.name)
            name_tokens = set(tokens(p.name))
            tag_tokens = set(t for tag in p.tags for t in tokens(tag))
            style_tokens = set(tokens(p.style))
            material_tokens = set(tokens(p.material))
            cat_tokens = set(tokens(p.category_name)) | set(p.category.split("_"))
            score = 0.0
            if q in name_n:
                score += 15
            if q in normalize(p.description):
                score += 8
            for t in toks:
                if t in name_tokens:
                    score += 5
                if t in tag_tokens:
                    score += 6
                if t in style_tokens:
                    score += 4
                if t in material_tokens:
                    score += 3
                if t in cat_tokens:
                    score += 5
            score += 8 * len(wanted_occasions & set(p.occasions))
            if score > 0:
                score += p.rating / 10 + (2 if p.is_hot else 0)  # phá hòa điểm
                scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [p for _, p in scored][:limit]
        return results if results else self.get_all(sort="popular")[:limit]

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


product_service = ProductService()
