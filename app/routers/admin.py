import codecs
import csv
import datetime as dt
import io
import math
import time
from typing import Any, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse

from app.db.database import db_service
from app.models.schemas import (
    AdminOrderNoteUpdate,
    AdminOrderStatusUpdate,
    AdminProcessRefundRequest,
    AdminProductPayload,
    AdminUserRoleUpdate,
    AdminUserStatusUpdate,
    InventoryBatchCreate,
    InventoryBatchItem,
    ProfitReportResponse,
    User,
)
from app.routers.deps import (
    base_context,
    get_client_ip,
    require_admin,
    require_admin_page,
    templates,
)
from app.services.image_service import image_service
from app.services.order_service import order_service
from app.services.product_import_service import product_import_service
from app.services.product_service import product_service
from app.services.trend_service import trend_service
from app.services.user_service import user_service
from app.services.vnpay_service import vnpay_service

router = APIRouter(tags=["admin"])


# ==========================================
# 6 Trang HTML Quản trị (Dùng require_admin_page dependency)
# ==========================================
@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard_page(request: Request, admin: User = Depends(require_admin_page)):
    ctx = base_context(request, user=admin, current_page="dashboard")
    return templates.TemplateResponse(request, "admin/dashboard.html", ctx)


@router.get("/admin/products", response_class=HTMLResponse)
def admin_products_page(request: Request, admin: User = Depends(require_admin_page)):
    ctx = base_context(request, user=admin, current_page="products")
    return templates.TemplateResponse(request, "admin/products.html", ctx)


@router.get("/admin/orders", response_class=HTMLResponse)
def admin_orders_page(request: Request, admin: User = Depends(require_admin_page)):
    ctx = base_context(request, user=admin, current_page="orders")
    return templates.TemplateResponse(request, "admin/orders.html", ctx)


@router.get("/admin/orders/{order_id}/print", response_class=HTMLResponse)
def admin_order_print_page(order_id: str, request: Request, admin: User = Depends(require_admin_page)):
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{order_id}' để in phiếu")

    # Đảm bảo KHÔNG che SĐT, KHÔNG che địa chỉ cho admin
    cust_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    cust_name = order.get("customer_name") or order.get("customer", {}).get("name", "Khách hàng")
    cust_addr = order.get("customer_address") or order.get("customer", {}).get("address", "")
    cust_note = order.get("customer_note") or order.get("customer", {}).get("note", "")

    # Nổi bật tiền COD
    is_cod = (order.get("payment_method") == "cod") and (order.get("payment_status") != "paid")
    total_amount = order.get("total_amount") or order.get("quote", {}).get("total", 0)
    cod_amount = total_amount if is_cod else 0

    quote = order.get("quote", {})
    items = quote.get("lines") or order.get("items") or []

    ctx = base_context(
        request,
        user=admin,
        order=order,
        order_id=order.get("order_id") or order_id,
        customer_phone=cust_phone,
        customer_name=cust_name,
        customer_address=cust_addr,
        customer_note=cust_note,
        is_cod=is_cod,
        cod_amount=cod_amount,
        items=items,
        total_amount=total_amount,
        subtotal=order.get("subtotal") or quote.get("subtotal", 0),
        shipping_fee=order.get("shipping_fee") or quote.get("shipping_fee", 0),
        discount_amount=order.get("discount_amount") or quote.get("discount_amount", 0),
        created_at=order.get("created_at") or "",
        payment_method=order.get("payment_method"),
        payment_status=order.get("payment_status"),
        carrier=order.get("carrier") or "Giao Hàng Nhanh (GHN Express)",
        tracking_code=order.get("tracking_code") or "",
        internal_note=order.get("internal_note") or "",
    )
    return templates.TemplateResponse(request, "admin/order_print.html", ctx)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request, admin: User = Depends(require_admin_page)):
    ctx = base_context(request, user=admin, current_page="users")
    return templates.TemplateResponse(request, "admin/users.html", ctx)


@router.get("/admin/trending", response_class=HTMLResponse)
def admin_trending_page(request: Request, admin: User = Depends(require_admin_page)):
    ctx = base_context(request, user=admin, current_page="trending")
    return templates.TemplateResponse(request, "admin/trending.html", ctx)


@router.get("/admin/inventory", response_class=HTMLResponse)
@router.get("/admin/profit", response_class=HTMLResponse)
@router.get("/admin/reports/profit", response_class=HTMLResponse)
def admin_inventory_and_profit_page(request: Request, admin: User = Depends(require_admin_page)):
    ctx = base_context(request, user=admin, current_page="inventory")
    return templates.TemplateResponse(request, "admin/profit_report.html", ctx)


# ==========================================
# API Quản trị Thống kê & Sản phẩm
# ==========================================
@router.get("/api/admin/stats")
def admin_stats(response: Response, _admin: User = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    stats = order_service.get_admin_stats()
    all_products = product_service.get_all()
    stats["total_products"] = len(all_products)
    stats["products_in_stock"] = sum(1 for p in all_products if p.stock > 10)
    stats["products_low_stock"] = sum(1 for p in all_products if 0 < p.stock <= 10)
    stats["products_out_of_stock"] = sum(1 for p in all_products if p.stock == 0)

    # Thống kê chi tiết biến thể tồn kho
    low_vars = db_service.get_low_stock_variants(threshold=5)
    stats["variants_low_stock"] = len(low_vars)
    stats["variants_out_of_stock"] = sum(1 for v in low_vars if v["stock"] == 0)

    all_users = user_service.get_all_users()
    stats["total_users"] = len(all_users)

    # Đếm tài khoản mới đăng ký hôm nay
    import datetime as _dt
    today_str = _dt.datetime.now().strftime("%d/%m/%Y")
    stats["new_users_today"] = sum(
        1 for u in all_users
        if getattr(u, "created_at", "") and str(u.created_at).startswith(today_str)
    )

    # Đơn hàng gần đây (10 đơn mới nhất)
    recent = order_service.get_orders(limit=10)
    stats["recent_orders"] = recent

    return stats


@router.get("/api/admin/products")
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
    items = all_items[offset : offset + limit]
    return {
        "items": [item.model_dump() for item in items],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
    }


@router.post("/api/admin/products")
def admin_create_product(body: AdminProductPayload, _admin: User = Depends(require_admin)):
    created = product_service.create_product(body.model_dump(exclude_unset=True))
    return created


@router.put("/api/admin/products/{product_id}")
def admin_update_product(
    product_id: str,
    body: AdminProductPayload,
    request: Request,
    _admin: User = Depends(require_admin),
):
    old_prod = product_service.get_by_id(product_id, include_inactive=True)
    old_price = old_prod.price if old_prod else None
    data = body.model_dump(exclude_unset=True)
    updated = product_service.update_product(product_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để cập nhật")

    # Ghi nhật ký kiểm toán (audit log) nếu có đổi giá
    new_price = getattr(updated, "price", None)
    if old_price is not None and new_price is not None and old_price != new_price:
        db_service.log_audit(
            admin_id=str(_admin.id),
            action="change_price",
            target_type="product",
            target_id=product_id,
            before_state={"price": old_price},
            after_state={"price": new_price},
            ip_address=get_client_ip(request),
        )
    return updated


@router.delete("/api/admin/products/{product_id}")
def admin_delete_product(
    product_id: str,
    request: Request,
    _admin: User = Depends(require_admin),
):
    old_prod = product_service.get_by_id(product_id, include_inactive=True)
    ok = product_service.delete_product(product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để xóa")

    # Ghi nhật ký kiểm toán (audit log) xóa sản phẩm
    db_service.log_audit(
        admin_id=str(_admin.id),
        action="delete_product",
        target_type="product",
        target_id=product_id,
        before_state={"is_active": getattr(old_prod, "is_active", True) if old_prod else True},
        after_state={"is_active": False, "deleted": True},
        ip_address=get_client_ip(request),
    )
    return {"success": True, "message": f"Đã xóa sản phẩm {product_id} thành công"}


# ==========================================
# Import & Upload Ảnh (Giới hạn kích thước, magic bytes, batch write)
# ==========================================
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


async def _read_with_limit(file: UploadFile, max_bytes: int) -> bytes:
    content = bytearray()
    chunk_size = 64 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"Dung lượng file vượt quá giới hạn tối đa {max_bytes // (1024 * 1024)}MB",
            )
    return bytes(content)


@router.post("/api/admin/products/import")
async def admin_import_products(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
):
    filename = (file.filename or "").strip()
    lower_name = filename.lower()

    if lower_name.endswith(".xls"):
        raise HTTPException(
            status_code=400,
            detail="Định dạng Excel cũ (.xls) không được hỗ trợ. Vui lòng chuyển đổi sang .xlsx hoặc .csv.",
        )

    if not (lower_name.endswith(".csv") or lower_name.endswith(".xlsx")):
        raise HTTPException(
            status_code=400,
            detail="Định dạng file không hỗ trợ. Vui lòng tải lên file .csv hoặc .xlsx",
        )

    content = await _read_with_limit(file, MAX_FILE_SIZE)
    if not content:
        raise HTTPException(status_code=400, detail="File rỗng, vui lòng kiểm tra lại nội dung")

    try:
        report = product_import_service.import_products(content, filename)
        return report
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi xử lý file import: {str(e)}") from e


def _validate_image_magic_bytes(content: bytes) -> bool:
    if len(content) < 12:
        return False
    # JPEG magic bytes
    if content.startswith(b"\xff\xd8\xff"):
        return True
    # PNG magic bytes
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    # WEBP magic bytes (RIFF .... WEBP)
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return True
    return False


@router.post("/api/admin/upload-image")
async def admin_upload_image(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
):
    filename = (file.filename or "").strip()
    content_type = (file.content_type or "").lower().strip()
    lower_name = filename.lower()

    valid_exts = (".jpg", ".jpeg", ".png", ".webp")
    valid_mimes = ("image/jpeg", "image/png", "image/webp")

    is_valid_ext = any(lower_name.endswith(ext) for ext in valid_exts)
    is_valid_mime = any(content_type == m or content_type.startswith(f"{m};") for m in valid_mimes)

    # Điều kiện đuôi file và MIME phải là AND
    if not (is_valid_ext and is_valid_mime):
        raise HTTPException(
            status_code=400,
            detail="Chỉ chấp nhận file ảnh định dạng JPG, PNG hoặc WEBP (đuôi file và MIME type phải khớp)",
        )

    content = await _read_with_limit(file, MAX_FILE_SIZE)
    if not content:
        raise HTTPException(status_code=400, detail="File ảnh rỗng")

    # Kiểm tra magic bytes
    if not _validate_image_magic_bytes(content):
        raise HTTPException(
            status_code=400,
            detail="Nội dung file không phải ảnh hợp lệ (sai magic bytes định dạng JPEG/PNG/WEBP)",
        )

    try:
        result = image_service.upload_image(content, folder="aura_store")
        return {
            "url": result["url"],
            "public_id": result["public_id"],
            "format": result.get("format"),
            "bytes": result.get("bytes"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể tải ảnh lên Cloudinary: {str(e)}") from e


@router.delete("/api/admin/images/{public_id:path}")
def admin_delete_image(
    public_id: str,
    _admin: User = Depends(require_admin),
):
    # Chỉ cho xóa ảnh thuộc thư mục aura_store/
    clean_id = public_id.strip()
    if not (clean_id.startswith("aura_store/") or clean_id == "aura_store"):
        raise HTTPException(
            status_code=403,
            detail="Chỉ được phép xóa các ảnh thuộc thư mục 'aura_store/' của cửa hàng",
        )

    ok = image_service.delete_image(clean_id)
    return {"success": ok}


# ==========================================
# Lô hàng, Báo cáo lãi/lỗ & Đơn hàng
# ==========================================
@router.post("/api/admin/products/{product_id}/inventory", response_model=InventoryBatchItem)
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
            color=body.color,
            size=body.size,
            note=body.note,
            created_by=_admin.username,
        )
        return batch
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/admin/products/{product_id}/inventory", response_model=List[InventoryBatchItem])
def admin_get_inventory_batches(
    product_id: str,
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
):
    prod = product_service.get_by_id(product_id)
    if not prod:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy sản phẩm với mã '{product_id}'")
    return db_service.get_inventory_batches(product_id=product_id, limit=limit)


@router.get("/api/admin/variants/low-stock")
def admin_low_stock_variants(
    threshold: int = Query(5, ge=0, le=50),
    _admin: User = Depends(require_admin),
):
    return db_service.get_low_stock_variants(threshold=threshold)


@router.get("/api/admin/reports/profit", response_model=ProfitReportResponse)
def admin_profit_report(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    _admin: User = Depends(require_admin),
):
    f_date = from_date or start_date
    t_date = to_date or end_date
    return db_service.get_profit_loss_report(from_date=f_date, to_date=t_date)


@router.get("/api/admin/orders/export")
def admin_export_orders(
    status: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    _admin: User = Depends(require_admin),
):
    """
    Xuất danh sách đơn hàng sang định dạng CSV (UTF-8 BOM),
    hỗ trợ lọc theo trạng thái và khoảng ngày.
    Chống triệt để lỗ hổng CSV/Formula Injection bằng cách thêm dấu ' trước ô bắt đầu bằng =, +, -, @.
    """
    f_date = from_date or start_date
    t_date = to_date or end_date
    orders = order_service.get_orders(
        status=status,
        from_date=f_date,
        to_date=t_date,
        limit=10000,
        offset=0,
    )

    def sanitize_csv_cell(val: Any) -> str:
        if val is None:
            return ""
        s = str(val)
        stripped = s.lstrip()
        if stripped and stripped[0] in ("=", "+", "-", "@"):
            return "'" + s
        return s

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    headers = [
        "Mã đơn hàng",
        "Thời gian đặt",
        "Khách hàng",
        "Số điện thoại",
        "Email",
        "Địa chỉ",
        "Tỉnh/Thành",
        "Phương thức thanh toán",
        "Trạng thái thanh toán",
        "Trạng thái đơn hàng",
        "Tiền thu hộ COD",
        "Tổng tiền",
        "Tiền hàng",
        "Phí vận chuyển",
        "Giảm giá",
        "Ghi chú khách hàng",
        "Ghi chú nội bộ",
    ]
    writer.writerow([sanitize_csv_cell(h) for h in headers])

    for o in orders:
        is_cod = (o.get("payment_method") == "cod") and (o.get("payment_status") != "paid")
        total_amt = o.get("total_amount", 0)
        cod_amt = total_amt if is_cod else 0

        cust_phone = o.get("customer_phone") or o.get("customer", {}).get("phone") or ""
        cust_name = o.get("customer_name") or o.get("customer", {}).get("name") or ""
        cust_email = o.get("customer_email") or o.get("customer", {}).get("email") or ""
        cust_addr = o.get("customer_address") or o.get("customer", {}).get("address") or ""
        cust_province = o.get("province") or o.get("customer", {}).get("province") or ""
        cust_note = o.get("customer_note") or o.get("customer", {}).get("note") or ""

        row = [
            sanitize_csv_cell(o.get("order_id")),
            sanitize_csv_cell(o.get("created_at")),
            sanitize_csv_cell(cust_name),
            sanitize_csv_cell(cust_phone),
            sanitize_csv_cell(cust_email),
            sanitize_csv_cell(cust_addr),
            sanitize_csv_cell(cust_province),
            sanitize_csv_cell("COD" if o.get("payment_method") == "cod" else ("VNPay" if o.get("payment_method") == "vnpay" else "Chuyển khoản QR")),
            sanitize_csv_cell(o.get("payment_status")),
            sanitize_csv_cell(o.get("status") or o.get("order_status")),
            sanitize_csv_cell(cod_amt),
            sanitize_csv_cell(total_amt),
            sanitize_csv_cell(o.get("subtotal")),
            sanitize_csv_cell(o.get("shipping_fee")),
            sanitize_csv_cell(o.get("discount_amount")),
            sanitize_csv_cell(cust_note),
            sanitize_csv_cell(o.get("internal_note")),
        ]
        writer.writerow(row)

    csv_bytes = codecs.BOM_UTF8 + output.getvalue().encode("utf-8")
    filename = f"orders_export_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


@router.get("/api/admin/orders")
def admin_get_orders(
    response: Response,
    status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    f_date = from_date or start_date
    t_date = to_date or end_date
    return order_service.get_orders(
        status=status,
        payment_status=payment_status,
        search=search,
        limit=limit,
        offset=offset,
        from_date=f_date,
        to_date=t_date,
    )


@router.get("/api/admin/orders/refund-pending")
def admin_get_refund_pending_orders(_admin: User = Depends(require_admin)):
    """Lấy danh sách các đơn hàng cần hoàn tiền (payment_status = 'refund_pending') cho admin/kế toán."""
    return db_service.get_refund_pending_orders()


@router.post("/api/admin/orders/{order_id}/process-refund")
def admin_process_order_refund(
    order_id: str,
    request: Request,
    body: Optional[AdminProcessRefundRequest] = None,
    _admin: User = Depends(require_admin),
):
    """
    Xử lý hoàn tiền cho đơn hàng:
    - Với Chuyển khoản ngân hàng / VietQR / COD hoặc khi is_manual=True: Xác nhận admin đã chuyển khoản thủ công.
    - Chống gửi lặp / hoàn tiền 2 lần (idempotency & concurrency guard).
    - Không cho hoàn tiền cho đơn chưa thanh toán; không cho hoàn quá số tiền đã thanh toán.
    """
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    payment_status = order.get("payment_status")
    if payment_status == "refunded":
        raise HTTPException(
            status_code=409,
            detail="Đơn hàng này đã được hoàn tiền thành công trước đó (chống hoàn tiền hai lần)",
        )
    if payment_status not in ("refund_pending", "refund_failed", "paid"):
        raise HTTPException(
            status_code=400,
            detail=f"Không thể hoàn tiền cho đơn hàng chưa thanh toán (trạng thái hiện tại: '{payment_status}')",
        )

    # Lấy thông tin giao dịch thanh toán gốc
    tx = db_service.get_order_payment_transaction(order_id)
    rf_req = db_service.get_refund_request(order_id)
    orig_trans_code = (tx.get("transaction_code") if tx else None) or (rf_req.get("original_trans_code") if rf_req else None)

    order_total = int(order.get("total_amount") or 0)
    paid_amount = int((tx.get("amount") if tx else None) or (rf_req.get("amount") if rf_req else None) or order_total)

    if paid_amount <= 0:
        raise HTTPException(status_code=400, detail="Đơn hàng chưa có giao dịch thanh toán hợp lệ (số tiền thanh toán bằng 0)")

    amount = body.amount if (body and body.amount is not None) else paid_amount
    if amount is None or amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền hoàn không hợp lệ (nhỏ hơn hoặc bằng 0)")
    if amount > paid_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Số tiền yêu cầu hoàn ({amount:,.0f}đ) vượt quá số tiền khách đã thanh toán ({paid_amount:,.0f}đ). Không thể hoàn quá số tiền đã thanh toán.",
        )

    payment_channel = (tx.get("payment_channel") if tx else None) or (rf_req.get("payment_channel") if rf_req else None) or order.get("payment_method") or "vietqr"
    payment_channel = payment_channel.lower()
    is_manual = bool(body and body.is_manual)

    refund_code = None
    refund_res = {}

    if is_manual or payment_channel in ("vietqr", "qr_transfer", "bank_transfer", "cod", "cash"):
        # Kênh thủ công (VietQR, Chuyển khoản ngân hàng, COD tiền mặt): Xác nhận admin đã chuyển tiền thủ công cho khách
        refund_code = (body.refund_trans_id if body and body.refund_trans_id else None) or f"MANUAL-REF-{int(time.time())}"
        note_msg = (body.note if body and body.note else None) or "Admin đã xác nhận chuyển khoản hoàn tiền thủ công"
        bank_info_str = ""
        if order.get("refund_bank_name"):
            bank_info_str = f" tới tài khoản {order.get('refund_bank_name')} - {order.get('refund_account_number')} ({order.get('refund_account_holder')})"

        refund_res = {
            "success": True,
            "refund_trans_id": refund_code,
            "mode": "manual",
            "message": f"Xác nhận Admin ({_admin.username}) đã hoàn tiền thủ công{bank_info_str}",
            "refunded_by": _admin.username,
            "refund_bank_name": order.get("refund_bank_name"),
            "refund_account_number": order.get("refund_account_number"),
            "refund_account_holder": order.get("refund_account_holder"),
            "note": note_msg,
        }

    elif payment_channel == "vnpay":
        trans_no = orig_trans_code
        if not trans_no:
            raise HTTPException(
                status_code=400,
                detail="Không thể hoàn tiền qua cổng VNPay: Đơn hàng thiếu mã giao dịch thanh toán gốc từ cổng VNPay. Vui lòng kiểm tra lại giao dịch hoặc chuyển sang hoàn tiền thủ công.",
            )
        if not vnpay_service.is_configured():
            raise HTTPException(
                status_code=400,
                detail="Cổng thanh toán VNPay chưa được cấu hình đầy đủ (thiếu TMN Code hoặc Hash Secret). Vui lòng cấu hình cổng hoặc chọn hoàn tiền thủ công.",
            )

        trans_date = None
        if tx and tx.get("created_at"):
            try:
                dt_obj = dt.datetime.fromisoformat(tx["created_at"])
                trans_date = dt_obj.strftime("%Y%m%d%H%M%S")
            except Exception:
                trans_date = None

        refund_res = vnpay_service.refund(
            order_id=order_id,
            trans_no=trans_no,
            amount=amount,
            trans_date=trans_date,
            create_by=_admin.username,
            client_ip=get_client_ip(request),
            order_desc=f"Hoan tien don hang {order_id}",
        )
        if not refund_res.get("success"):
            err_msg = refund_res.get("message") or "Cổng VNPay từ chối xử lý hoàn tiền"
            db_service.mark_order_refund_failed(order_id, err_msg, processed_by=_admin.username)
            raise HTTPException(status_code=400, detail=f"Lỗi hoàn tiền VNPay: {err_msg}")
        refund_code = refund_res.get("refund_trans_id") or f"VNPAY-REF-{order_id}"


    else:
        # Kênh khác: Hoàn tiền thủ công
        refund_code = (body.refund_trans_id if body and body.refund_trans_id else None) or f"MANUAL-REF-{int(time.time())}"
        refund_res = {
            "success": True,
            "refund_trans_id": refund_code,
            "mode": "manual",
            "message": f"Xác nhận hoàn tiền thủ công qua {payment_channel.upper()}",
            "refunded_by": _admin.username,
        }

    # Cập nhật trạng thái payment_status = 'refunded' và lưu vào payment_transactions & refund_requests
    db_service.mark_order_refunded(
        order_id=order_id,
        refund_tx_code=refund_code,
        amount=amount,
        payment_channel=payment_channel,
        payload=refund_res,
        processed_by=_admin.username,
    )

    db_service.log_audit(
        admin_id=str(_admin.id),
        action="refund_order",
        target_type="order",
        target_id=order_id,
        before_state={"payment_status": payment_status},
        after_state={"payment_status": "refunded", "refund_code": refund_code, "channel": payment_channel, "is_manual": is_manual},
        ip_address=get_client_ip(request),
    )

    return {
        "success": True,
        "order_id": order_id,
        "payment_status": "refunded",
        "amount": amount,
        "payment_channel": payment_channel,
        "refund_trans_id": refund_code,
        "is_manual": is_manual,
        "message": f"Đã hoàn tiền {amount:,.0f}đ thành công cho đơn hàng {order_id} ({'Thủ công' if (is_manual or payment_channel in ('vietqr', 'cod', 'bank_transfer')) else payment_channel.upper()})",
    }


@router.post("/api/admin/orders/expire-unpaid")
def admin_expire_unpaid_orders(_admin: User = Depends(require_admin)):
    """Tự động hủy các đơn online quá hạn chưa thanh toán (chỉ admin)."""
    expired = order_service.expire_unpaid_orders()
    return {
        "success": True,
        "expired_count": len(expired),
        "order_ids": expired,
    }


@router.get("/api/admin/stock/reconcile")
def admin_stock_reconcile(_admin: User = Depends(require_admin)):
    """Đối soát chênh lệch tồn kho giữa CSDL và RAM (chỉ đọc, chỉ admin)."""
    from app.services.stock_reconcile import reconcile_stock
    report = reconcile_stock(apply_fix=False, admin_id=str(_admin.id))
    return {
        "success": True,
        **report,
    }


@router.put("/api/admin/orders/{order_id}/status")
def admin_update_order_status(
    order_id: str,
    body: AdminOrderStatusUpdate,
    request: Request,
    _admin: User = Depends(require_admin),
):
    valid_statuses = ["pending_payment", "confirmed", "shipping", "completed", "cancelled"]
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Trạng thái không hợp lệ. Phải thuộc: {', '.join(valid_statuses)}",
        )
    old_order = order_service.get_order_by_id(order_id)
    old_status = (old_order.get("status") or old_order.get("order_status")) if old_order else None

    updated = order_service.update_order_status(order_id, body.status, internal_note=body.internal_note)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    # Ghi nhật ký kiểm toán (audit log) huỷ đơn hoặc đổi trạng thái
    action = "cancel_order" if body.status == "cancelled" else "update_status"
    db_service.log_audit(
        admin_id=str(_admin.id),
        action=action,
        target_type="order",
        target_id=order_id,
        before_state={"order_status": old_status},
        after_state={"order_status": body.status},
        ip_address=get_client_ip(request),
    )
    return updated


@router.put("/api/admin/orders/{order_id}/internal-note")
@router.put("/api/admin/orders/{order_id}/note")
def admin_update_order_internal_note(
    order_id: str,
    body: AdminOrderNoteUpdate,
    _admin: User = Depends(require_admin),
):
    updated = order_service.update_order_internal_note(order_id, body.internal_note)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")
    return updated


# ==========================================
# Quản lý User (Role, Status, Self-protection)
# ==========================================
@router.get("/api/admin/users")
def admin_get_users(response: Response, _admin: User = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return user_service.get_all_users()


@router.put("/api/admin/users/{user_id}/role")
def admin_update_user_role(
    user_id: str,
    body: AdminUserRoleUpdate,
    request: Request,
    current_admin: User = Depends(require_admin),
):
    old_user = user_service.get_by_id(user_id)
    old_role = old_user.role if old_user else None
    try:
        updated = user_service.update_role(user_id, body.role, current_admin_id=str(current_admin.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

    db_service.log_audit(
        admin_id=str(current_admin.id),
        action="update_role",
        target_type="user",
        target_id=user_id,
        before_state={"role": old_role},
        after_state={"role": updated.role},
        ip_address=get_client_ip(request),
    )
    return updated


@router.put("/api/admin/users/{user_id}/status")
def admin_update_user_status(
    user_id: str,
    body: AdminUserStatusUpdate,
    request: Request,
    current_admin: User = Depends(require_admin),
):
    is_active = body.is_active
    if is_active is None and body.status is not None:
        is_active = body.status == "active"
    if is_active is None:
        raise HTTPException(status_code=400, detail="Thiếu trường is_active hoặc status")

    old_user = user_service.get_by_id(user_id)
    old_status = old_user.status if old_user else None

    try:
        updated = user_service.toggle_status(user_id, bool(is_active), current_admin_id=str(current_admin.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

    action = "lock_user" if updated.status == "disabled" else "update_status"
    db_service.log_audit(
        admin_id=str(current_admin.id),
        action=action,
        target_type="user",
        target_id=user_id,
        before_state={"status": old_status, "is_active": old_status == "active"},
        after_state={"status": updated.status, "is_active": updated.is_active},
        ip_address=get_client_ip(request),
    )
    return updated


@router.get("/api/admin/audit-logs")
def admin_get_audit_logs(
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    admin_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
):
    return db_service.get_audit_logs(
        action=action,
        target_type=target_type,
        admin_id=admin_id,
        limit=limit,
        offset=offset,
    )


# ==========================================
# Xu hướng Trend (Admin)
# ==========================================
@router.get("/api/admin/trending")
def admin_get_trending(_admin: User = Depends(require_admin)):
    return trend_service.get_trends()


@router.post("/api/admin/trending/refresh")
def admin_refresh_trending(_admin: User = Depends(require_admin)):
    return trend_service.refresh_trends(force=True)
