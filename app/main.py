import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.core.logging import request_id_ctx, setup_logging

# Cấu hình log trước khi import db_service — init_db() chạy ngay lúc import module
setup_logging()
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

logger = logging.getLogger("aura.main")

# Tích hợp Sentry Error Tracking qua SENTRY_DSN
if getattr(settings, "SENTRY_DSN", None) and str(settings.SENTRY_DSN).strip():
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=str(settings.SENTRY_DSN).strip(),
            environment=settings.APP_ENV,
            traces_sample_rate=getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.1),
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            send_default_pii=False,
        )
        logger.info("Sentry monitoring đã được khởi tạo thành công.")
    except Exception as _e:
        logger.warning("Không thể khởi tạo Sentry: %s", _e)


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

    # Kiểm tra an toàn Production: Từ chối khởi động nếu DEMO_DATA=true hoặc secret mặc định
    is_prod = settings.APP_ENV.strip().lower() == "production"
    if is_prod:
        default_secrets = {
            "dev-only-change-me",
            "dev-payment-webhook-secret",
            "secret",
            "changeme",
            "default",
            "admin",
            "123456",
        }
        sec = (settings.SECRET_KEY or "").strip()
        if not sec or sec in default_secrets or len(sec) < 16 or sec.lower().startswith("dev-"):
            msg = "🚨 TỪ CHỐI KHỞI ĐỘNG (APP_ENV=production): SECRET_KEY chưa được cấu hình hoặc vẫn dùng khóa mặc định dev."
            logger.critical(msg)
            raise RuntimeError(msg)

        webhook_sec = (settings.PAYMENT_WEBHOOK_SECRET or "").strip()
        if not webhook_sec or webhook_sec in default_secrets or webhook_sec.lower().startswith("dev-"):
            msg = "🚨 TỪ CHỐI KHỞI ĐỘNG (APP_ENV=production): PAYMENT_WEBHOOK_SECRET chưa được cấu hình an toàn."
            logger.critical(msg)
            raise RuntimeError(msg)

        if getattr(settings, "DEMO_DATA", False):
            msg = "🚨 TỪ CHỐI KHỞI ĐỘNG (APP_ENV=production): DEMO_DATA=true không được phép trong môi trường Production."
            logger.critical(msg)
            raise RuntimeError(msg)

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


# Request-ID Middleware: Tạo hoặc đọc X-Request-ID, đưa vào context logging và response header
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = (
        request.headers.get("X-Request-ID")
        or request.headers.get("X-Correlation-ID")
        or uuid.uuid4().hex[:12]
    )
    token = request_id_ctx.set(req_id)
    request.state.request_id = req_id
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
    finally:
        request_id_ctx.reset(token)


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


# Endpoint kiểm tra sức khỏe hệ thống & CSDL (/healthz)
@app.get("/healthz", tags=["System"])
@app.get("/health", tags=["System"])
def health_check():
    """Kiểm tra tính sẵn sàng của ứng dụng và kết nối cơ sở dữ liệu (Health Check)."""
    db_ok = False
    error_msg = None
    try:
        import sqlite3
        conn = sqlite3.connect(db_service.db_path, timeout=5.0)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            row = cursor.fetchone()
            if row and row[0] == 1:
                db_ok = True
        finally:
            conn.close()
    except Exception as e:
        error_msg = str(e)
        logger.error("Health check DB thất bại: %s", e)

    if not db_ok:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "database": "disconnected",
                "error": error_msg or "Không thể kết nối cơ sở dữ liệu",
            },
        )

    return {
        "status": "ok",
        "database": "connected",
        "app_env": settings.APP_ENV,
        "version": settings.VERSION,
    }


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

