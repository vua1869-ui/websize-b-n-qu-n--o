import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.trend_sources.base import BaseTrendSource

# Tương thích với urllib3 v2.x (pytrends dùng tham số method_whitelist đã đổi thành allowed_methods)
try:
    import urllib3.util.retry
    _orig_retry_init = urllib3.util.retry.Retry.__init__
    def _patched_retry_init(self, *args, **kwargs):
        if "method_whitelist" in kwargs:
            kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
        return _orig_retry_init(self, *args, **kwargs)
    urllib3.util.retry.Retry.__init__ = _patched_retry_init
except Exception:
    pass


class GoogleTrendsSource(BaseTrendSource):
    """Nguồn xu hướng thực tế từ Google Trends qua thư viện pytrends."""

    def __init__(self):
        self._source_name = "google_trends"

    @property
    def source_name(self) -> str:
        return self._source_name

    def fetch_trends(self, keywords: List[str]) -> Optional[List[Dict[str, Any]]]:
        """Thu thập dữ liệu tìm kiếm từ Google Trends cho thị trường Việt Nam."""
        try:
            from pytrends.request import TrendReq
        except ImportError:
            print("[GoogleTrendsSource] Thư viện pytrends chưa được cài đặt.")
            return None

        results: List[Dict[str, Any]] = []

        try:
            pt = TrendReq(
                hl="vi",
                tz=settings.TREND_TIMEZONE,
                timeout=(5, settings.TREND_REFRESH_TIMEOUT),
                retries=1,
                backoff_factor=0.5,
            )

            # Google Trends hỗ trợ tối đa 5 từ khóa mỗi payload, chia nhóm 3-4 để ổn định
            batch_size = 4
            for i in range(0, len(keywords), batch_size):
                batch = keywords[i : i + batch_size]
                try:
                    pt.build_payload(
                        batch,
                        cat=0,
                        timeframe="today 1-m",  # 30 ngày gần nhất để nhìn rõ xu thế
                        geo=settings.TREND_COUNTRY,
                        gprop="",
                    )
                    df = pt.interest_over_time()
                    if df is not None and not df.empty:
                        for kw in batch:
                            if kw in df.columns:
                                series = df[kw].astype(float)
                                n = len(series)
                                if n >= 4:
                                    mid = n // 2
                                    prev_mean = series.iloc[:mid].mean()
                                    recent_mean = series.iloc[mid:].mean()
                                    # Công thức tính tỷ lệ tăng trưởng %
                                    growth = (
                                        (recent_mean - prev_mean)
                                        / max(prev_mean, 5.0)
                                    ) * 100.0
                                    interest = float(series.mean())
                                else:
                                    growth = 0.0
                                    interest = float(series.mean()) if n > 0 else 50.0

                                results.append({
                                    "keyword": kw,
                                    "growth_rate": round(growth, 1),
                                    "search_interest": round(min(100.0, max(0.0, interest)), 1),
                                    "source": self.source_name,
                                })
                    time.sleep(0.3)  # Nghỉ nhẹ giữa các batch để tránh bị Google 429
                except Exception as batch_err:
                    print(f"[GoogleTrendsSource] Batch {batch} gặp lỗi: {batch_err}")
                    # Tiếp tục batch khác nếu có
                    continue

            if not results:
                print("[GoogleTrendsSource] Không lấy được dữ liệu từ Google Trends, kích hoạt fallback.")
                return None

            return results

        except Exception as e:
            print(f"[GoogleTrendsSource] Lỗi toàn cục khi gọi Google Trends: {e}")
            return None
