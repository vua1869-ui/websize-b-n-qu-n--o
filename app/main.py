import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.core.logging import setup_logging
from app.db.database import db_service
from app.routers import (
    admin,
    ai,
    auth,
    cart,
    loyalty,
    orders,
    pages,
    payment,
    products,
)
from app.routers.deps import (
    AdminForbiddenException,
    AdminRedirectException,
    _login_pair_attempts,
    _rate_limit_lock,
    base_context,
    get_current_user_optional,
    templates,
)
from app.services.order_service import OrderError

# Khởi tạo logging tập trung
setup_logging()
logger = logging.getLogger("aura.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo DB: Base.metadata.create_all TRƯỚC init_db
    try:
        from app.db.models import Base
        from app.db.session import engine
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.exception("Lỗi khởi tạo metadata database: %s", e)
    try:
        db_service.init_db()
    except Exception as e:
        logger.exception("Lỗi khởi tạo seed data database: %s", e)

    # Cảnh báo DEMO_DATA trong production (MỤC C.3)
    is_prod = settings.APP_ENV.strip().lower() == "production"
    if is_prod and getattr(settings, "DEMO_DATA", False):
        logger.warning(
            "\n" + "=" * 70 + "\n"
            "CẢNH BÁO NGUY HIỂM: DEMO_DATA ĐANG BẬT TRONG MÔI TRƯỜNG PRODUCTION!\n"
            "Dữ liệu số liệu đánh giá, lượt bán mẫu đang được sử dụng.\n"
            "Vui lòng tắt DEMO_DATA=false và chạy scripts/clear_demo_social_proof.py!\n"
            + "=" * 70
        )
    yield


is_prod = settings.APP_ENV.strip().lower() == "production"
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Cửa hàng thời trang tích hợp AI Stylist, tính size, phối đồ và Flash Sale.",
    docs_url=None if is_prod else "/docs",
    redoc_url=None if is_prod else "/redoc",
    openapi_url=None if is_prod else "/openapi.json",
    lifespan=lifespan,
)


# Security headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy-Report-Only"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://images.unsplash.com https://img.vietqr.io https://res.cloudinary.com; "
        "connect-src 'self'"
    )
    return response


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _vnd(n: int) -> str:
    return f"{int(n):,}".replace(",", ".") + "đ"


templates.env.filters["vnd"] = _vnd


# ==========================================
# Exception Handlers
# ==========================================
FIELD_LABELS = {
    "customer_name": "Họ tên",
    "customer_phone": "Số điện thoại",
    "customer_address": "Địa chỉ",
    "height_cm": "Chiều cao (100–230 cm)",
    "weight_kg": "Cân nặng (25–200 kg)",
    "user_message": "Nội dung tin nhắn",
    "quantity": "Số lượng",
    "items": "Giỏ hàng",
}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    err = exc.errors()[0]
    field = str(err["loc"][-1]) if err.get("loc") else ""
    msg = str(err.get("msg", ""))
    if msg.startswith("Value error, "):
        detail = msg[len("Value error, ") :]
    else:
        label = FIELD_LABELS.get(field, field or "dữ liệu")
        detail = f"{label} không hợp lệ"
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(OrderError)
async def order_error_handler(_: Request, exc: OrderError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and "text/html" in request.headers.get("accept", ""):
        user = get_current_user_optional(request)
        ctx = base_context(
            request,
            user=user,
            message=exc.detail if isinstance(exc.detail, str) else "Trang không tồn tại",
        )
        return templates.TemplateResponse(request, "404.html", ctx, status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(AdminRedirectException)
async def admin_redirect_handler(_: Request, exc: AdminRedirectException):
    return RedirectResponse(url=exc.location, status_code=302)


@app.exception_handler(AdminForbiddenException)
async def admin_forbidden_handler(request: Request, exc: AdminForbiddenException):
    ctx = base_context(request, user=exc.user)
    return templates.TemplateResponse(request, "403.html", ctx, status_code=403)


# ==========================================
# Include Routers
# ==========================================
app.include_router(pages.router)
app.include_router(products.router)
app.include_router(ai.router)
app.include_router(orders.router)
app.include_router(payment.router)
app.include_router(auth.router)
app.include_router(cart.router)
app.include_router(admin.router)
app.include_router(loyalty.router)

# Tương thích ngược cho các test import biến rate limit từ app.main
def _login_key(ip: str, username: str) -> str:
    return f"{ip}|{username.strip().lower()}"


_login_fails = _login_pair_attempts
_login_lock = _rate_limit_lock

__all__ = ["app", "_login_key", "_login_fails", "_login_lock"]

