from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseTrendSource(ABC):
    """Lớp cơ sở trừu tượng cho tất cả các nguồn dữ liệu Trend (Adapter Pattern)."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Tên định danh của nguồn (vd: 'google_trends', 'demo', 'cached')."""
        pass

    @abstractmethod
    def fetch_trends(self, keywords: List[str]) -> Optional[List[Dict[str, Any]]]:
        """Thu thập dữ liệu xu hướng cho danh sách từ khóa.

        Trả về danh sách dict dạng:
        [
            {
                "keyword": str,
                "growth_rate": float,       # % tăng trưởng so với giai đoạn trước
                "search_interest": float,   # mức độ quan tâm tìm kiếm (0-100)
                "source": str,
            },
            ...
        ]
        Trả về None nếu nguồn bị lỗi hoặc không khả dụng để chuyển sang fallback.
        """
        pass
