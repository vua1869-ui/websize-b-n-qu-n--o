import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.db.database import db_service
from app.models.schemas import (
    Category,
    FlashSaleResponse,
    Product,
    ProductReviewCreate,
    SizeChartResponse,
    User,
    VideoItem,
    Voucher,
    VoucherCheckRequest,
    VoucherCheckResponse,
)
from app.routers.deps import get_current_user
from app.services.geo_service import geo_service
from app.services.product_service import product_service
from app.services.size_chart_service import size_chart_service

router = APIRouter(tags=["products"])


def _vnd(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


@router.get("/api/products", response_model=List[Product])
def get_products(
    category: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    min_price: Optional[int] = Query(None, ge=0),
    max_price: Optional[int] = Query(None, ge=0),
    sort: str = Query("popular", pattern="^(popular|top_sales|price_asc|price_desc|rating|newest)$"),
    search: Optional[str] = Query(None, max_length=100),
    flash_sale_only: bool = Query(False),
):
    return product_service.get_all(
        category=category,
        gender=gender,
        min_price=min_price,
        max_price=max_price,
        sort=sort,
        search=search,
        flash_sale_only=flash_sale_only,
    )


@router.get("/api/products/{product_id}", response_model=Product)
def get_product_detail(product_id: str):
    product = product_service.get_by_id(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm")
    return product


@router.get("/api/products/{product_id}/related", response_model=List[Product])
def get_related_products(product_id: str, limit: int = Query(4, ge=1, le=12)):
    return product_service.get_related(product_id, limit=limit)


@router.get("/api/categories", response_model=List[Category])
def get_categories():
    return product_service.get_categories()


@router.get("/api/flash-sale", response_model=FlashSaleResponse)
def get_flash_sale():
    items = product_service.get_flash_sale_products()
    now_ms = int(time.time() * 1000)
    if items:
        valid_ends = [
            int(p.flash_sale_end.timestamp() * 1000)
            for p in items
            if p.flash_sale_end
        ]
        ends_ms = min(valid_ends) if valid_ends else now_ms
    else:
        ends_ms = now_ms
    return FlashSaleResponse(ends_at=ends_ms, server_now=now_ms, items=items)


@router.get("/api/vouchers", response_model=List[Voucher])
def get_vouchers():
    return product_service.get_vouchers()


@router.post("/api/vouchers/validate", response_model=VoucherCheckResponse)
def validate_voucher(body: VoucherCheckRequest):
    v = product_service.get_voucher(body.code)
    if not v:
        return VoucherCheckResponse(valid=False, message=f"Mã '{body.code.strip().upper()}' không tồn tại")
    if body.subtotal < v.min_order:
        return VoucherCheckResponse(
            valid=False, message=f"Mã {v.code} áp dụng cho đơn từ {_vnd(v.min_order)}"
        )
    return VoucherCheckResponse(valid=True, message=f"Có thể áp dụng mã {v.code}")


@router.get("/api/videos", response_model=List[VideoItem])
def get_videos():
    return product_service.get_videos()


@router.get("/api/products/{product_id}/variants")
def get_product_variants(product_id: str):
    variants = db_service.get_product_variants(product_id)
    return variants


@router.get("/api/products/{product_id}/reviews")
def get_product_reviews(
    product_id: str,
    rating: Optional[int] = Query(None, ge=1, le=5),
    limit: int = Query(50, ge=1, le=100),
):
    p = product_service.get_by_id(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Sản phẩm không tồn tại")
    return db_service.get_product_reviews(product_id=product_id, rating_filter=rating, limit=limit)


@router.post("/api/products/{product_id}/reviews")
def add_product_review(
    product_id: str,
    body: ProductReviewCreate,
    current_user: User = Depends(get_current_user),
):
    p = product_service.get_by_id(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Sản phẩm không tồn tại")

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

    return {
        "success": True,
        "message": "Cảm ơn bạn đã gửi đánh giá! Nhận xét của bạn giúp cộng đồng chọn size chuẩn hơn.",
        "review": created,
    }


@router.get("/api/products/{product_id}/size-chart", response_model=SizeChartResponse)
def get_product_size_chart(product_id: str):
    p = product_service.get_by_id(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Sản phẩm không tồn tại")
    return size_chart_service.get_chart_for_product(product_id)


# ==========================================
# Địa giới hành chính
# ==========================================
@router.get("/api/locations")
def get_locations():
    return geo_service.get_all_locations()


@router.get("/api/geo/provinces", response_model=List[str], deprecated=True)
def get_provinces(response: Response):
    response.headers["Warning"] = '299 - "Deprecated: use /api/locations instead"'
    return geo_service.get_provinces()


@router.get("/api/geo/districts", response_model=List[str], deprecated=True)
def get_districts(
    response: Response,
    province: Optional[str] = Query(None),
    province_code: Optional[str] = Query(None),
):
    response.headers["Warning"] = '299 - "Deprecated: use /api/locations instead"'
    prov = province or province_code or ""
    code_map = {"01": "Hà Nội", "79": "TP. Hồ Chí Minh", "48": "Đà Nẵng", "31": "Hải Phòng", "92": "Cần Thơ"}
    prov = code_map.get(prov, prov)
    return geo_service.get_districts(prov)


@router.get("/api/geo/wards", response_model=List[str], deprecated=True)
def get_wards(
    response: Response,
    province: Optional[str] = Query(""),
    province_code: Optional[str] = Query(""),
    district: Optional[str] = Query(None),
    district_code: Optional[str] = Query(None),
):
    response.headers["Warning"] = '299 - "Deprecated: use /api/locations instead"'
    prov = province or province_code or ""
    code_map = {"01": "Hà Nội", "79": "TP. Hồ Chí Minh", "48": "Đà Nẵng", "31": "Hải Phòng", "92": "Cần Thơ"}
    prov = code_map.get(prov, prov)
    dist = district or district_code or ""
    dist_map = {"001": "Quận Ba Đình", "002": "Quận Hoàn Kiếm", "004": "Quận Hai Bà Trưng", "005": "Quận Đống Đa"}
    dist = dist_map.get(dist, dist)
    return geo_service.get_wards(prov, dist)
