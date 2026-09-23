"""Gợi ý theo mùa: đồ hot trong mùa hiện tại, và gợi ý xả hàng khi gần hết mùa.

Thị trường thời trang Việt Nam thường chia hàng theo 2 bộ sưu tập: Xuân Hè và Thu Đông.
Module này không dùng dữ liệu ngoài hay machine learning — chỉ dựa trên:
  1. Ngày hiện tại (mùa nào đang diễn ra, còn bao lâu thì hết mùa)
  2. Dữ liệu bán hàng đã có sẵn trong products.json (sold_count, stock, rating, is_hot)
Nhờ vậy toàn bộ logic minh bạch, test được và giải thích được trong báo cáo/vấn đáp.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.models.schemas import Product
from app.services.product_service import product_service

# Mỗi bộ sưu tập gắn với các mã "occasions" đã có sẵn trong dữ liệu sản phẩm.
COLLECTIONS: List[Dict] = [
    {
        "key": "xuan_he",
        "label": "Xuân Hè",
        "emoji": "☀️",
        "months": [2, 3, 4, 5, 6, 7, 8],
        "occasions": {"mua_he", "di_bien", "nghi_duong"},
    },
    {
        "key": "thu_dong",
        "label": "Thu Đông",
        "emoji": "🍂",
        "months": [9, 10, 11, 12, 1],
        "occasions": {"thu_dong", "mua_dong", "du_lich_da_lat"},
    },
]
CLEARANCE_WINDOW_DAYS = 30   # còn <= 30 ngày là hết mùa -> chuyển sang gợi ý xả hàng


def _month_end(year: int, month: int) -> dt.date:
    nxt = dt.date(year + (month == 12), (month % 12) + 1, 1)
    return nxt - dt.timedelta(days=1)


@dataclass
class SeasonInfo:
    collection: Dict
    days_left: int              # số ngày còn lại của mùa hiện tại
    is_ending_soon: bool        # còn <= CLEARANCE_WINDOW_DAYS ngày
    next_collection: Dict = field(default_factory=dict)


def current_season(today: Optional[dt.date] = None) -> SeasonInfo:
    """Xác định bộ sưu tập đang diễn ra tại ngày `today` (mặc định: hôm nay).

    Nhận `today` làm tham số để có thể kiểm thử mọi thời điểm trong năm mà không phải
    chờ đến đúng ngày, và để demo được cảnh "sắp hết mùa" bất kể ngày chạy demo thật.
    """
    today = today or dt.date.today()
    collection = next(c for c in COLLECTIONS if today.month in c["months"])
    idx = COLLECTIONS.index(collection)
    next_collection = COLLECTIONS[(idx + 1) % len(COLLECTIONS)]

    # Mùa có thể vắt qua năm sau (Thu Đông: tháng 9 -> tháng 1 năm sau)
    last_month = collection["months"][-1]
    end_year = today.year + (1 if last_month < today.month else 0)
    season_end = _month_end(end_year, last_month)
    days_left = (season_end - today).days
    return SeasonInfo(collection=collection, days_left=days_left,
                      is_ending_soon=days_left <= CLEARANCE_WINDOW_DAYS,
                      next_collection=next_collection)


def hot_this_season(today: Optional[dt.date] = None, limit: int = 4) -> List[Product]:
    """Đồ hot của mùa hiện tại: còn hàng, đúng dịp trong mùa, sắp theo mức bán chạy."""
    info = current_season(today)
    occ = info.collection["occasions"]
    pool = [p for p in product_service.get_all(sort="popular")
            if p.in_stock and occ & set(p.occasions)]
    pool.sort(key=lambda p: p.sold_count + (200 if p.is_hot else 0) + p.rating * 20, reverse=True)
    return pool[:limit]


def clearance_candidates(today: Optional[dt.date] = None, limit: int = 6) -> List[Product]:
    """Đồ đúng mùa hiện tại, bán chậm nhất trong mùa (xếp hạng tương đối) -> nên đẩy giá để xả kho.

    Dùng xếp hạng tương đối (thay vì một ngưỡng % cố định) để luôn ra được danh sách hợp lý dù
    tốc độ bán chung của shop cao hay thấp, thay vì phụ thuộc vào một con số ngưỡng đặt cứng.
    """
    info = current_season(today)
    occ = info.collection["occasions"]
    pool = []
    for p in product_service.get_all(sort="popular"):
        if not p.in_stock or not (occ & set(p.occasions)):
            continue
        sell_through = 1 - (p.stock / p.stock_total) if p.stock_total else 1
        pool.append((p, sell_through))
    pool.sort(key=lambda x: (x[1], -x[0].stock))  # bán chậm nhất + tồn nhiều nhất lên trước
    return [p for p, _ in pool[:limit]]


def suggested_discount(p: Product, sell_through: float) -> int:
    """Gợi ý mức giảm giá (%) để đẩy hàng, tăng theo lượng tồn còn nhiều."""
    if sell_through < 0.10:
        return 40
    if sell_through < 0.20:
        return 30
    return 20