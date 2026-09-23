from typing import Any, Dict, List, Optional

from app.services.trend_sources.base import BaseTrendSource

# Dữ liệu xu hướng mẫu chuẩn xác cho thị trường thời trang Việt Nam
# Phục vụ chạy offline, demo sản phẩm hoặc khi các API bên ngoài gặp sự cố.
DEMO_TREND_CATALOG: Dict[str, Dict[str, float]] = {
    "quần ống rộng": {"growth_rate": 42.5, "search_interest": 86.0},
    "áo oversize": {"growth_rate": 35.0, "search_interest": 82.0},
    "áo baby tee": {"growth_rate": 38.2, "search_interest": 75.0},
    "áo blazer": {"growth_rate": 28.5, "search_interest": 79.0},
    "áo polo": {"growth_rate": 22.0, "search_interest": 71.0},
    "quần jeans ống rộng": {"growth_rate": 31.5, "search_interest": 80.0},
    "quần baggy": {"growth_rate": 15.0, "search_interest": 65.0},
    "chân váy": {"growth_rate": 24.5, "search_interest": 74.0},
    "váy maxi": {"growth_rate": 18.0, "search_interest": 68.0},
    "y2k": {"growth_rate": 46.0, "search_interest": 88.0},
    "streetwear": {"growth_rate": 29.0, "search_interest": 84.0},
    "old money": {"growth_rate": 36.5, "search_interest": 81.0},
    "minimalism": {"growth_rate": 12.0, "search_interest": 62.0},
    "korean fashion": {"growth_rate": 26.0, "search_interest": 77.0},
}


class DemoTrendSource(BaseTrendSource):
    """Nguồn xu hướng Demo / Mock: dữ liệu mẫu thực tế, minh bạch và ổn định."""

    @property
    def source_name(self) -> str:
        return "demo"

    def fetch_trends(self, keywords: List[str]) -> Optional[List[Dict[str, Any]]]:
        results = []
        for kw in keywords:
            kw_clean = kw.strip().lower()
            if kw_clean in DEMO_TREND_CATALOG:
                info = DEMO_TREND_CATALOG[kw_clean]
                results.append({
                    "keyword": kw,
                    "growth_rate": info["growth_rate"],
                    "search_interest": info["search_interest"],
                    "source": self.source_name,
                })
            else:
                # Từ khóa tùy chỉnh ngoài danh mục mặc định: sinh điểm xu hướng hợp lý
                results.append({
                    "keyword": kw,
                    "growth_rate": 15.0,
                    "search_interest": 60.0,
                    "source": self.source_name,
                })
        return results
