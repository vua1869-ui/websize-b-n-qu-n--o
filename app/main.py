import collections
import os
import threading
import time
import urllib.request
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.schemas import (
    Category, ChatRequest, ChatResponse, FlashSaleResponse, LiveCommentRequest,
    LiveCommentResponse, OrderCreateRequest, OrderResponse, OutfitRequest, OutfitResponse,
    Product, QuoteRequest, QuoteResponse, SizeRecommendRequest, SizeRecommendResponse,
    TrendDebugResponse, TrendItem, TrendingProduct, TrendRefreshResponse,
    VideoItem, Voucher, VoucherCheckRequest, VoucherCheckResponse,
)
from app.services.ai_service import ai_service
from app.services.order_service import OrderError, order_service
from app.services.outfit_service import outfit_service
from app.services.product_service import flash_sale_window, product_service
from app.services.trend_service import trend_service

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Cửa hàng thời trang tích hợp AI Stylist, tính size, phối đồ và Flash Sale.",
)
# Không bật CORS: giao diện và API cùng một origin nên không cần mở cho trang web lạ gọi vào.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _vnd(n: int) -> str:
    return f"{int(n):,}".replace(",", ".") + "đ"


templates.env.filters["vnd"] = _vnd


# ==========================================
# Xử lý lỗi: luôn trả {"detail": "<câu tiếng Việt>"} để giao diện hiển thị được
# ==========================================
FIELD_LABELS = {
    "customer_name": "Họ tên", "customer_phone": "Số điện thoại",
    "customer_address": "Địa chỉ", "height_cm": "Chiều cao (100–230 cm)",
    "weight_kg": "Cân nặng (25–200 kg)", "user_message": "Nội dung tin nhắn",
    "quantity": "Số lượng", "items": "Giỏ hàng",
}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    err = exc.errors()[0]
    field = str(err["loc"][-1]) if err.get("loc") else ""
    msg = str(err.get("msg", ""))
    if msg.startswith("Value error, "):
        detail = msg[len("Value error, "):]
    else:
        label = FIELD_LABELS.get(field, field or "dữ liệu")
        detail = f"{label} không hợp lệ"
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(OrderError)
async def order_error_handler(_: Request, exc: OrderError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


# ==========================================
# Giới hạn tần suất gọi AI (tránh bị spam tốn tiền/tài nguyên)
# ==========================================
_hits: dict = collections.defaultdict(collections.deque)
_hits_lock = threading.Lock()


def ai_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= settings.AI_RATE_LIMIT_PER_MIN:
            raise HTTPException(status_code=429, detail="Bạn thao tác hơi nhanh, vui lòng thử lại sau ít phút")
        q.append(now)


# ==========================================
# Trang chủ
# ==========================================
@app.get("/", response_class=HTMLResponse)
def home_page(request: Request):
    # Starlette mới yêu cầu truyền request là tham số đầu tiên
    return templates.TemplateResponse(request, "index.html", {
        "app_name": settings.APP_NAME,
        "shipping_fee": settings.SHIPPING_FEE,
        "free_ship_threshold": settings.FREE_SHIPPING_THRESHOLD,
        "combo_percent": settings.COMBO_DISCOUNT_PERCENT,
        "version": settings.VERSION,
    })


# ==========================================
# Sản phẩm & khuyến mãi
# ==========================================
@app.get("/api/products", response_model=List[Product])
def get_products(
    category: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    min_price: Optional[int] = Query(None, ge=0),
    max_price: Optional[int] = Query(None, ge=0),
    sort: str = Query("popular", pattern="^(popular|top_sales|price_asc|price_desc|rating|newest)$"),
    search: Optional[str] = Query(None, max_length=100),
    flash_sale_only: bool = Query(False),
):
    return product_service.get_all(category=category, gender=gender, min_price=min_price,
                                   max_price=max_price, sort=sort, search=search,
                                   flash_sale_only=flash_sale_only)


@app.get("/api/products/{product_id}", response_model=Product)
def get_product_detail(product_id: str):
    product = product_service.get_by_id(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm")
    return product


@app.get("/api/products/{product_id}/related", response_model=List[Product])
def get_related_products(product_id: str, limit: int = Query(4, ge=1, le=12)):
    return product_service.get_related(product_id, limit=limit)


@app.get("/api/categories", response_model=List[Category])
def get_categories():
    return product_service.get_categories()


@app.get("/api/flash-sale", response_model=FlashSaleResponse)
def get_flash_sale():
    now_ms, ends_ms = flash_sale_window()
    return FlashSaleResponse(ends_at=ends_ms, server_now=now_ms,
                             items=product_service.get_flash_sale_products())


@app.get("/api/vouchers", response_model=List[Voucher])
def get_vouchers():
    return product_service.get_vouchers()


@app.post("/api/vouchers/validate", response_model=VoucherCheckResponse)
def validate_voucher(body: VoucherCheckRequest):
    v = product_service.get_voucher(body.code)
    if not v:
        return VoucherCheckResponse(valid=False, message=f"Mã '{body.code.strip().upper()}' không tồn tại")
    if body.subtotal < v.min_order:
        return VoucherCheckResponse(
            valid=False, message=f"Mã {v.code} áp dụng cho đơn từ {_vnd(v.min_order)}")
    return VoucherCheckResponse(valid=True, message=f"Có thể áp dụng mã {v.code}")


@app.get("/api/videos", response_model=List[VideoItem])
def get_videos():
    return product_service.get_videos()


# ==========================================
# AI
# ==========================================
@app.post("/api/ai/chat", response_model=ChatResponse, dependencies=[Depends(ai_rate_limit)])
def chat_with_stylist(body: ChatRequest):
    # Hàm đồng bộ (def) -> FastAPI chạy trong threadpool, không chặn các request khác khi chờ LLM
    return ai_service.chat(body)


@app.post("/api/ai/size-recommend", response_model=SizeRecommendResponse)
def recommend_size(body: SizeRecommendRequest):
    return outfit_service.calculate_size(
        height_cm=body.height_cm, weight_kg=body.weight_kg, gender=body.gender,
        fit_preference=body.fit_preference, product_id=body.product_id or None)


@app.post("/api/ai/outfit", response_model=OutfitResponse)
def generate_outfit(body: OutfitRequest):
    if body.product_id and not product_service.get_by_id(body.product_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để phối đồ")
    return outfit_service.get_outfit(product_id=body.product_id or None, occasion=body.occasion,
                                     gender=body.gender, variant=body.variant)


@app.get("/api/ai/search", response_model=List[Product])
def semantic_search(q: str = Query(..., min_length=1, max_length=100), limit: int = Query(8, ge=1, le=20)):
    return product_service.semantic_search(q, limit=limit)


@app.post("/api/ai/live-comment", response_model=LiveCommentResponse,
          dependencies=[Depends(ai_rate_limit)])
def handle_live_comment(body: LiveCommentRequest):
    return ai_service.live_reply(body.user_name, body.comment)


# ==========================================
# AI Trend Detection & Recommendation
# ==========================================
@app.get("/api/trends", response_model=List[TrendItem])
def get_trends(limit: Optional[int] = Query(None, ge=1, le=50)):
    """Lấy danh sách các xu hướng thời trang đang tăng trưởng."""
    return trend_service.get_trends(limit=limit)


@app.get("/api/trending-products", response_model=List[TrendingProduct])
def get_trending_products(
    limit: int = Query(settings.TREND_PRODUCT_LIMIT, ge=1, le=30),
    gender: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user_cats: Optional[str] = Query(None, description="Danh mục ưa thích (phân cách bằng dấu phẩy)"),
    user_styles: Optional[str] = Query(None, description="Phong cách ưa thích (phân cách bằng dấu phẩy)"),
):
    """Lấy danh sách sản phẩm bắt trend được đề xuất, hỗ trợ cá nhân hóa ẩn danh."""
    cats = [c.strip() for c in user_cats.split(",") if c.strip()] if user_cats else None
    styles = [s.strip() for s in user_styles.split(",") if s.strip()] if user_styles else None
    return trend_service.get_trending_products(
        limit=limit,
        gender=gender,
        category=category,
        user_categories=cats,
        user_styles=styles,
    )


@app.post("/api/trends/refresh", response_model=TrendRefreshResponse)
def refresh_trends(force: bool = Query(True)):
    """Endpoint nội bộ: thu thập dữ liệu mới, phân tích, chấm điểm và cập nhật cache."""
    res = trend_service.refresh_trends(force=force)
    return TrendRefreshResponse(**res)


@app.get("/api/trends/debug", response_model=TrendDebugResponse)
def debug_trends():
    """Endpoint debug: thông tin chẩn đoán kỹ thuật về cache, nguồn dữ liệu và số lượng."""
    return trend_service.get_debug_info()


# ==========================================
# Đơn hàng (server tự tính giá)
# ==========================================
@app.post("/api/orders/quote", response_model=QuoteResponse)
def quote_order(body: QuoteRequest):
    return order_service.build_quote(body.items, body.voucher_code)


@app.post("/api/orders", response_model=OrderResponse)
def create_order(body: OrderCreateRequest):
    return order_service.create_order(body)


# ==========================================
# Trạng thái hệ thống
# ==========================================
_status_cache = {"at": 0.0, "ollama": False}


def _ollama_available() -> bool:
    if time.time() - _status_cache["at"] < 30:
        return _status_cache["ollama"]
    try:
        with urllib.request.urlopen(f"{settings.OLLAMA_HOST}/api/tags", timeout=1.5) as r:
            ok = r.status == 200
    except Exception:  # noqa: BLE001
        ok = False
    _status_cache.update(at=time.time(), ollama=ok)
    return ok


@app.get("/api/system/status")
def system_status():
    engines = ["rules"]
    if _ollama_available():
        engines.insert(0, "ollama")
    if settings.GEMINI_API_KEY:
        engines.insert(0, "gemini")
    return {
        "status": "online",
        "app_name": settings.APP_NAME,
        "version": settings.VERSION,
        "ai_engines_available": engines,
        "total_products": len(product_service.get_all()),
        "total_orders": order_service.count_orders(),
    }
