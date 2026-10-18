from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.db.database import db_service
from app.routers.deps import (
    base_context,
    get_current_user_optional,
    mask_address,
    mask_phone,
    normalize_phone,
    safe_redirect,
    templates,
    verify_order_access,
)
from app.services.order_service import order_service
from app.services.product_service import product_service
from app.services.size_chart_service import size_chart_service

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def home_page(request: Request):
    user = get_current_user_optional(request)
    hot_products = product_service.get_all(sort="popular")[:8]
    new_products = product_service.get_all(sort="newest")[:8]
    ctx = base_context(
        request,
        user=user,
        hot_products=hot_products,
        new_products=new_products,
    )
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/products", response_class=HTMLResponse)
def products_page(
    request: Request,
    category: Optional[str] = None,
    gender: Optional[str] = "all",
    sort: Optional[str] = "popular",
    search: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    flash_sale_only: bool = False,
):
    user = get_current_user_optional(request)
    products = product_service.get_all(
        category=category,
        gender=gender,
        min_price=min_price,
        max_price=max_price,
        sort=sort,
        search=search,
        flash_sale_only=flash_sale_only,
    )
    categories = product_service.get_categories()
    ctx = base_context(
        request,
        user=user,
        products=products,
        categories=categories,
        selected_category=category or "all",
        selected_gender=gender or "all",
        selected_sort=sort or "popular",
        search_query=search or "",
    )
    return templates.TemplateResponse(request, "products.html", ctx)


@router.get("/product/{product_id}", response_class=HTMLResponse)
def product_detail_page(request: Request, product_id: str):
    user = get_current_user_optional(request)
    product = product_service.get_by_id(product_id)
    if not product:
        ctx = base_context(
            request,
            user=user,
            message=f"Không tìm thấy sản phẩm với mã '{product_id}'",
        )
        return templates.TemplateResponse(request, "404.html", ctx, status_code=404)

    related_products = product_service.get_related(product_id, limit=4)
    try:
        size_chart = size_chart_service.get_chart_for_product(product_id)
    except Exception:
        size_chart = None

    ctx = base_context(
        request,
        user=user,
        product=product,
        related_products=related_products,
        size_chart=size_chart,
    )
    return templates.TemplateResponse(request, "product-detail.html", ctx)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, redirect: Optional[str] = None):
    user = get_current_user_optional(request)
    default_dest = "/admin" if (user and user.role == "admin") else "/profile"
    safe_target = safe_redirect(redirect, default=default_dest) if redirect else default_dest
    if user and user.is_active:
        return RedirectResponse(url=safe_target, status_code=302)
    ctx = base_context(
        request,
        redirect_url=safe_redirect(redirect, default="") if redirect else "",
    )
    return templates.TemplateResponse(request, "login.html", ctx)


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    user = get_current_user_optional(request)
    if user and user.is_active:
        return RedirectResponse(url="/profile", status_code=302)
    ctx = base_context(request)
    return templates.TemplateResponse(request, "register.html", ctx)


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: Optional[str] = Query(None)):
    user = get_current_user_optional(request)
    ctx = base_context(
        request,
        token=token or "",
        user=user,
    )
    return templates.TemplateResponse(request, "reset-password.html", ctx)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    user = get_current_user_optional(request)
    if not user or not user.is_active:
        return RedirectResponse(url="/login?redirect=/profile", status_code=302)
    ctx = base_context(request, user=user)
    return templates.TemplateResponse(request, "profile.html", ctx)


@router.get("/order-success/{order_id}", response_class=HTMLResponse)
def order_success_page(request: Request, order_id: str, phone: Optional[str] = Query(None)):
    user = get_current_user_optional(request)
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        return RedirectResponse(url="/", status_code=302)

    is_allowed, is_full = verify_order_access(order, user, phone)
    if not is_allowed and not order.get("user_id"):
        cookie_phone = request.cookies.get(f"aura_order_{order_id}")
        order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
        if cookie_phone and normalize_phone(cookie_phone) == normalize_phone(order_phone):
            is_allowed, is_full = True, False

    if not is_allowed:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    display_order = dict(order)
    if not is_full:
        order_phone = display_order.get("customer_phone") or display_order.get("customer", {}).get("phone", "")
        masked_phone_str = mask_phone(order_phone)
        masked_addr_str = mask_address(display_order.get("customer_address"), display_order.get("province"))
        display_order["customer_phone"] = masked_phone_str
        display_order["customer_address"] = masked_addr_str
        if "customer" in display_order and isinstance(display_order["customer"], dict):
            display_order["customer"] = dict(display_order["customer"])
            display_order["customer"]["phone"] = masked_phone_str
            display_order["customer"]["address"] = masked_addr_str

    ctx = base_context(
        request,
        order=display_order,
        user=user,
    )
    return templates.TemplateResponse(request, "order-success.html", ctx)


@router.get("/tracking", response_class=HTMLResponse)
def tracking_page(
    request: Request,
    code: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
):
    """
    Trang theo dõi đơn hàng công khai.
    Yêu cầu khớp cả mã đơn và số điện thoại để xem chi tiết.
    """
    user = get_current_user_optional(request)

    # Hiển thị form tìm kiếm nếu chưa nhập code
    if not code:
        ctx = base_context(request, user=user, order=None, error=None)
        return templates.TemplateResponse(request, "tracking.html", ctx)

    order = db_service.get_order_by_tracking_or_id(code.strip())
    if not order:
        mem = order_service.get_order_by_id(code.strip())
        if mem:
            order = db_service._format_order_row(dict(mem))

    if not order:
        ctx = base_context(request, user=user, order=None,
                           error="Không tìm thấy đơn hàng. Vui lòng kiểm tra lại mã đơn.")
        return templates.TemplateResponse(request, "tracking.html", ctx)

    is_allowed, is_full = verify_order_access(order, user, phone)
    if not is_allowed:
        ctx = base_context(request, user=user, order=None,
                           error="Số điện thoại không khớp với đơn hàng. Vui lòng nhập đúng SĐT đặt hàng.")
        return templates.TemplateResponse(request, "tracking.html", ctx)

    display_order = dict(order)
    if not is_full:
        raw_phone = display_order.get("customer_phone") or display_order.get("customer", {}).get("phone", "")
        raw_addr = display_order.get("customer_address") or display_order.get("customer", {}).get("address", "")
        masked_phone_str = mask_phone(raw_phone)
        masked_addr_str = mask_address(raw_addr, display_order.get("province"))
        display_order["customer_phone"] = masked_phone_str
        display_order["customer_address"] = masked_addr_str
        if "customer" in display_order and isinstance(display_order["customer"], dict):
            display_order["customer"] = dict(display_order["customer"])
            display_order["customer"]["phone"] = masked_phone_str
            display_order["customer"]["address"] = masked_addr_str

    timeline = db_service.get_tracking_timeline(display_order)

    # Cho phép hủy nếu đơn chưa shipping
    can_cancel = display_order.get("order_status") not in ("shipping", "completed", "cancelled")

    ctx = base_context(
        request,
        user=user,
        order=display_order,
        timeline=timeline,
        can_cancel=can_cancel,
        error=None,
        searched_code=code,
        searched_phone=phone or "",
    )
    return templates.TemplateResponse(request, "tracking.html", ctx)


@router.get("/403", response_class=HTMLResponse)
def forbidden_page(request: Request):
    user = get_current_user_optional(request)
    ctx = base_context(request, user=user)
    return templates.TemplateResponse(request, "403.html", ctx, status_code=403)


_status_cache = {"at": 0.0, "ollama": False}


def _ollama_available() -> bool:
    import time
    import urllib.request
    if time.time() - _status_cache["at"] < 30:
        return _status_cache["ollama"]
    try:
        with urllib.request.urlopen(f"{settings.OLLAMA_HOST}/api/tags", timeout=1.5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    _status_cache.update(at=time.time(), ollama=ok)
    return ok


@router.get("/api/system/status")
def system_status(request: Request):
    user = get_current_user_optional(request)
    if user and user.role == "admin":
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
    return {
        "status": "online",
        "app_name": settings.APP_NAME,
        "version": settings.VERSION,
    }
