import logging
import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.trend_sources.base import BaseTrendSource

logger = logging.getLogger(__name__)


class GoogleTrendsSource(BaseTrendSource):
    """
    Nguồn xu hướng thực tế từ Google Trends qua thư viện pytrends.
    - Chuẩn hóa đa batch qua từ khóa neo cố định (TREND_ANCHOR_KEYWORD).
    - Sử dụng retries=0 để tránh xung đột urllib3 Retry(method_whitelist) mà không cần monkey-patch toàn cục.
    """

    def __init__(self):
        self._source_name = "google_trends"

    @property
    def source_name(self) -> str:
        return self._source_name

    @property
    def anchor_kw(self) -> str:
        return settings.TREND_ANCHOR_KEYWORD

    def fetch_trends(self, keywords: List[str]) -> Optional[List[Dict[str, Any]]]:
        """Thu thập dữ liệu tìm kiếm từ Google Trends cho thị trường Việt Nam."""
        try:
            from pytrends.request import TrendReq
        except ImportError:
            logger.warning("[GoogleTrendsSource] Thư viện pytrends chưa được cài đặt.")
            return None

        results: List[Dict[str, Any]] = []

        try:
            # retries=0 tránh sử dụng Retry(method_whitelist=...) cũ của pytrends trên urllib3 v2
            pt = TrendReq(
                hl="vi",
                tz=settings.TREND_TIMEZONE,
                timeout=(5, settings.TREND_REFRESH_TIMEOUT),
                retries=0,
                backoff_factor=0.5,
            )

            anchor = settings.TREND_ANCHOR_KEYWORD
            target_keywords = [k for k in keywords if k != anchor]
            include_anchor_in_output = anchor in keywords

            # Mỗi batch gồm 3 từ khóa mục tiêu + 1 từ khóa neo cố định (tổng 4 từ khóa)
            batch_size = 3
            base_anchor_mean: Optional[float] = None

            for i in range(0, len(target_keywords), batch_size):
                batch_targets = target_keywords[i : i + batch_size]
                batch_payload = batch_targets + [anchor]
                try:
                    pt.build_payload(
                        batch_payload,
                        cat=0,
                        timeframe="today 1-m",  # 30 ngày gần nhất
                        geo=settings.TREND_COUNTRY,
                        gprop="",
                    )
                    df = pt.interest_over_time()
                    if df is not None and not df.empty:
                        # Điểm trung bình của từ khóa neo trong batch này
                        if anchor in df.columns:
                            anchor_series = df[anchor].astype(float)
                            anchor_mean = float(anchor_series.mean()) if len(anchor_series) > 0 else 50.0
                        else:
                            anchor_mean = 50.0

                        if base_anchor_mean is None:
                            base_anchor_mean = max(anchor_mean, 1.0)

                        # Hệ số quy đổi về cùng thang đo theo từ khóa neo
                        scale_factor = base_anchor_mean / max(anchor_mean, 1.0)

                        for kw in batch_targets:
                            if kw in df.columns:
                                series = df[kw].astype(float)
                                n = len(series)
                                if n >= 4:
                                    mid = n // 2
                                    prev_mean = series.iloc[:mid].mean()
                                    recent_mean = series.iloc[mid:].mean()
                                    growth = (
                                        (recent_mean - prev_mean)
                                        / max(prev_mean, 5.0)
                                    ) * 100.0
                                    raw_interest = float(series.mean())
                                else:
                                    growth = 0.0
                                    raw_interest = float(series.mean()) if n > 0 else 50.0

                                # Quy đổi mức độ quan tâm theo hệ số neo
                                normalized_interest = raw_interest * scale_factor

                                results.append({
                                    "keyword": kw,
                                    "growth_rate": round(growth, 1),
                                    "search_interest": round(min(100.0, max(0.0, normalized_interest)), 1),
                                    "source": self.source_name,
                                })

                        if include_anchor_in_output and not any(r["keyword"] == anchor for r in results):
                            results.append({
                                "keyword": anchor,
                                "growth_rate": 0.0,
                                "search_interest": round(min(100.0, max(0.0, base_anchor_mean)), 1),
                                "source": self.source_name,
                            })

                    time.sleep(0.3)  # Tránh Google 429
                except Exception as batch_err:
                    logger.warning("[GoogleTrendsSource] Batch %s gặp lỗi: %s", batch_payload, batch_err)
                    continue

            if not results:
                logger.info("[GoogleTrendsSource] Không lấy được dữ liệu từ Google Trends, kích hoạt fallback.")
                return None

            return results

        except Exception as e:
            logger.error("[GoogleTrendsSource] Lỗi toàn cục khi gọi Google Trends: %s", e)
            return None
