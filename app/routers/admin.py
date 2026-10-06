import math
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse

from app.db.database import db_service
from app.models.schemas import (
    AdminOrderStatusUpdate,
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
def admin_stats(_admin: User = Depends(require_admin)):
    stats = order_service.get_admin_stats()
    all_products = product_service.get_all()
    stats["total_products"] = len(all_products)
    stats["products_in_stock"] = sum(1 for p in all_products if p.stock > 10)
    stats["products_low_stock"] = sum(1 for p in all_products if 0 < p.stock <= 10)
    stats["products_out_of_stock"] = sum(1 for p in all_products if p.stock == 0)
    stats["total_users"] = len(user_service.get_all_users())
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
    _admin: User = Depends(require_admin),
):
    data = body.model_dump(exclude_unset=True)
    updated = product_service.update_product(product_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để cập nhật")
    return updated


@router.delete("/api/admin/products/{product_id}")
def admin_delete_product(product_id: str, _admin: User = Depends(require_admin)):
    ok = product_service.delete_product(product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm để xóa")
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


@router.get("/api/admin/reports/profit", response_model=ProfitReportResponse)
def admin_profit_report(_admin: User = Depends(require_admin)):
    return db_service.get_profit_loss_report()


@router.get("/api/admin/orders")
def admin_get_orders(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
):
    return order_service.get_orders(status=status, search=search, limit=limit, offset=offset)


@router.put("/api/admin/orders/{order_id}/status")
def admin_update_order_status(
    order_id: str,
    body: AdminOrderStatusUpdate,
    _admin: User = Depends(require_admin),
):
    valid_statuses = ["pending_payment", "confirmed", "shipping", "completed", "cancelled"]
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Trạng thái không hợp lệ. Phải thuộc: {', '.join(valid_statuses)}",
        )
    updated = order_service.update_order_status(order_id, body.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")
    return updated


# ==========================================
# Quản lý User (Role, Status, Self-protection)
# ==========================================
@router.get("/api/admin/users")
def admin_get_users(_admin: User = Depends(require_admin)):
    return user_service.get_all_users()


@router.put("/api/admin/users/{user_id}/role")
def admin_update_user_role(
    user_id: str,
    body: AdminUserRoleUpdate,
    current_admin: User = Depends(require_admin),
):
    try:
        updated = user_service.update_role(user_id, body.role, current_admin_id=str(current_admin.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return updated


@router.put("/api/admin/users/{user_id}/status")
def admin_update_user_status(
    user_id: str,
    body: AdminUserStatusUpdate,
    current_admin: User = Depends(require_admin),
):
    is_active = body.is_active
    if is_active is None and body.status is not None:
        is_active = body.status == "active"
    if is_active is None:
        raise HTTPException(status_code=400, detail="Thiếu trường is_active hoặc status")

    try:
        updated = user_service.toggle_status(user_id, bool(is_active), current_admin_id=str(current_admin.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return updated


# ==========================================
# Xu hướng Trend (Admin)
# ==========================================
@router.get("/api/admin/trending")
def admin_get_trending(_admin: User = Depends(require_admin)):
    return trend_service.get_trends()


@router.post("/api/admin/trending/refresh")
def admin_refresh_trending(_admin: User = Depends(require_admin)):
    return trend_service.refresh_trends(force=True)
