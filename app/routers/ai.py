import collections
import threading
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import settings
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    LiveCommentRequest,
    LiveCommentResponse,
    OutfitRequest,
    OutfitResponse,
    Product,
    SizeRecommendRequest,
    SizeRecommendResponse,
    TrendDebugResponse,
    TrendingProduct,
    TrendItem,
    TrendRefreshResponse,
    User,
)
from app.routers.deps import get_client_ip, require_admin
from app.services.ai_service import ai_service
from app.services.outfit_service import outfit_service
from app.services.product_service import product_service
from app.services.trend_service import trend_service

router = APIRouter(tags=["ai"])

_hits: dict = collections.defaultdict(collections.deque)
_hits_lock = threading.Lock()


def ai_rate_limit(request: Request):
    ip = get_client_ip(request)
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= settings.AI_RATE_LIMIT_PER_MIN:
            raise HTTPException(status_code=429, detail="Bạn thao tác hơi nhanh, vui lòng thử lại sau ít phút")
        q.append(now)


@router.post("/api/ai/chat", response_model=ChatResponse, dependencies=[Depends(ai_rate_limit)])
def chat_with_stylist(body: ChatRequest):
    return ai_service.chat(body)


@router.post("/api/ai/size-recommend", response_model=SizeRecommendResponse)
def recommend_size(body: SizeRecommendRequest):
    return outfit_service.calculate_size(
        height_cm=body.height_cm,
        weight_kg=body.weight_kg,
        gender=body.gender,
        fit_preference=body.fit_preference,
        product_id=body.product_id or None,
    )


@router.post("/api/ai/outfit", response_model=OutfitResponse)
def generate_outfit(body: OutfitRequest):
    if body.product_id and not product_service.get_by_id(body.product_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để phối đồ")
    return outfit_service.get_outfit(
        product_id=body.product_id or None,
        occasion=body.occasion,
        gender=body.gender,
        variant=body.variant,
    )


@router.get("/api/ai/search", response_model=List[Product])
def semantic_search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(8, ge=1, le=20),
):
    return product_service.semantic_search(q, limit=limit)


@router.post("/api/ai/live-comment", response_model=LiveCommentResponse, dependencies=[Depends(ai_rate_limit)])
def handle_live_comment(body: LiveCommentRequest):
    return ai_service.live_reply(body.user_name, body.comment)


# ==========================================
# Trends
# ==========================================
@router.get("/api/trends", response_model=List[TrendItem])
def get_trends(limit: Optional[int] = Query(None, ge=1, le=50)):
    return trend_service.get_trends(limit=limit)


@router.get("/api/trending-products", response_model=List[TrendingProduct])
def get_trending_products(
    limit: int = Query(settings.TREND_PRODUCT_LIMIT, ge=1, le=30),
    gender: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user_cats: Optional[str] = Query(None, description="Danh mục ưa thích (phân cách bằng dấu phẩy)"),
    user_styles: Optional[str] = Query(None, description="Phong cách ưa thích (phân cách bằng dấu phẩy)"),
):
    cats = [c.strip() for c in user_cats.split(",") if c.strip()] if user_cats else None
    styles = [s.strip() for s in user_styles.split(",") if s.strip()] if user_styles else None
    return trend_service.get_trending_products(
        limit=limit,
        gender=gender,
        category=category,
        user_categories=cats,
        user_styles=styles,
    )


@router.post("/api/trends/refresh", response_model=TrendRefreshResponse)
def refresh_trends(force: bool = Query(True), _admin: User = Depends(require_admin)):
    res = trend_service.refresh_trends(force=force)
    return TrendRefreshResponse(**res)


@router.get("/api/trends/debug", response_model=TrendDebugResponse)
def debug_trends(_admin: User = Depends(require_admin)):
    return trend_service.get_debug_info()
