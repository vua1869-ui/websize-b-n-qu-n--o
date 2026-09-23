import datetime as dt
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import settings
from app.models.schemas import Product, TrendDebugResponse, TrendItem, TrendingProduct
from app.services.product_service import product_service
from app.services.text_utils import has_any, has_word, normalize, tokens
from app.services.trend_sources.base import BaseTrendSource
from app.services.trend_sources.demo_source import DemoTrendSource
from app.services.trend_sources.google_trends import GoogleTrendsSource

TRENDS_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trends.json"
)

# Danh sách từ khóa hạt giống ban đầu (có thể bổ sung hoặc ghi đè qua cấu hình)
SEED_KEYWORDS: List[str] = [
    "quần ống rộng",
    "áo oversize",
    "áo baby tee",
    "áo polo",
    "áo blazer",
    "quần jeans ống rộng",
    "quần baggy",
    "chân váy",
    "váy maxi",
    "Y2K",
    "streetwear",
    "old money",
    "minimalism",
    "Korean fashion",
]

# Ánh xạ từ khóa xu hướng sang ngữ nghĩa thời trang: từ đồng nghĩa, nhóm danh mục, phong cách, thẻ
TREND_TAXONOMY: Dict[str, Dict[str, Any]] = {
    "quần ống rộng": {
        "category_group": "bottom",
        "categories": {"quan_tay", "quan_jeans"},
        "synonyms": ["ong rong", "ong suong", "wide leg", "suong", "baggy", "quan suong"],
        "styles": ["Old Money", "Minimalism", "Streetwear", "Smart Casual"],
        "tags": ["ong suong", "wide leg", "baggy", "ong rong", "quan tay"],
        "display_name": "Quần ống rộng / Wide-Leg",
    },
    "quần jeans ống rộng": {
        "category_group": "bottom",
        "categories": {"quan_jeans"},
        "synonyms": ["jeans ong rong", "bo ong suong", "wide leg jeans", "jeans baggy", "baggy jeans"],
        "styles": ["Streetwear", "Y2K", "Retro", "Năng động"],
        "tags": ["baggy", "retro", "ong suong", "wide leg", "denim"],
        "display_name": "Jeans ống rộng Retro",
    },
    "quần baggy": {
        "category_group": "bottom",
        "categories": {"quan_jeans", "quan_tay"},
        "synonyms": ["baggy", "quan baggy", "form rong", "ong suong"],
        "styles": ["Streetwear", "Minimalism", "Casual"],
        "tags": ["baggy", "retro", "ong suong"],
        "display_name": "Quần Baggy thoải mái",
    },
    "áo oversize": {
        "category_group": "top",
        "categories": {"ao_thun", "ao_so_mi", "ao_khoac"},
        "synonyms": ["oversize", "form rong", "boxy", "rong", "ao rong"],
        "styles": ["Streetwear", "Casual", "Minimalism"],
        "tags": ["oversize", "boxy", "form rong", "cotton"],
        "display_name": "Form rộng Oversize / Boxy",
    },
    "áo baby tee": {
        "category_group": "top",
        "categories": {"ao_thun"},
        "synonyms": ["baby tee", "croptop", "ao om", "ao thun nu", "babytee"],
        "styles": ["Y2K", "Năng động", "Trẻ trung"],
        "tags": ["baby tee", "croptop", "cotton", "om"],
        "display_name": "Áo Baby Tee ôm dáng",
    },
    "áo polo": {
        "category_group": "top",
        "categories": {"ao_thun"},
        "synonyms": ["polo", "co be", "det kim", "ao polo"],
        "styles": ["Old Money", "Smart Casual", "Lịch sự"],
        "tags": ["polo", "det kim", "co be"],
        "display_name": "Áo Polo cổ bẻ chỉn chu",
    },
    "áo blazer": {
        "category_group": "outer",
        "categories": {"ao_khoac", "ao_so_mi"},
        "synonyms": ["blazer", "vest", "khoac ngoai", "smart casual", "khoac blazer"],
        "styles": ["Smart Casual", "Old Money", "Thanh Lịch", "Công Sở"],
        "tags": ["blazer", "linen", "cong so", "khoac"],
        "display_name": "Áo Blazer thanh lịch",
    },
    "chân váy": {
        "category_group": "bottom",
        "categories": {"chan_vay"},
        "synonyms": ["chan vay", "midi", "xep ly", "chu a", "vay ngan"],
        "styles": ["Nữ tính", "Thanh Lịch", "Dịu dàng"],
        "tags": ["chan vay", "midi", "xep ly", "chu a"],
        "display_name": "Chân váy Midi / Xếp ly",
    },
    "váy maxi": {
        "category_group": "dress",
        "categories": {"vay_dam"},
        "synonyms": ["maxi", "dam maxi", "dam dai", "dam hoa", "vay lua"],
        "styles": ["Nàng thơ", "Quý phái", "Dự tiệc", "Nghỉ dưỡng"],
        "tags": ["maxi", "lua", "hoa nhi", "di bien"],
        "display_name": "Váy Maxi thướt tha",
    },
    "Y2K": {
        "category_group": "style",
        "categories": {"ao_thun", "quan_jeans", "chan_vay", "phu_kien"},
        "synonyms": ["y2k", "retro", "vintage", "90s", "croptop", "kinh mat"],
        "styles": ["Y2K", "Retro", "Vintage", "Streetwear"],
        "tags": ["y2k", "retro", "vintage", "wash", "croptop"],
        "display_name": "Xu hướng Y2K Retro",
    },
    "streetwear": {
        "category_group": "style",
        "categories": {"ao_thun", "quan_jeans", "ao_khoac"},
        "synonyms": ["streetwear", "duong pho", "hip hop", "ngau", "boxy", "hoodie"],
        "styles": ["Streetwear", "Cá tính", "Năng động"],
        "tags": ["streetwear", "boxy", "baggy", "hoodie"],
        "display_name": "Streetwear cá tính",
    },
    "old money": {
        "category_group": "style",
        "categories": {"ao_thun", "ao_so_mi", "ao_khoac", "quan_tay"},
        "synonyms": ["old money", "thanh lich", "sang trong", "co dien", "tinh te", "linen", "satin"],
        "styles": ["Old Money", "Thanh Lịch", "Smart Casual", "Sang Trọng"],
        "tags": ["old money", "linen", "satin", "polo", "blazer", "ong suong"],
        "display_name": "Phong cách Old Money",
    },
    "minimalism": {
        "category_group": "style",
        "categories": {"ao_thun", "ao_so_mi", "quan_tay", "chan_vay"},
        "synonyms": ["minimalism", "toi gian", "basic", "tron", "tinh te"],
        "styles": ["Minimalism", "Tối Giản", "Basic"],
        "tags": ["basic", "minimalism", "toi gian", "tron"],
        "display_name": "Phong cách Tối giản (Minimalism)",
    },
    "Korean fashion": {
        "category_group": "style",
        "categories": {"ao_khoac", "ao_so_mi", "chan_vay", "quan_tay"},
        "synonyms": ["han quoc", "korean", "ulzzang", "tre trung", "cardigan", "mang to"],
        "styles": ["Hàn Quốc", "Korean", "Trẻ Trung", "Nàng Thơ"],
        "tags": ["han quoc", "korean", "form rong"],
        "display_name": "Phong cách Hàn Quốc",
    },
}


def _now_vn_iso() -> str:
    """Thời gian hiện tại theo múi giờ Việt Nam (UTC+7)."""
    vn_tz = dt.timezone(dt.timedelta(hours=7))
    return dt.datetime.now(vn_tz).replace(microsecond=0).isoformat()


def _format_time_ago(iso_str: str) -> str:
    """Hiển thị thời gian ngắn gọn thân thiện với người dùng (ví dụ: 'Cập nhật 15 phút trước')."""
    try:
        updated = dt.datetime.fromisoformat(iso_str)
        vn_tz = dt.timezone(dt.timedelta(hours=7))
        now = dt.datetime.now(vn_tz)
        diff = (now - updated).total_seconds()
        if diff < 60:
            return "Vừa cập nhật"
        if diff < 3600:
            return f"Cập nhật {int(diff // 60)} phút trước"
        if diff < 86400:
            return f"Cập nhật {int(diff // 3600)} giờ trước"
        return f"Cập nhật ngày {updated.strftime('%d/%m')}"
    except Exception:
        return "Cập nhật gần đây"


class TrendService:
    """Dịch vụ AI phát hiện xu hướng thời trang và đề xuất sản phẩm AURA Studio."""

    def __init__(self):
        self._lock = threading.Lock()
        self._trends_cache: List[TrendItem] = []
        self._trending_products_cache: List[TrendingProduct] = []
        self._last_refresh_time: float = 0.0
        self._current_source: str = "demo"
        self._custom_keywords: List[str] = list(SEED_KEYWORDS)

        # Khởi tạo các nguồn dữ liệu theo mẫu Adapter
        self._sources: Dict[str, BaseTrendSource] = {
            "google_trends": GoogleTrendsSource(),
            "demo": DemoTrendSource(),
        }

        # Nạp dữ liệu khởi động từ trends.json hoặc demo
        self._load_from_storage_or_demo()

    # =========================================================================
    # LƯU TRỮ VÀ NẠP DỮ LIỆU CACHE
    # =========================================================================
    def _load_from_storage_or_demo(self):
        """Khởi động: ưu tiên nạp từ app/data/trends.json, nếu chưa có thì dùng Demo."""
        loaded = False
        if os.path.exists(TRENDS_DATA_PATH):
            try:
                with open(TRENDS_DATA_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                trends_data = data.get("trends", [])
                if trends_data:
                    self._trends_cache = [TrendItem(**item) for item in trends_data]
                    self._current_source = data.get("source", "cached")
                    self._last_refresh_time = time.time()
                    self._recalculate_trending_products()
                    loaded = True
            except Exception as e:
                print(f"[TrendService] Không thể đọc {TRENDS_DATA_PATH}: {e}")

        if not loaded:
            self._use_demo_source(save=True)

    def _save_to_storage(self, trends: List[TrendItem], source: str):
        """Lưu kết quả phân tích xu hướng và lịch sử vào app/data/trends.json."""
        try:
            history_records = []
            today_str = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d")

            # Đọc lịch sử cũ nếu file tồn tại
            if os.path.exists(TRENDS_DATA_PATH):
                try:
                    with open(TRENDS_DATA_PATH, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                        history_records = old_data.get("history", [])
                except Exception:
                    history_records = []

            # Thêm điểm hôm nay vào lịch sử (giới hạn 100 bản ghi gần nhất)
            for t in trends[:5]:
                history_records.append({
                    "date": today_str,
                    "keyword": t.keyword,
                    "score": round(t.score, 1),
                })
            # Giữ tối đa 100 mục lịch sử
            history_records = history_records[-100:]

            payload = {
                "updated_at": _now_vn_iso(),
                "source": source,
                "trends": [t.model_dump() for t in trends],
                "history": history_records,
            }
            os.makedirs(os.path.dirname(TRENDS_DATA_PATH), exist_ok=True)
            with open(TRENDS_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TrendService] Không thể ghi file {TRENDS_DATA_PATH}: {e}")

    # =========================================================================
    # THU THẬP VÀ PHÂN TÍCH XU HƯỚNG
    # =========================================================================
    def refresh_trends(self, force: bool = False) -> Dict[str, Any]:
        """Làm mới dữ liệu xu hướng: thu thập, phân tích, chấm điểm, match sản phẩm.

        Sử dụng Lock để đảm bảo chỉ có 1 request thực hiện refresh tại một thời điểm.
        """
        with self._lock:
            now = time.time()
            # Kiểm tra Cache TTL nếu không ép buộc refresh
            if not force and self._trends_cache and (now - self._last_refresh_time < settings.TREND_CACHE_TTL):
                return {
                    "success": True,
                    "source": "cached",
                    "trends_count": len(self._trends_cache),
                    "products_count": len(self._trending_products_cache),
                    "updated_at": self._trends_cache[0].updated_at if self._trends_cache else _now_vn_iso(),
                }

            preferred_source = settings.TREND_SOURCE.lower()
            collected_data = None
            used_source = "demo"

            # 1. Thử nguồn Google Trends nếu cấu hình auto hoặc google_trends
            if preferred_source in ("auto", "google_trends"):
                gt_source = self._sources.get("google_trends")
                if gt_source:
                    try:
                        collected_data = gt_source.fetch_trends(self._custom_keywords)
                        if collected_data:
                            used_source = "google_trends"
                    except Exception as err:
                        print(f"[TrendService] Lỗi khi lấy Google Trends: {err}")
                        collected_data = None

            # 2. Nếu không thành công, thử dùng cache đã lưu
            if not collected_data and self._trends_cache:
                print("[TrendService] Nguồn chính không khả dụng, sử dụng dữ liệu Cache sẵn có.")
                used_source = "cached"
                # Cập nhật lại thời gian refresh để tránh spam nguồn lỗi liên tục
                self._last_refresh_time = now
                return {
                    "success": True,
                    "source": "cached",
                    "trends_count": len(self._trends_cache),
                    "products_count": len(self._trending_products_cache),
                    "updated_at": self._trends_cache[0].updated_at if self._trends_cache else _now_vn_iso(),
                }

            # 3. Fallback cuối cùng: DemoTrendSource
            if not collected_data:
                print("[TrendService] Kích hoạt DemoTrendSource fallback.")
                demo_source = self._sources.get("demo", DemoTrendSource())
                collected_data = demo_source.fetch_trends(self._custom_keywords)
                used_source = "demo"

            # Phân tích và chấm điểm xu hướng
            new_trends = self._analyze_and_score_trends(collected_data, used_source)
            self._trends_cache = new_trends
            self._current_source = used_source
            self._last_refresh_time = now

            # Khớp sản phẩm và tính điểm đề xuất
            self._recalculate_trending_products()

            # Lưu vào file JSON
            self._save_to_storage(new_trends, used_source)

            return {
                "success": True,
                "source": used_source,
                "trends_count": len(self._trends_cache),
                "products_count": len(self._trending_products_cache),
                "updated_at": _now_vn_iso(),
            }

    def _use_demo_source(self, save: bool = True):
        """Khởi động nhanh bằng DemoTrendSource."""
        demo_source = self._sources.get("demo", DemoTrendSource())
        raw_data = demo_source.fetch_trends(self._custom_keywords)
        self._trends_cache = self._analyze_and_score_trends(raw_data, "demo")
        self._current_source = "demo"
        self._last_refresh_time = time.time()
        self._recalculate_trending_products()
        if save:
            self._save_to_storage(self._trends_cache, "demo")

    # =========================================================================
    # CHẤM ĐIỂM XU HƯỚNG (TREND SCORE)
    # =========================================================================
    def _analyze_and_score_trends(self, raw_items: List[Dict[str, Any]], source_name: str) -> List[TrendItem]:
        """Chấm điểm Trend Score (chuẩn hóa 0 đến 100).

        Công thức:
            Trend Score = 60% Growth Score + 25% Search Interest + 15% Store Performance

        Giải thích:
            1. Growth Score (60%): Phản ánh tốc độ bứt phá của xu hướng (không chỉ số lượng tuyệt đối).
               Được chuẩn hóa từ growth_rate: growth_rate = 0% -> 50 điểm, +50% -> 90 điểm, -50% -> 10 điểm.
            2. Search Interest (25%): Độ phổ biến tìm kiếm diện rộng (0-100).
            3. Store Performance (15%): Sức hút thực tế của nhóm sản phẩm này trong kho AURA.
        """
        all_products = product_service.get_all()
        trend_items: List[TrendItem] = []
        now_str = _now_vn_iso()

        for item in raw_items:
            keyword = item["keyword"]
            growth_rate = float(item.get("growth_rate", 0.0))
            search_interest = float(item.get("search_interest", 50.0))

            # 1. Growth Score chuẩn hóa (0 -> 100)
            growth_score = min(100.0, max(0.0, 50.0 + growth_rate * 0.8))

            # 2. Search Interest (0 -> 100)
            interest_score = min(100.0, max(0.0, search_interest))

            # 3. Store Performance: Hiệu quả thực tế của sản phẩm thuộc phong cách này tại cửa hàng
            matching_products = self._find_candidate_products_for_keyword(keyword, all_products)
            if matching_products:
                avg_rating = sum(p.rating for p in matching_products) / len(matching_products)
                avg_sold = sum(p.sold_count for p in matching_products) / len(matching_products)
                store_perf = min(100.0, (avg_rating / 5.0) * 50.0 + min(50.0, avg_sold / 2.0))
            else:
                store_perf = 50.0

            # Tính điểm Trend Score tổng hợp
            trend_score = (
                0.60 * growth_score
                + 0.25 * interest_score
                + 0.15 * store_perf
            )
            trend_score = round(min(100.0, max(0.0, trend_score)), 1)

            # Phân loại trạng thái (Rising / Stable / Declining)
            if growth_rate > settings.TREND_RISING_THRESHOLD:
                status = "rising"
            elif growth_rate < settings.TREND_DECLINING_THRESHOLD:
                status = "declining"
            else:
                status = "stable"

            # Xác định nhóm danh mục (nếu có trong taxonomy)
            meta = TREND_TAXONOMY.get(keyword.strip().lower(), {})
            cat_group = meta.get("category_group", "fashion")

            trend_items.append(
                TrendItem(
                    keyword=keyword,
                    score=trend_score,
                    growth_rate=round(growth_rate, 1),
                    search_interest=round(interest_score, 1),
                    category=cat_group,
                    status=status,
                    source=source_name,
                    updated_at=now_str,
                )
            )

        # Sắp xếp các trend theo điểm số giảm dần
        trend_items.sort(key=lambda t: t.score, reverse=True)
        return trend_items

    # =========================================================================
    # ĐỐI SOÁT VÀ CHẤM ĐIỂM SẢN PHẨM (PRODUCT MATCH SCORE)
    # =========================================================================
    def _find_candidate_products_for_keyword(self, keyword: str, products: List[Product]) -> List[Product]:
        """Lọc nhanh các sản phẩm có liên quan đến từ khóa để tính store performance."""
        k_norm = normalize(keyword)
        meta = TREND_TAXONOMY.get(keyword.strip().lower(), {})
        synonyms = [normalize(s) for s in meta.get("synonyms", [])]
        allowed_cats = meta.get("categories", set())

        candidates = []
        for p in products:
            if not p.in_stock:
                continue
            name_norm = normalize(p.name)
            tags_norm = [normalize(t) for t in p.tags]
            style_norm = normalize(p.style)

            # Khớp theo danh mục
            cat_match = bool(allowed_cats and p.category in allowed_cats)
            # Khớp theo từ khóa / từ đồng nghĩa
            text_match = (
                k_norm in name_norm
                or any(s in name_norm for s in synonyms)
                or any(k_norm in t for t in tags_norm)
                or any(s in t for t in tags_norm for s in synonyms)
                or k_norm in style_norm
            )
            if cat_match or text_match:
                candidates.append(p)
        return candidates

    def _calculate_product_match_score(self, product: Product, trend: TrendItem) -> Tuple[float, str]:
        """Tính điểm độ phù hợp giữa sản phẩm trong kho và xu hướng thời trang.

        Công thức:
            Product Trend Score =
                50% Keyword Match
              + 20% Category Match
              + 15% Tag Match
              + 10% Style Match
              + 5% Store Performance

        Lý do lựa chọn trọng số:
            - Keyword Match (50%): Tên và mô tả sản phẩm là yếu tố rõ ràng nhất thể hiện kiểu dáng.
            - Category Match (20%): Đảm bảo đúng loại trang phục (ví dụ trend quần thì không đề xuất áo).
            - Tag Match (15%): Các nhãn phân loại chi tiết (như "ong suong", "baggy", "oversize").
            - Style Match (10%): Phong cách đồng điệu (Y2K, Old Money, Streetwear).
            - Store Performance (5%): Ưu tiên nhẹ các sản phẩm có đánh giá tốt trong kho mà không làm lấn át trend.
        """
        meta = TREND_TAXONOMY.get(trend.keyword.strip().lower(), {})
        k_norm = normalize(trend.keyword)
        synonyms = [normalize(s) for s in meta.get("synonyms", [])]
        target_cats = meta.get("categories", set())
        target_styles = [normalize(s) for s in meta.get("styles", [])]
        target_tags = [normalize(t) for t in meta.get("tags", [])]

        name_norm = normalize(product.name)
        desc_norm = normalize(product.description)
        prod_tags = [normalize(t) for t in product.tags]
        prod_style = normalize(product.style)

        # 1. Keyword Match (0 - 100)
        kw_score = 0.0
        reason_detail = ""
        if k_norm in name_norm:
            kw_score = 100.0
            reason_detail = f"Tên sản phẩm trực tiếp thuộc xu hướng '{trend.keyword}'"
        elif any(syn in name_norm for syn in synonyms):
            matched_syn = next(syn for syn in synonyms if syn in name_norm)
            kw_score = 85.0
            reason_detail = f"Form dáng '{matched_syn}' chuẩn xu hướng '{trend.keyword}'"
        elif k_norm in desc_norm:
            kw_score = 65.0
            reason_detail = f"Thiết kế mang phong cách '{trend.keyword}'"
        elif any(syn in desc_norm for syn in synonyms):
            kw_score = 50.0
            reason_detail = f"Chất liệu & form dáng phù hợp xu hướng '{trend.keyword}'"
        else:
            # So khớp token
            k_tokens = set(tokens(trend.keyword))
            name_tokens = set(tokens(product.name))
            overlap = len(k_tokens & name_tokens)
            if overlap > 0:
                kw_score = min(60.0, overlap * 25.0)
                reason_detail = f"Họa tiết/chi tiết ăn khớp xu hướng '{trend.keyword}'"

        # 2. Category Match (0 - 100)
        cat_score = 0.0
        if target_cats and product.category in target_cats:
            cat_score = 100.0
        elif not target_cats:
            cat_score = 60.0  # Các xu hướng phong cách tổng thể (Y2K, Old Money) áp dụng cho mọi danh mục

        # 3. Tag Match (0 - 100)
        tag_score = 0.0
        matching_tags = [t for t in prod_tags if t == k_norm or t in synonyms or any(tt in t for tt in target_tags)]
        if matching_tags:
            tag_score = min(100.0, 50.0 + len(matching_tags) * 25.0)

        # 4. Style Match (0 - 100)
        style_score = 0.0
        if any(ts in prod_style for ts in target_styles) or k_norm in prod_style:
            style_score = 100.0
        elif any(has_word(prod_style, ts) for ts in target_styles):
            style_score = 80.0

        # 5. Store Performance (0 - 100)
        store_score = min(100.0, (product.rating / 5.0) * 60.0 + min(40.0, product.sold_count / 3.0))

        match_score = (
            0.50 * kw_score
            + 0.20 * cat_score
            + 0.15 * tag_score
            + 0.10 * style_score
            + 0.05 * store_score
        )
        match_score = round(min(100.0, max(0.0, match_score)), 1)

        # Xây dựng lý do đề xuất tự nhiên
        growth_text = f"+{trend.growth_rate}%" if trend.growth_rate > 0 else f"{trend.growth_rate}%"
        if not reason_detail:
            reason_detail = f"Phù hợp với xu hướng {trend.keyword}"
        full_reason = f"Đang tăng {growth_text} quan tâm • {reason_detail}"

        return match_score, full_reason

    def _recalculate_trending_products(self):
        """Khớp tất cả các sản phẩm còn hàng với các xu hướng hiện tại và sắp xếp đề xuất."""
        if not self._trends_cache:
            self._trending_products_cache = []
            return

        all_products = product_service.get_all()
        # QUY TẮC BẮT BUỘC: CHỈ chọn sản phẩm CÒN HÀNG (stock > 0)
        in_stock_products = [p for p in all_products if p.stock > 0 and p.in_stock]

        scored_candidates: Dict[str, TrendingProduct] = {}

        # Duyệt qua các xu hướng hàng đầu
        top_trends = self._trends_cache[: settings.TREND_LIMIT]
        for trend in top_trends:
            for p in in_stock_products:
                match_score, reason = self._calculate_product_match_score(p, trend)
                if match_score >= 35.0:  # Ngưỡng tối thiểu để được xem là phù hợp
                    # Final Trending Score = 70% Trend Score + 30% Product Trend Score
                    final_score = round(0.70 * trend.score + 0.30 * match_score, 1)

                    # Nếu sản phẩm match nhiều trend khác nhau, giữ lại trend cho điểm cao nhất
                    if p.id not in scored_candidates or final_score > scored_candidates[p.id].final_score:
                        scored_candidates[p.id] = TrendingProduct(
                            product=p,
                            trend_keyword=trend.keyword,
                            trend_score=trend.score,
                            match_score=match_score,
                            final_score=final_score,
                            reason=reason,
                            rank=1,
                        )

        ranked = list(scored_candidates.values())
        # Sắp xếp theo final_score giảm dần, phụ trợ thêm rating và sold_count
        ranked.sort(
            key=lambda x: (
                x.final_score,
                x.product.rating,
                x.product.sold_count,
            ),
            reverse=True,
        )

        # Đánh số thứ tự hạng (Rank)
        for i, item in enumerate(ranked, 1):
            item.rank = i

        self._trending_products_cache = ranked

    # =========================================================================
    # TRUY VẤN PUBLIC APIs
    # =========================================================================
    def get_trends(self, limit: Optional[int] = None) -> List[TrendItem]:
        """Lấy danh sách các xu hướng thời trang hiện tại."""
        # Tự động nạp hoặc làm mới nếu cache trống hoặc hết hạn
        if not self._trends_cache or (time.time() - self._last_refresh_time > settings.TREND_CACHE_TTL):
            self.refresh_trends(force=False)
        lim = limit or settings.TREND_LIMIT
        return self._trends_cache[:lim]

    def get_trending_products(
        self,
        limit: Optional[int] = None,
        gender: Optional[str] = None,
        category: Optional[str] = None,
        user_categories: Optional[List[str]] = None,
        user_styles: Optional[List[str]] = None,
    ) -> List[TrendingProduct]:
        """Lấy danh sách sản phẩm gợi ý dựa trên xu hướng, có hỗ trợ bộ lọc và cá nhân hóa ẩn danh."""
        if not self._trending_products_cache or (time.time() - self._last_refresh_time > settings.TREND_CACHE_TTL):
            self.refresh_trends(force=False)

        results = list(self._trending_products_cache)

        # Lọc theo giới tính
        if gender and gender != "all":
            results = [r for r in results if r.product.gender in (gender, "unisex")]

        # Lọc theo danh mục
        if category and category not in ("all", "flash_sale"):
            results = [r for r in results if r.product.category == category]

        # Áp dụng cá nhân hóa nếu client cung cấp hành vi mua sắm ẩn danh
        if user_categories or user_styles:
            results = self._personalize_ranking(results, user_categories or [], user_styles or [])

        # Kiểm tra lại một lần nữa tính sẵn có trong kho (stock > 0)
        results = [r for r in results if r.product.stock > 0 and r.product.in_stock]

        lim = limit or settings.TREND_PRODUCT_LIMIT
        final_list = results[:lim]

        # Đánh lại rank sau khi lọc
        for i, item in enumerate(final_list, 1):
            item.rank = i

        return final_list

    def _personalize_ranking(
        self,
        items: List[TrendingProduct],
        user_categories: List[str],
        user_styles: List[str],
    ) -> List[TrendingProduct]:
        """Tính điểm cá nhân hóa:

        Personalized Score = 70% Global Trend Score + 30% User Interest Score

        Đảm bảo trải nghiệm cá nhân hóa không đẩy các món không bắt trend lên quá mức.
        """
        user_cats_set = set(user_categories)
        user_styles_set = {normalize(s) for s in user_styles}

        scored = []
        for item in items:
            p = item.product
            user_affinity = 0.0
            if p.category in user_cats_set:
                user_affinity += 50.0
            if normalize(p.style) in user_styles_set:
                user_affinity += 50.0

            personalized_score = 0.70 * item.final_score + 0.30 * user_affinity
            # Bản sao cập nhật điểm cá nhân hóa
            scored.append((personalized_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    def get_debug_info(self) -> TrendDebugResponse:
        """Trả về thông tin chẩn đoán kỹ thuật của hệ thống Trend Detection."""
        now = time.time()
        age = now - self._last_refresh_time
        cache_status = "valid" if age < settings.TREND_CACHE_TTL else "expired"
        last_update_str = self._trends_cache[0].updated_at if self._trends_cache else _now_vn_iso()

        return TrendDebugResponse(
            last_update=last_update_str,
            source=self._current_source,
            cache_status=cache_status,
            total_trends=len(self._trends_cache),
            total_trending_products=len(self._trending_products_cache),
            cache_age_seconds=round(age, 1),
        )


trend_service = TrendService()
