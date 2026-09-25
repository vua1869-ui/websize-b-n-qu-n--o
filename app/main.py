import collections
import math
import os
import threading
import time
import urllib.request
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.schemas import (
    AdminProductPayload, AuthResponse, Category, ChangePasswordRequest,
    ChatRequest, ChatResponse, FlashSaleResponse, LiveCommentRequest,
    LiveCommentResponse, OrderCreateRequest, OrderResponse, OutfitRequest, OutfitResponse,
    Product, QuoteRequest, QuoteResponse, SizeRecommendRequest, SizeRecommendResponse,
    TrendDebugResponse, TrendItem, TrendingProduct, TrendRefreshResponse,
    User, UserLoginRequest, UserProfileUpdateRequest, UserRegisterRequest,
    VideoItem, Voucher, VoucherCheckRequest, VoucherCheckResponse,
)
from app.services.ai_service import ai_service
from app.services.order_service import OrderError, order_service
from app.services.outfit_service import outfit_service
from app.services.product_service import flash_sale_window, product_service
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
    response.set_cookie(key="aura_session", value=token, httponly=False, path="/", max_age=86400 * 7, samesite="lax")
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
    response.set_cookie(key="aura_session", value=token, httponly=False, path="/", max_age=86400 * 7, samesite="lax")
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
