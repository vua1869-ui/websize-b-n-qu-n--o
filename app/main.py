import collections
import hashlib
import hmac
import json
import math
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.schemas import (
    AdminProductPayload, AuthResponse, Category, ChangePasswordRequest,
    ChatRequest, ChatResponse, FlashSaleResponse, InventoryBatchCreate, InventoryBatchItem,
    InvoiceResponse, LiveCommentRequest, LiveCommentResponse, LoyaltyHistoryResponse,
    LoyaltyStatusResponse, OrderCreateRequest, OrderResponse, OrderTrackingResponse,
    OutfitRequest, OutfitResponse, Product, ProductReviewCreate, ProductReviewsResponse,
    ProfitReportResponse, QuoteRequest, QuoteResponse, SizeChartResponse,
    SizeRecommendRequest, SizeRecommendResponse, TrendDebugResponse, TrendItem,
    TrendingProduct, TrendRefreshResponse, User, UserLoginRequest, UserProfileUpdateRequest,
    UserRegisterRequest, VideoItem, Voucher, VoucherCheckRequest, VoucherCheckResponse,
)
from app.db.database import db_service
from app.services.ai_service import ai_service
from app.services.image_service import image_service
from app.services.order_service import OrderError, order_service
from app.services.outfit_service import outfit_service
from app.services.product_import_service import product_import_service
from app.services.product_service import flash_sale_window, product_service
from app.services.size_chart_service import size_chart_service
from app.services.trend_service import trend_service
from app.services.user_service import user_service

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
# Xác thực & Phân quyền (Authentication & RBAC)
# ==========================================
def get_current_user_optional(request: Request) -> Optional[User]:
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("aura_session")
    if not token:
        return None
    return user_service.verify_session_token(token)


def get_current_user(request: Request) -> User:
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập để tiếp tục")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản của bạn đã bị khóa")
    return user


def require_admin(request: Request) -> User:
    user = get_current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập trang quản trị")
    return user


# ==========================================
# Web Pages (Trang Web & Giao diện)
# ==========================================
@app.get("/", response_class=HTMLResponse)
def home_page(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "app_name": settings.APP_NAME,
        "shipping_fee": settings.SHIPPING_FEE,
        "free_ship_threshold": settings.FREE_SHIPPING_THRESHOLD,
        "combo_percent": settings.COMBO_DISCOUNT_PERCENT,
        "version": settings.VERSION,
    })


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, redirect: Optional[str] = None):
    user = get_current_user_optional(request)
    if user and user.is_active:
        target = redirect if redirect and redirect.startswith("/") else ("/admin" if user.role == "admin" else "/profile")
        return RedirectResponse(url=target, status_code=302)
    return templates.TemplateResponse(request, "login.html", {
        "app_name": settings.APP_NAME,
        "redirect_url": redirect or "",
        "version": settings.VERSION,
    })


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    user = get_current_user_optional(request)
    if user and user.is_active:
        return RedirectResponse(url="/profile", status_code=302)
    return templates.TemplateResponse(request, "register.html", {
        "app_name": settings.APP_NAME,
        "version": settings.VERSION,
    })


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    user = get_current_user_optional(request)
    if not user or not user.is_active:
        return RedirectResponse(url="/login?redirect=/profile", status_code=302)
    return templates.TemplateResponse(request, "profile.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "version": settings.VERSION,
    })


@app.get("/order-success/{order_id}", response_class=HTMLResponse)
def order_success_page(request: Request, order_id: str):
    user = get_current_user_optional(request)
    order = order_service.get_order_by_id(order_id)
    if not order:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "order-success.html", {
        "app_name": settings.APP_NAME,
        "order": order,
        "user": user,
        "version": settings.VERSION,
    })


@app.get("/tracking", response_class=HTMLResponse)
def tracking_page(request: Request, code: Optional[str] = None):
    url = f"/?tracking={code}" if code else "/?tracking="
    return RedirectResponse(url=url, status_code=302)


@app.get("/403", response_class=HTMLResponse)
def forbidden_page(request: Request):
    user = get_current_user_optional(request)
    return templates.TemplateResponse(request, "403.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "version": settings.VERSION,
    }, status_code=403)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/admin", status_code=302)
    if user.role != "admin":
        return templates.TemplateResponse(request, "403.html", {
            "app_name": settings.APP_NAME,
            "user": user,
            "version": settings.VERSION,
        }, status_code=403)
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "current_page": "dashboard",
        "version": settings.VERSION,
    })


@app.get("/admin/products", response_class=HTMLResponse)
def admin_products_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/admin/products", status_code=302)
    if user.role != "admin":
        return templates.TemplateResponse(request, "403.html", {
            "app_name": settings.APP_NAME,
            "user": user,
            "version": settings.VERSION,
        }, status_code=403)
    return templates.TemplateResponse(request, "admin/products.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "current_page": "products",
        "version": settings.VERSION,
    })


@app.get("/admin/orders", response_class=HTMLResponse)
def admin_orders_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/admin/orders", status_code=302)
    if user.role != "admin":
        return templates.TemplateResponse(request, "403.html", {
            "app_name": settings.APP_NAME,
            "user": user,
            "version": settings.VERSION,
        }, status_code=403)
    return templates.TemplateResponse(request, "admin/orders.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "current_page": "orders",
        "version": settings.VERSION,
    })


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/admin/users", status_code=302)
    if user.role != "admin":
        return templates.TemplateResponse(request, "403.html", {
            "app_name": settings.APP_NAME,
            "user": user,
            "version": settings.VERSION,
        }, status_code=403)
    return templates.TemplateResponse(request, "admin/users.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "current_page": "users",
        "version": settings.VERSION,
    })


@app.get("/admin/trending", response_class=HTMLResponse)
def admin_trending_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/admin/trending", status_code=302)
    if user.role != "admin":
        return templates.TemplateResponse(request, "403.html", {
            "app_name": settings.APP_NAME,
            "user": user,
            "version": settings.VERSION,
        }, status_code=403)
    return templates.TemplateResponse(request, "admin/trending.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "current_page": "trending",
        "version": settings.VERSION,
    })


@app.get("/admin/inventory", response_class=HTMLResponse)
@app.get("/admin/profit", response_class=HTMLResponse)
@app.get("/admin/reports/profit", response_class=HTMLResponse)
def admin_inventory_and_profit_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/admin/inventory", status_code=302)
    if user.role != "admin":
        return templates.TemplateResponse(request, "403.html", {
            "app_name": settings.APP_NAME,
            "user": user,
            "version": settings.VERSION,
        }, status_code=403)
    return templates.TemplateResponse(request, "admin/profit_report.html", {
        "app_name": settings.APP_NAME,
        "user": user,
        "current_page": "inventory",
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
def quote_order(body: QuoteRequest, request: Request):
    user = get_current_user_optional(request)
    return order_service.build_quote(body.items, body.voucher_code, use_points=body.use_points, user=user)


@app.post("/api/orders", response_model=OrderResponse)
def create_order(body: OrderCreateRequest, request: Request):
    user = get_current_user_optional(request)
    return order_service.create_order(body, user=user)


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


# ==========================================
# API Xác thực người dùng (Auth APIs)
# ==========================================
@app.post("/api/auth/register", response_model=AuthResponse)
def auth_register(body: UserRegisterRequest, response: Response):
    try:
        user = user_service.register(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token = user_service.create_session_token(user)
    response.set_cookie(
        key="aura_session",
        value=token,
        httponly=True,
        secure=not settings.DEBUG,
        path="/",
        max_age=86400 * 7,
        samesite="lax",
    )
    return AuthResponse(token=token, user=user, message="Đăng ký tài khoản thành công!")


@app.post("/api/auth/login", response_model=AuthResponse)
def auth_login(body: UserLoginRequest, response: Response):
    identifier = body.username or body.username_or_email
    if not identifier:
        raise HTTPException(status_code=422, detail="Vui lòng nhập tên đăng nhập hoặc email")
    try:
        user = user_service.authenticate(identifier, body.password)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not user:
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không chính xác")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản này đã bị tạm khóa bởi quản trị viên")
    token = user_service.create_session_token(user)
    response.set_cookie(
        key="aura_session",
        value=token,
        httponly=True,
        secure=not settings.DEBUG,
        path="/",
        max_age=86400 * 7,
        samesite="lax",
    )
    return AuthResponse(success=True, token=token, user=user, message="Đăng nhập thành công!")


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(key="aura_session", path="/")
    return {"success": True, "message": "Đã đăng xuất thành công"}


@app.get("/api/auth/me", response_model=User)
def auth_me(user: User = Depends(get_current_user)):
    return user


@app.put("/api/auth/profile", response_model=User)
def auth_update_profile(body: UserProfileUpdateRequest, user: User = Depends(get_current_user)):
    updated = user_service.update_profile(user.id, full_name=body.full_name, phone=body.phone, address=body.address)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản người dùng")
    return updated


@app.post("/api/auth/change-password")
def auth_change_password(body: ChangePasswordRequest, user: User = Depends(get_current_user)):
    ok, msg = user_service.change_password(user.id, body.old_password, body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@app.get("/api/auth/orders")
def auth_user_orders(user: User = Depends(get_current_user)):
    all_orders = order_service.get_orders(limit=100)
    user_orders = []
    for o in all_orders:
        c = o.get("customer", {})
        if (user.phone and c.get("phone") == user.phone) or \
           (c.get("name") and c.get("name").strip().lower() == user.full_name.strip().lower()) or \
           (o.get("username") == user.username):
            user_orders.append(o)
    return user_orders


# ==========================================
# API Quản trị viên (Admin APIs)
# ==========================================
@app.get("/api/admin/stats")
def admin_stats(_admin: User = Depends(require_admin)):
    stats = order_service.get_admin_stats()
    all_products = product_service.get_all()
    stats["total_products"] = len(all_products)
    stats["products_in_stock"] = sum(1 for p in all_products if p.stock > 10)
    stats["products_low_stock"] = sum(1 for p in all_products if 0 < p.stock <= 10)
    stats["products_out_of_stock"] = sum(1 for p in all_products if p.stock == 0)
    stats["total_users"] = len(user_service.get_all_users())
    return stats


@app.get("/api/admin/products")
def admin_products(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    _admin: User = Depends(require_admin),
):
    all_items = product_service.get_all(category=category, gender=gender, search=search)
    total = len(all_items)
    pages = max(1, math.ceil(total / limit))
    offset = (page - 1) * limit
    items = all_items[offset: offset + limit]
    return {
        "items": [item.model_dump() for item in items],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
    }


@app.post("/api/admin/products")
def admin_create_product(body: AdminProductPayload, _admin: User = Depends(require_admin)):
    created = product_service.create_product(body.model_dump(exclude_unset=True))
    return created


@app.put("/api/admin/products/{product_id}")
def admin_update_product(product_id: str, body: AdminProductPayload, _admin: User = Depends(require_admin)):
    data = body.model_dump(exclude_unset=True)
    updated = product_service.update_product(product_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để cập nhật")
    return updated


@app.delete("/api/admin/products/{product_id}")
def admin_delete_product(product_id: str, _admin: User = Depends(require_admin)):
    ok = product_service.delete_product(product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để xóa")
    return {"success": True, "message": f"Đã xóa sản phẩm {product_id} thành công"}


@app.post("/api/admin/products/import")
async def admin_import_products(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
):
    filename = file.filename or ""
    lower_name = filename.lower()
    if not (lower_name.endswith(".csv") or lower_name.endswith(".xlsx") or lower_name.endswith(".xls")):
        raise HTTPException(status_code=400, detail="Định dạng file không hỗ trợ. Vui lòng tải lên file .csv hoặc .xlsx")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File rỗng, vui lòng kiểm tra lại nội dung")

    try:
        report = product_import_service.import_products(content, filename)
        return report
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi xử lý file import: {str(e)}")


@app.post("/api/admin/upload-image")
async def admin_upload_image(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
):
    filename = file.filename or ""
    content_type = (file.content_type or "").lower()
    lower_name = filename.lower()

    valid_exts = [".jpg", ".jpeg", ".png", ".webp"]
    valid_mimes = ["image/jpeg", "image/png", "image/webp", "image/jpg"]

    is_valid_ext = any(lower_name.endswith(ext) for ext in valid_exts)
    is_valid_mime = any(content_type.startswith(m) for m in valid_mimes)

    if not (is_valid_ext or is_valid_mime):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file ảnh định dạng JPG, PNG hoặc WEBP")

    content = await file.read()
    max_size = 5 * 1024 * 1024  # 5MB
    if len(content) > max_size:
        raise HTTPException(status_code=400, detail="Dung lượng ảnh vượt quá giới hạn tối đa 5MB")

    try:
        result = image_service.upload_image(content, folder="aura_store")
        return {
            "url": result["url"],
            "public_id": result["public_id"],
            "format": result.get("format"),
            "bytes": result.get("bytes"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể tải ảnh lên Cloudinary: {str(e)}")


@app.delete("/api/admin/images/{public_id:path}")
def admin_delete_image(
    public_id: str,
    _admin: User = Depends(require_admin),
):
    ok = image_service.delete_image(public_id)
    return {"success": ok}


@app.post("/api/admin/products/{product_id}/inventory", response_model=InventoryBatchItem)
def admin_add_inventory_batch(
    product_id: str,
    body: InventoryBatchCreate,
    _admin: User = Depends(require_admin),
):
    prod = product_service.get_by_id(product_id)
    if not prod:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy sản phẩm với mã '{product_id}'")
    try:
        batch = db_service.add_inventory_batch(
            product_id=product_id,
            quantity=body.quantity,
            cost_price=body.cost_price,
            note=body.note,
            created_by=_admin.username,
        )
        return batch
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/products/{product_id}/inventory", response_model=List[InventoryBatchItem])
def admin_get_inventory_batches(
    product_id: str,
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
):
    prod = product_service.get_by_id(product_id)
    if not prod:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy sản phẩm với mã '{product_id}'")
    batches = db_service.get_inventory_batches(product_id=product_id, limit=limit)
    return batches


@app.get("/api/admin/reports/profit", response_model=ProfitReportResponse)
def admin_profit_report(_admin: User = Depends(require_admin)):
    report = db_service.get_profit_loss_report()
    return report


@app.get("/api/admin/orders")
def admin_get_orders(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
):
    return order_service.get_orders(status=status, search=search, limit=limit, offset=offset)


@app.put("/api/admin/orders/{order_id}/status")
def admin_update_order_status(order_id: str, body: dict, _admin: User = Depends(require_admin)):
    new_status = body.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Thiếu trường status")
    valid_statuses = ["pending_payment", "confirmed", "shipping", "completed", "cancelled"]
    if new_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Trạng thái không hợp lệ. Phải thuộc: {', '.join(valid_statuses)}")
    updated = order_service.update_order_status(order_id, new_status)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")
    return updated


@app.get("/api/admin/users")
def admin_get_users(_admin: User = Depends(require_admin)):
    return user_service.get_all_users()


@app.put("/api/admin/users/{user_id}/role")
def admin_update_user_role(user_id: str, body: dict, _admin: User = Depends(require_admin)):
    role = body.get("role")
    if role not in ["admin", "user"]:
        raise HTTPException(status_code=400, detail="Vai trò phải là 'admin' hoặc 'user'")
    try:
        updated = user_service.update_role(user_id, role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return updated


@app.put("/api/admin/users/{user_id}/status")
def admin_update_user_status(user_id: str, body: dict, _admin: User = Depends(require_admin)):
    if "is_active" not in body:
        raise HTTPException(status_code=400, detail="Thiếu trường is_active")
    is_active = bool(body["is_active"])
    try:
        updated = user_service.toggle_status(user_id, is_active)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return updated


@app.get("/api/admin/trending")
def admin_get_trending(_admin: User = Depends(require_admin)):
    return trend_service.get_trends()


@app.post("/api/admin/trending/refresh")
def admin_refresh_trending(_admin: User = Depends(require_admin)):
    res = trend_service.refresh_trends(force=True)
    return res


# ==========================================
# Địa giới hành chính 2 cấp (Nghị quyết 202/2025/QH15)
# ==========================================
from app.services.geo_service import geo_service

@app.get("/api/locations")
def get_locations():
    """
    Lấy danh mục 34 Tỉnh/Thành phố và Xã/Phường/Đặc khu
    theo cơ cấu 2 cấp hành chính mới chính thức từ 1/7/2025 (đọc tĩnh từ vn_locations.json, cache sẵn).
    """
    return geo_service.get_all_locations()


# ==========================================
# Giai đoạn 1: Địa giới hành chính Việt Nam (Tương thích ngược)
# ==========================================

@app.get("/api/geo/provinces", response_model=List[str])
def get_provinces():
    """Lấy danh sách 63 tỉnh/thành phố chuẩn hóa."""
    return geo_service.get_provinces()


@app.get("/api/geo/districts", response_model=List[str])
def get_districts(
    province: Optional[str] = Query(None),
    province_code: Optional[str] = Query(None),
):
    """Lấy danh sách quận/huyện theo tỉnh/thành phố."""
    prov = province or province_code or ""
    code_map = {"01": "Hà Nội", "79": "TP. Hồ Chí Minh", "48": "Đà Nẵng", "31": "Hải Phòng", "92": "Cần Thơ"}
    prov = code_map.get(prov, prov)
    return geo_service.get_districts(prov)


@app.get("/api/geo/wards", response_model=List[str])
def get_wards(
    province: Optional[str] = Query(""),
    province_code: Optional[str] = Query(""),
    district: Optional[str] = Query(None),
    district_code: Optional[str] = Query(None),
):
    """Lấy danh sách phường/xã theo quận/huyện."""
    prov = province or province_code or ""
    code_map = {"01": "Hà Nội", "79": "TP. Hồ Chí Minh", "48": "Đà Nẵng", "31": "Hải Phòng", "92": "Cần Thơ"}
    prov = code_map.get(prov, prov)
    dist = district or district_code or ""
    dist_map = {"001": "Quận Ba Đình", "002": "Quận Hoàn Kiếm", "004": "Quận Hai Bà Trưng", "005": "Quận Đống Đa"}
    dist = dist_map.get(dist, dist)
    return geo_service.get_wards(prov, dist)


# ==========================================
# Giai đoạn 1: Ma trận Biến thể & Tồn kho Màu x Size
# ==========================================
@app.get("/api/products/{product_id}/variants")
def get_product_variants(product_id: str):
    """Lấy ma trận biến thể tồn kho (Màu x Size) của sản phẩm từ CSDL SQLite."""
    from app.db.database import db_service
    return db_service.get_product_variants(product_id)


# ==========================================
# Giai đoạn 1: Cổng thanh toán VietQR & Webhook tự động
# ==========================================
import uuid
from pydantic import BaseModel

class PaymentWebhookPayload(BaseModel):
    order_id: Optional[str] = None
    amount: Optional[int] = None
    transaction_code: Optional[str] = None
    channel: Optional[str] = "vietqr"
    content: Optional[str] = None
    transferAmount: Optional[int] = None
    referenceCode: Optional[str] = None


@app.post("/api/payment/webhook")
async def payment_webhook(request: Request):
    """
    Webhook tự động nhận thông báo biến động số dư từ VietQR / PayOS / SePay.
    Tự động cập nhật trạng thái đơn hàng sang 'confirmed' và thanh toán 'paid'.
    Yêu cầu xác thực chữ ký HMAC-SHA256 trong header X-Signature.
    """
    x_signature = request.headers.get("X-Signature")
    if not x_signature:
        raise HTTPException(status_code=401, detail="Thiếu chữ ký xác thực X-Signature")

    raw_body = await request.body()
    secret = settings.PAYMENT_WEBHOOK_SECRET
    computed_sig = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(x_signature.lower(), computed_sig.lower()):
        raise HTTPException(status_code=401, detail="Chữ ký webhook không hợp lệ")

    import re
    try:
        data = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
        body = PaymentWebhookPayload(**data)
    except Exception:
        raise HTTPException(status_code=400, detail="Dữ liệu payload webhook không hợp lệ")

    order_id = body.order_id
    if not order_id and body.content:
        # 1. Tìm chuẩn định dạng AURA-YYMMDD-XXXXXX
        m = re.search(r"(AURA[-_]\d{6}[-_][A-Za-z0-9]+)", body.content, re.IGNORECASE)
        if m:
            order_id = m.group(1).upper()
        else:
            # 2. Tìm cú pháp 'AURA <order_id>'
            parts = body.content.strip().split()
            for i, p in enumerate(parts):
                if p.upper() == "AURA" and i + 1 < len(parts):
                    order_id = parts[i + 1].strip()
                    break

    if not order_id:
        raise HTTPException(status_code=400, detail="Không tìm thấy mã đơn hàng trong payload webhook")

    from app.db.database import db_service
    amount = body.amount or body.transferAmount or 0
    tx_code = body.transaction_code or body.referenceCode or f"TX-{uuid.uuid4().hex[:8].upper()}"
    ok = db_service.confirm_payment(
        order_id=order_id,
        amount=amount,
        transaction_code=tx_code,
        payment_channel=body.channel or "vietqr",
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{order_id}'")
    return {"success": True, "order_id": order_id, "message": f"Đã xác nhận thanh toán đơn {order_id} thành công!"}


@app.get("/api/payment/check-status/{order_id}")
def check_order_payment_status(order_id: str):
    """Kiểm tra trạng thái thanh toán theo thời gian thực (Polling khi khách quét mã QR)."""
    from app.db.database import db_service
    status = db_service.get_order_payment_status(order_id)
    if not status.get("exists"):
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
    return status


def check_simulate_enabled():
    if not settings.DEBUG:
        raise HTTPException(status_code=404, detail="Endpoint không tồn tại")


@app.post("/api/payment/simulate-success/{order_id}", dependencies=[Depends(check_simulate_enabled)])
def simulate_payment_success(order_id: str, _admin: User = Depends(require_admin)):
    """Giả lập chuyển khoản thành công để trải nghiệm thử nghiệm dòng tiền và webhook."""
    from app.db.database import db_service
    order = db_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
    
    amount = order.get("total_amount") or order.get("quote", {}).get("total", 0)
    tx_code = f"MB-{uuid.uuid4().hex[:8].upper()}"
    db_service.confirm_payment(order_id=order_id, amount=amount, transaction_code=tx_code, payment_channel="simulation")
    return {
        "success": True,
        "order_id": order_id,
        "transaction_code": tx_code,
        "status": "paid",
        "payment_status": "paid",
        "order_status": "confirmed",
        "message": "Thanh toán QR thành công! Trạng thái đơn hàng đã tự động xác nhận."
    }


# ==========================================
# Cổng thanh toán VNPay (Sandbox / Production)
# ==========================================
class VNPayCreatePaymentRequest(BaseModel):
    order_id: str
    bank_code: Optional[str] = None


@app.post("/api/payment/vnpay/create-payment-url")
def vnpay_create_payment_url(body: VNPayCreatePaymentRequest, request: Request):
    """
    Tạo URL thanh toán VNPay chuẩn HMAC-SHA512.
    Frontend chuyển hướng khách hàng sang URL này để thực hiện thanh toán.
    """
    from app.db.database import db_service
    from app.services.order_service import order_service
    order = db_service.get_order_by_id(body.order_id)
    if not order:
        order = order_service.get_order_by_id(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    client_ip = request.client.host if request.client else "127.0.0.1"
    from app.services.vnpay_service import vnpay_service
    payment_url = vnpay_service.build_payment_url(
        order_id=body.order_id,
        amount=amount,
        client_ip=client_ip,
        bank_code=body.bank_code,
    )
    return {
        "success": True,
        "order_id": body.order_id,
        "payment_url": payment_url,
    }


@app.get("/api/payment/vnpay/return")
def vnpay_payment_return(request: Request):
    """
    Xử lý khi VNPay redirect khách hàng về sau khi hoàn tất giao dịch.
    Xác thực chữ ký HMAC-SHA512. Nếu hợp lệ và vnp_ResponseCode == '00', xác nhận đơn hàng thành công.
    """
    from app.services.vnpay_service import vnpay_service
    params = dict(request.query_params)
    is_valid, vnp_data = vnpay_service.verify_response(params)

    order_id = vnp_data.get("vnp_TxnRef")
    response_code = vnp_data.get("vnp_ResponseCode")
    transaction_no = vnp_data.get("vnp_TransactionNo") or f"VNPAY-{order_id}"
    amount_raw = int(vnp_data.get("vnp_Amount", 0))
    amount = amount_raw // 100

    accept_header = request.headers.get("accept", "")
    is_json_request = "application/json" in accept_header.lower()
    is_browser_html = not is_json_request

    if not is_valid:
        if is_browser_html:
            return HTMLResponse(
                content="""
                <div style="font-family:sans-serif;text-align:center;padding:50px;">
                    <h2 style="color:#e11d48;">Lỗi bảo mật thanh toán</h2>
                    <p>Chữ ký bảo mật VNPay không hợp lệ.</p>
                    <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                </div>
                """,
                status_code=400,
            )
        raise HTTPException(status_code=400, detail="Chữ ký phản hồi VNPay không hợp lệ")

    from app.db.database import db_service
    from app.services.order_service import order_service
    if response_code == "00":
        db_service.confirm_payment(
            order_id=order_id,
            amount=amount,
            transaction_code=transaction_no,
            payment_channel="vnpay",
        )
        order_service.update_order_status(order_id, "confirmed")
        if is_browser_html:
            return RedirectResponse(url=f"/order-success/{order_id}", status_code=302)
        return {
            "success": True,
            "order_id": order_id,
            "response_code": response_code,
            "payment_status": "paid",
            "order_status": "confirmed",
            "message": "Thanh toán VNPay thành công!",
        }
    else:
        if is_browser_html:
            return HTMLResponse(
                content=f"""
                <div style="font-family:sans-serif;text-align:center;padding:50px;">
                    <h2 style="color:#d97706;">Giao dịch chưa hoàn tất</h2>
                    <p>Mã đơn hàng: <strong>{order_id}</strong></p>
                    <p>Mã phản hồi VNPay: <strong>{response_code}</strong></p>
                    <p>Giao dịch thanh toán chưa thành công hoặc quý khách đã hủy thanh toán.</p>
                    <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                </div>
                """
            )
        return {
            "success": False,
            "order_id": order_id,
            "response_code": response_code,
            "payment_status": "unpaid",
            "order_status": "pending_payment",
            "message": f"Giao dịch không thành công (Mã lỗi VNPay: {response_code})",
        }


@app.api_route("/api/payment/vnpay/ipn", methods=["GET", "POST"])
async def vnpay_ipn(request: Request):
    """
    Xử lý Instant Payment Notification (IPN) từ server VNPay gọi ngầm.
    Xác thực chữ ký HMAC-SHA512 độc lập với Return URL và cập nhật trạng thái đơn hàng.
    Trả JSON chuẩn VNPay: {"RspCode": "00", "Message": "Confirm Success"}
    """
    from app.services.vnpay_service import vnpay_service
    params = dict(request.query_params)
    if not params and request.method == "POST":
        try:
            form_data = await request.form()
            params = dict(form_data)
        except Exception:
            try:
                body_data = await request.json()
                params = dict(body_data)
            except Exception:
                params = {}

    is_valid, vnp_data = vnpay_service.verify_response(params)
    if not is_valid:
        return {"RspCode": "97", "Message": "Invalid signature"}

    order_id = vnp_data.get("vnp_TxnRef")
    from app.db.database import db_service
    from app.services.order_service import order_service
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        return {"RspCode": "01", "Message": "Order not found"}

    order_amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    vnp_amount = int(vnp_data.get("vnp_Amount", 0)) // 100
    if vnp_amount != order_amount:
        return {"RspCode": "04", "Message": "Invalid amount"}

    if order.get("payment_status") == "paid" or order.get("status") == "confirmed" or order.get("order_status") == "confirmed":
        return {"RspCode": "02", "Message": "Order already confirmed"}

    response_code = vnp_data.get("vnp_ResponseCode")
    transaction_no = vnp_data.get("vnp_TransactionNo") or f"VNPAY-{order_id}"

    if response_code == "00":
        db_service.confirm_payment(
            order_id=order_id,
            amount=vnp_amount,
            transaction_code=transaction_no,
            payment_channel="vnpay",
        )
        order_service.update_order_status(order_id, "confirmed")
        return {"RspCode": "00", "Message": "Confirm Success"}
    else:
        return {"RspCode": "00", "Message": "Payment failed acknowledged"}


# ==========================================
# Giai đoạn 2: Đánh giá & Bằng chứng Xã hội (Social Proof & Reviews)
# ==========================================
@app.get("/api/products/{product_id}/reviews", response_model=ProductReviewsResponse)
def get_product_reviews(
    product_id: str,
    rating: Optional[int] = Query(None, ge=1, le=5, description="Lọc theo số sao"),
    limit: int = Query(50, ge=1, le=100)
):
    """Lấy danh sách đánh giá thực tế của sản phẩm kèm phân bổ số sao và số đo người mua."""
    p = product_service.get_by_id(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Sản phẩm không tồn tại")
    from app.db.database import db_service
    res = db_service.get_product_reviews(product_id, rating_filter=rating, limit=limit)
    return res


@app.post("/api/products/{product_id}/reviews")
def add_product_review(
    product_id: str,
    body: ProductReviewCreate,
    current_user: User = Depends(get_current_user),
):
    """Gửi đánh giá và nhận xét mới cho sản phẩm. Yêu cầu đăng nhập và đã mua hàng thành công."""
    p = product_service.get_by_id(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Sản phẩm không tồn tại")

    from app.db.database import db_service
    has_purchased = db_service.check_user_purchased_product(
        user_id=current_user.id,
        product_id=product_id,
        username=current_user.username,
    )
    if not has_purchased:
        raise HTTPException(
            status_code=403,
            detail="Bạn cần mua sản phẩm này trước khi đánh giá.",
        )

    review_dict = body.model_dump()
    if not review_dict.get("user_name") or not str(review_dict["user_name"]).strip():
        review_dict["user_name"] = current_user.full_name or current_user.username

    created = db_service.add_product_review(
        product_id=product_id,
        review_data=review_dict,
        user_id=current_user.id,
        is_verified_buyer=True,
    )

    # Đồng bộ rating và reviews_count trong bộ nhớ cache
    with product_service._lock:
        cached_p = product_service._by_id.get(product_id)
        if cached_p:
            cached_p.reviews_count += 1
            # Cập nhật xấp xỉ
            cached_p.rating = round(
                (cached_p.rating * (cached_p.reviews_count - 1) + body.rating) / cached_p.reviews_count, 1
            )

    return {
        "success": True,
        "message": "Cảm ơn bạn đã gửi đánh giá! Nhận xét của bạn giúp cộng đồng chọn size chuẩn hơn.",
        "review": created,
    }


# ==========================================
# Giai đoạn 2: Bảng số đo chi tiết & Hướng dẫn chọn size
# ==========================================
@app.get("/api/products/{product_id}/size-chart", response_model=SizeChartResponse)
def get_product_size_chart(product_id: str):
    """Lấy bảng thông số đo kích thước thực tế (dài áo, vai, ngực, eo, mông) và hướng dẫn tự đo."""
    p = product_service.get_by_id(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Sản phẩm không tồn tại")
    return size_chart_service.get_chart_for_product(product_id)


# ==========================================
# Giai đoạn 2: Tra cứu Vận đơn & Logistics GHN/GHTK
# ==========================================
@app.get("/api/orders/track/{tracking_or_order_id}", response_model=OrderTrackingResponse)
def track_order_shipment(tracking_or_order_id: str):
    """
    Tra cứu hành trình vận chuyển kiện hàng công khai không cần đăng nhập:
    Hỗ trợ tra cứu bằng: Mã đơn hàng (AURA-...), Mã vận đơn (GHN-..., GHTK-...), hoặc SĐT nhận hàng.
    """
    from app.db.database import db_service
    order = db_service.get_order_by_tracking_or_id(tracking_or_order_id)
    if not order:
        # Thử tìm trong order_service nếu chưa có trong DB
        mem_order = order_service.get_order_by_id(tracking_or_order_id)
        if mem_order:
            order = db_service._format_order_row(dict(mem_order))
            order["order_status"] = mem_order.get("status", "confirmed")
            order["customer_address"] = mem_order.get("customer", {}).get("address", "")
            order["customer_phone"] = mem_order.get("customer", {}).get("phone", "")
            order["customer_name"] = mem_order.get("customer", {}).get("name", "")
            order["total_amount"] = mem_order.get("quote", {}).get("total", 0)

    if not order:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy thông tin đơn hàng hoặc mã vận đơn '{tracking_or_order_id}'. Vui lòng kiểm tra lại!"
        )

    timeline = db_service.get_tracking_timeline(order)
    quote = order.get("quote", {})
    lines = quote.get("lines", [])

    status_labels = {
        "pending_payment": "Chờ thanh toán",
        "confirmed": "Đã xác nhận - Đang đóng gói",
        "picking": "Bưu tá đang lấy hàng",
        "in_transit": "Đang vận chuyển liên tỉnh",
        "delivering": "Shipper đang giao hàng",
        "completed": "Giao hàng thành công",
        "cancelled": "Đơn hàng đã hủy",
    }
    ship_status = order.get("shipping_status", "ready_to_pick")
    if order.get("order_status") == "completed":
        ship_status = "delivered"

    return OrderTrackingResponse(
        order_id=order["order_id"],
        tracking_code=order.get("tracking_code") or f"GHN-VN-{order['order_id'][-6:]}",
        carrier=order.get("carrier") or "Giao Hàng Nhanh (GHN Express)",
        shipping_status=ship_status,
        shipping_status_label=status_labels.get(order.get("order_status"), "Đang xử lý"),
        estimated_delivery=order.get("estimated_delivery") or "2 - 3 ngày tới",
        customer_name=order.get("customer_name") or order.get("customer", {}).get("name", "Khách hàng"),
        customer_phone=order.get("customer_phone") or order.get("customer", {}).get("phone", ""),
        customer_address=order.get("customer_address") or order.get("customer", {}).get("address", ""),
        timeline=timeline,
        items=lines,
        total_amount=order.get("total_amount") or quote.get("total", 0),
        payment_method="Chuyển khoản QR NAPAS" if order.get("payment_method") == "qr_transfer" else "Thanh toán khi nhận hàng (COD)",
        payment_status="Đã thanh toán" if order.get("payment_status") == "paid" else "Chưa thanh toán",
        created_at=order.get("created_at") or "",
    )


# ==========================================
# Giai đoạn 2: Hóa đơn điện tử E-Invoice
# ==========================================
@app.get("/api/orders/{order_id}/invoice", response_model=InvoiceResponse)
def get_order_invoice(order_id: str):
    """Lấy dữ liệu hóa đơn điện tử VAT thương mại phục vụ xem và in ấn (window.print)."""
    from app.db.database import db_service
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
        if order:
            order = db_service._format_order_row(dict(order))

    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng để xuất hóa đơn")

    quote = order.get("quote", {})
    subtotal = quote.get("subtotal", 0)
    shipping_fee = quote.get("shipping_fee", 0)
    discount = quote.get("combo_discount", 0) + quote.get("voucher_discount", 0)
    total = quote.get("total", subtotal + shipping_fee - discount)

    # Thuế GTGT 8% (đã bao gồm trong giá bán niêm yết theo chuẩn bán lẻ VN)
    vat_rate = 8
    vat_amount = round(total * 8 / 108)

    # Sinh mã số hóa đơn điện tử bảo mật
    inv_number = f"INV-AURA-{order['order_id'].replace('AURA-', '')}"
    
    issued_date = order.get("created_at") or time.strftime("%d/%m/%Y %H:%M")

    return InvoiceResponse(
        invoice_number=inv_number,
        order_id=order["order_id"],
        issued_at=issued_date,
        seller={
            "company_name": "CÔNG TY CỔ PHẦN THỜI TRANG AURA STUDIO VIỆT NAM",
            "tax_code": "0317894562",
            "address": "Số 186 Hai Bà Trưng, Phường Đa Kao, Quận 1, TP. Hồ Chí Minh",
            "hotline": "1900 8866 (8:00 - 22:00)",
            "email": "support@aurastudio.vn",
            "website": "https://aurastudio.vn"
        },
        buyer={
            "name": order.get("customer_name") or order.get("customer", {}).get("name", "Khách hàng"),
            "phone": order.get("customer_phone") or order.get("customer", {}).get("phone", ""),
            "address": order.get("customer_address") or order.get("customer", {}).get("address", ""),
        },
        items=quote.get("lines", []),
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        discount_amount=discount,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        total_amount=total,
        payment_method="Chuyển khoản QR NAPAS 247" if order.get("payment_method") == "qr_transfer" else "Thanh toán khi nhận hàng (COD)",
        payment_status="Đã thanh toán" if order.get("payment_status") == "paid" else "Chưa thanh toán (Thu hộ COD)",
        carrier=order.get("carrier") or "Giao Hàng Nhanh (GHN Express)",
        tracking_code=order.get("tracking_code") or f"GHN-VN-{order['order_id'][-6:]}",
    )


@app.post("/api/orders/{order_id}/send-notification")
def send_order_notification(order_id: str, channel: str = Query("zalo", enum=["zalo", "email", "sms"])):
    """Giả lập gửi thông báo tiến độ đơn hàng tự động qua Zalo ZNS / SMS / Email."""
    from app.db.database import db_service
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    cust_name = order.get("customer_name") or order.get("customer", {}).get("name", "Quý khách")
    tracking = order.get("tracking_code") or f"GHN-VN-{order_id[-6:]}"

    return {
        "success": True,
        "channel": channel,
        "recipient": phone,
        "message": f"Đã gửi thông báo hành trình đơn hàng {order_id} (Vận đơn {tracking}) tới {cust_name} qua {channel.upper()} thành công!"
    }


# ==========================================
# Giai đoạn 3: Khách hàng thân thiết & Loyalty Club
# ==========================================
@app.get("/api/loyalty/status", response_model=LoyaltyStatusResponse)
def get_loyalty_status(request: Request):
    """Lấy trạng thái hạng thẻ thành viên, điểm tích lũy và đặc quyền AURA Club."""
    user = get_current_user(request)
    return db_service.get_user_loyalty(user.id)


@app.get("/api/loyalty/history", response_model=LoyaltyHistoryResponse)
def get_loyalty_history(request: Request, limit: int = Query(20, ge=1, le=100)):
    """Lấy lịch sử cộng/trừ điểm thưởng của người dùng."""
    user = get_current_user(request)
    status = db_service.get_user_loyalty(user.id)
    history = db_service.get_loyalty_history(user.id, limit=limit)
    return {
        "points_balance": status["points_balance"],
        "total_spent": status["total_spent"],
        "tier": status["tier"],
        "transactions": history,
    }


@app.post("/api/loyalty/simulate-earn")
def simulate_loyalty_earn(points: int = Query(50, ge=1, le=1000), request: Request = None):
    """Endpoint hỗ trợ test/demo cộng điểm thưởng nhanh cho tài khoản đang đăng nhập."""
    user = get_current_user(request)
    new_bal = db_service.add_loyalty_points(
        user.id, points, "bonus", f"Điểm thưởng trải nghiệm sự kiện AURA (+{points} điểm)"
    )
    return {"success": True, "points_added": points, "new_balance": new_bal}



