import asyncio
import datetime as dt
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.core.logging import request_id_ctx, setup_logging
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

# Cấu hình log và logger
setup_logging()
logger = logging.getLogger("aura.main")
_last_backup_time: Optional[str] = None

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

        # Kiểm tra tài khoản admin không dùng mật khẩu mặc định
        try:
            from sqlalchemy import func

            from app.db.models import UserDB
            from app.db.session import get_db_session
            from app.services.user_service import verify_password

            with get_db_session() as session:
                admin_user = session.query(UserDB).filter(func.lower(UserDB.username) == "admin").first()
                if admin_user and admin_user.password_hash:
                    for weak_pw in ("admin123", "user123"):
                        if verify_password(weak_pw, admin_user.password_hash):
                            msg = f"🚨 TỪ CHỐI KHỞI ĐỘNG (APP_ENV=production): Tài khoản 'admin' đang dùng mật khẩu mặc định nguy hiểm ('{weak_pw}'). Vui lòng đổi mật khẩu bằng scripts/reset_admin_password.py trước khi khởi chạy."
                            logger.critical(msg)
                            raise RuntimeError(msg)
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("Không thể kiểm tra mật khẩu admin lúc khởi động: %s", e)

    # 7.3 Kiểm tra cảnh báo lệch tồn kho lúc khởi động (không chặn khởi động)
    try:
        from app.services.stock_reconcile import reconcile_stock
        stk_rep = reconcile_stock(apply_fix=False)
        if stk_rep.get("has_mismatch"):
            logger.warning(
                "CẢNH BÁO TỒN KHO: Phát hiện chênh lệch tồn kho giữa CSDL và RAM (%s). "
                "Vui lòng chạy 'python scripts/reconcile_stock.py --apply' để sửa.",
                stk_rep.get("summary"),
            )
    except Exception as _e:
        logger.warning("Không thể kiểm tra đối soát tồn kho lúc khởi động: %s", _e)

    # Khởi chạy các tác vụ nền định kỳ
    bg_tasks = []

    # 1.4 Tự động hủy đơn online quá hạn thanh toán
    order_ttl = getattr(settings, "ORDER_PAYMENT_TTL_MINUTES", 30)
    order_interval = getattr(settings, "ORDER_EXPIRY_INTERVAL_SEC", 60)
    if order_ttl > 0 and order_interval > 0:
        async def _expire_unpaid_loop():
            while True:
                try:
                    await asyncio.sleep(order_interval)
                    from app.services.order_service import order_service
                    await asyncio.to_thread(order_service.expire_unpaid_orders)
                except asyncio.CancelledError:
                    break
                except Exception as ex:
                    logger.warning("Lỗi trong tác vụ nền expire_unpaid_orders: %s", ex)

        task_expiry = asyncio.create_task(_expire_unpaid_loop())
        bg_tasks.append(task_expiry)

    # 10.1 Tự động sao lưu CSDL định kỳ (chỉ cho SQLite)
    backup_interval_h = getattr(settings, "BACKUP_INTERVAL_HOURS", 0)
    backup_keep = getattr(settings, "BACKUP_KEEP", 7)
    db_url = getattr(settings, "DATABASE_URL", "")
    if backup_interval_h > 0:
        if "postgres" in db_url.lower():
            logger.info("Cơ sở dữ liệu là PostgreSQL. Tác vụ sao lưu SQLite định kỳ được bỏ qua.")
        else:
            async def _backup_loop():
                global _last_backup_time
                while True:
                    try:
                        await asyncio.sleep(backup_interval_h * 3600)
                        from scripts.backup import run_backup
                        await asyncio.to_thread(
                            run_backup,
                            db_path=db_service.db_path,
                            keep_count=backup_keep,
                        )
                        _last_backup_time = dt.datetime.now().isoformat(timespec="seconds")
                        logger.info("Sao lưu CSDL tự động hoàn tất thành công lúc %s", _last_backup_time)
                    except asyncio.CancelledError:
                        break
                    except Exception as ex:
                        logger.warning("Lỗi trong tác vụ nền auto-backup: %s", ex)

            task_backup = asyncio.create_task(_backup_loop())
            bg_tasks.append(task_backup)

    try:
        yield
    finally:
        for t in bg_tasks:
            t.cancel()
        for t in bg_tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass


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


# CSRF Protection Middleware: Bảo vệ các API ghi dữ liệu dùng cookie aura_session
CSRF_EXEMPT_PREFIXES = (
    "/api/payment/webhook",
    "/api/payment/vnpay/ipn",
)


def _is_csrf_exempt(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PREFIXES):
        return True
    if path.startswith("/api/payment/") and (path.endswith("/return") or path.endswith("/ipn") or path.endswith("/webhook")):
        return True
    return False


@app.middleware("http")
async def csrf_protect_middleware(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not _is_csrf_exempt(request.url.path):
            session_cookie = request.cookies.get("aura_session")
            auth_header = request.headers.get("Authorization", "").strip()
            is_bearer = auth_header.lower().startswith("bearer ")

            # Chỉ áp dụng kiểm tra Origin/Referer khi request dùng cookie xác thực và không dùng Bearer header
            if session_cookie and not is_bearer:
                sec_fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
                if sec_fetch_site == "cross-site":
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF verification failed: cross-site request forbidden."},
                    )

                origin = request.headers.get("origin")
                referer = request.headers.get("referer")
                source_url = origin or referer
                if source_url:
                    try:
                        source_host = urlparse(source_url).hostname or ""
                    except Exception:
                        source_host = ""

                    source_host = source_host.lower()

                    # Danh sách host hợp lệ
                    valid_hosts = set()
                    if request.url.hostname:
                        valid_hosts.add(request.url.hostname.lower())
                    host_hdr = request.headers.get("host")
                    if host_hdr:
                        valid_hosts.add(host_hdr.split(":")[0].lower())
                    if settings.PUBLIC_BASE_URL:
                        try:
                            pub_h = urlparse(settings.PUBLIC_BASE_URL).hostname
                            if pub_h:
                                valid_hosts.add(pub_h.lower())
                        except Exception:
                            pass

                    if not source_host or source_host not in valid_hosts:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "CSRF verification failed: cross-origin request forbidden."},
                        )

    return await call_next(request)


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
    if settings.APP_ENV.strip().lower() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Endpoint kiểm tra sức khỏe hệ thống & CSDL (/healthz)
@app.get("/healthz", tags=["System"])
@app.get("/health", tags=["System"])
def health_check():
    """Kiểm tra tính sẵn sàng của ứng dụng và kết nối cơ sở dữ liệu (Health Check qua SQLAlchemy)."""
    db_ok = False
    error_msg = None
    try:
        from sqlalchemy import text

        from app.db.session import engine

        if str(engine.url).startswith("sqlite"):
            import sqlite3
            conn_sq = sqlite3.connect(db_service.db_path, timeout=5.0)
            conn_sq.close()

        with engine.connect() as conn:
            row = conn.execute(text("SELECT 1;")).fetchone()
            if row and row[0] == 1:
                db_ok = True
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

    pending_count = 0
    oldest_age_minutes = None
    try:
        from app.db.models import OrderDB
        from app.db.session import get_db_session
        from app.services.order_service import parse_order_created_at

        with get_db_session() as session:
            pending_orders = (
                session.query(OrderDB.created_at)
                .filter(OrderDB.order_status == "pending_payment")
                .all()
            )
            pending_count = len(pending_orders)
            if pending_orders:
                now_dt = dt.datetime.now()
                oldest_dt = None
                for (cat_str,) in pending_orders:
                    p_dt = parse_order_created_at(cat_str)
                    if p_dt:
                        if oldest_dt is None or p_dt < oldest_dt:
                            oldest_dt = p_dt
                if oldest_dt:
                    oldest_age_minutes = round((now_dt - oldest_dt).total_seconds() / 60.0, 1)
    except Exception as _e:
        logger.warning("Không thể lấy thống kê đơn chờ thanh toán cho /healthz: %s", _e)

    return {
        "status": "ok",
        "database": "connected",
        "app_env": settings.APP_ENV,
        "version": settings.VERSION,
        "pending_payment_count": pending_count,
        "oldest_pending_payment_age_minutes": oldest_age_minutes,
        "last_backup_at": _last_backup_time,
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
    status_code = 400 if (field == "payment_method" or "payment_method" in detail) else 422
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "message": detail, "error": detail, "detail": detail}
    )


@app.exception_handler(OrderError)
async def order_error_handler(_: Request, exc: OrderError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.message, "error": exc.message, "detail": exc.message}
    )


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

