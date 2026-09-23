import time
import pytest
from app.config import settings
from app.models.schemas import Product, TrendItem
from app.services.product_service import product_service
from app.services.trend_service import (
    TrendService,
    trend_service,
)
from app.services.trend_sources.base import BaseTrendSource


# =========================================================================
# TEST 1: Trend score luôn nằm trong khoảng 0 <= score <= 100
# =========================================================================
def test_trend_score_bounds():
    service = TrendService()
    test_cases = [
        {"growth_rate": 0.0, "search_interest": 50.0},
        {"growth_rate": 500.0, "search_interest": 100.0},   # Tăng trưởng cực đại
        {"growth_rate": -100.0, "search_interest": 0.0},    # Giảm cực sâu
        {"growth_rate": 42.5, "search_interest": 86.0},     # Điển hình thực tế
        {"growth_rate": -45.0, "search_interest": 20.0},
    ]
    raw_items = [{"keyword": f"test_kw_{i}", **tc} for i, tc in enumerate(test_cases)]
    results = service._analyze_and_score_trends(raw_items, "test")

    for item in results:
        assert 0.0 <= item.score <= 100.0, f"Điểm {item.score} nằm ngoài phạm vi 0..100"


# =========================================================================
# TEST 2: Trend tăng được xác định đúng: growth_rate > threshold
# =========================================================================
def test_trend_rising_status_threshold():
    service = TrendService()
    rising_growth = settings.TREND_RISING_THRESHOLD + 5.0
    stable_growth = 5.0
    declining_growth = settings.TREND_DECLINING_THRESHOLD - 5.0

    raw_items = [
        {"keyword": "trend_tang", "growth_rate": rising_growth, "search_interest": 80.0},
        {"keyword": "trend_on_dinh", "growth_rate": stable_growth, "search_interest": 60.0},
        {"keyword": "trend_giam", "growth_rate": declining_growth, "search_interest": 40.0},
    ]
    results = {t.keyword: t for t in service._analyze_and_score_trends(raw_items, "test")}

    assert results["trend_tang"].status == "rising"
    assert results["trend_on_dinh"].status == "stable"
    assert results["trend_giam"].status == "declining"


# =========================================================================
# TEST 3: Sản phẩm match đúng trend (ví dụ 'quần ống rộng' match 'Quần Tây Xếp Ly Ống Suông Wide-Leg')
# =========================================================================
def test_product_matching_trend():
    service = TrendService()
    sample_trend = TrendItem(
        keyword="quần ống rộng",
        score=87.0,
        growth_rate=42.5,
        search_interest=78.0,
        category="bottom",
        status="rising",
        source="test",
        updated_at="2026-09-23T18:00:00+07:00",
    )
    prod_wide_leg = product_service.get_by_id("prod_003")  # Quần Tây Xếp Ly Ống Suông Wide-Leg
    assert prod_wide_leg is not None
    assert "ống suông" in prod_wide_leg.name.lower() or "wide-leg" in prod_wide_leg.name.lower()

    # Kiểm tra thuật toán chấm điểm đối soát Trend -> Product
    match_score, reason = service._calculate_product_match_score(prod_wide_leg, sample_trend)
    assert match_score >= 70.0, f"Điểm match {match_score} không đạt kỳ vọng"
    assert "ong suong" in reason or "quần ống rộng" in reason


# =========================================================================
# TEST 4: Sản phẩm hết hàng (stock <= 0) KHÔNG BAO GIỜ xuất hiện trong Trending
# =========================================================================
def test_out_of_stock_never_in_trending():
    # Giả lập sản phẩm hết hàng
    p = product_service.get_by_id("prod_003")
    assert p is not None
    original_stock = p.stock

    try:
        p.stock = 0
        trend_service._recalculate_trending_products()
        prods = trend_service.get_trending_products(limit=20)
        assert all(tp.product.stock > 0 and tp.product.in_stock for tp in prods)
        assert not any(tp.product.id == "prod_003" for tp in prods)
    finally:
        p.stock = original_stock
        trend_service._recalculate_trending_products()


# =========================================================================
# TEST 5: API GET /api/trends hoạt động
# =========================================================================
def test_api_get_trends(client):
    r = client.get("/api/trends")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    first = data[0]
    assert "keyword" in first
    assert "score" in first
    assert "growth_rate" in first
    assert "status" in first
    assert "source" in first
    assert 0 <= first["score"] <= 100
    assert first["status"] in ("rising", "stable", "declining")


# =========================================================================
# TEST 6: API GET /api/trending-products hoạt động
# =========================================================================
def test_api_get_trending_products(client):
    r = client.get("/api/trending-products", params={"limit": 6})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert 1 <= len(data) <= 6

    first = data[0]
    assert "product" in first
    assert "trend_keyword" in first
    assert "trend_score" in first
    assert "match_score" in first
    assert "final_score" in first
    assert "reason" in first
    assert first["rank"] == 1
    assert first["product"]["stock"] > 0

    # Test lọc theo gender
    r_gender = client.get("/api/trending-products", params={"gender": "nu"})
    assert r_gender.status_code == 200
    for item in r_gender.json():
        assert item["product"]["gender"] in ("nu", "unisex")


# =========================================================================
# TEST 7: Nguồn trend lỗi thì fallback hoạt động (không sập server)
# =========================================================================
def test_trend_fallback_when_external_fails(monkeypatch):
    class FailingSource(BaseTrendSource):
        @property
        def source_name(self) -> str:
            return "google_trends"

        def fetch_trends(self, keywords):
            raise ConnectionError("Mất kết nối Internet hoặc lỗi mạng")

    service = TrendService()
    service._sources["google_trends"] = FailingSource()

    # Thử refresh khi nguồn chính ném ngoại lệ
    res = service.refresh_trends(force=True)
    assert res["success"] is True
    # Nguồn trả về phải là fallback (cached hoặc demo)
    assert res["source"] in ("cached", "demo")
    assert len(service.get_trends()) > 0


# =========================================================================
# TEST 8: Cache hoạt động đúng (không gọi lại nguồn ngoài trước khi hết TTL)
# =========================================================================
def test_trend_cache_ttl(monkeypatch):
    service = TrendService()
    fetch_count = 0

    class TrackingSource(BaseTrendSource):
        @property
        def source_name(self) -> str:
            return "demo"

        def fetch_trends(self, keywords):
            nonlocal fetch_count
            fetch_count += 1
            return [
                {"keyword": "test_kw", "growth_rate": 25.0, "search_interest": 80.0, "source": "demo"}
            ]

    service._sources["demo"] = TrackingSource()
    service.refresh_trends(force=True)
    initial_count = fetch_count

    # Lần gọi tiếp theo không dùng force và cache còn hạn -> không được fetch lại
    res = service.refresh_trends(force=False)
    assert res["source"] == "cached"
    assert fetch_count == initial_count


# =========================================================================
# TEST 9: Tích hợp AI Stylist khi hỏi về xu hướng
# =========================================================================
def test_ai_stylist_trend_integration(client):
    r = client.post("/api/ai/chat", json={"user_message": "Xu hướng thời trang hiện nay là gì?", "engine": "rules"})
    assert r.status_code == 200
    data = r.json()
    assert "Xu hướng thời trang" in data["reply"]
    assert len(data["recommended_products"]) > 0


# =========================================================================
# TEST 10: Tìm kiếm 'trend' ưu tiên sản phẩm trending
# =========================================================================
def test_semantic_search_trend_keyword(client):
    r = client.get("/api/ai/search", params={"q": "hot trend", "limit": 4})
    assert r.status_code == 200
    items = r.json()
    assert len(items) > 0
    trending_ids = {tp.product.id for tp in trend_service.get_trending_products(limit=8)}
    # Ít nhất 1 sản phẩm trả về nằm trong top trending
    assert any(p["id"] in trending_ids for p in items)


# =========================================================================
# TEST 11: Cá nhân hóa đề xuất (Personalization)
# =========================================================================
def test_trending_personalization():
    # Khách thích danh mục 'ao_khoac' và phong cách 'Smart Casual'
    regular = trend_service.get_trending_products(limit=8)
    personalized = trend_service.get_trending_products(
        limit=8,
        user_categories=["ao_khoac"],
        user_styles=["Smart Casual"],
    )
    assert len(personalized) > 0
    # Đảm bảo danh sách vẫn chỉ chứa sản phẩm còn hàng
    assert all(tp.product.stock > 0 for tp in personalized)


# =========================================================================
# TEST 12: Debug Endpoint GET /api/trends/debug
# =========================================================================
def test_api_trends_debug(client):
    r = client.get("/api/trends/debug")
    assert r.status_code == 200
    data = r.json()
    assert "last_update" in data
    assert "source" in data
    assert "cache_status" in data
    assert "total_trends" in data
    assert "total_trending_products" in data
    assert data["total_trends"] > 0
