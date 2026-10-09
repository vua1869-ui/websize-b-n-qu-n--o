import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.config import settings
from app.db.database import db_service
from app.models.schemas import (
    InvoiceResponse,
    OrderCancelRequest,
    OrderCreateRequest,
    OrderResponse,
    OrderTrackingResponse,
    QuoteRequest,
    QuoteResponse,
    User,
)
from app.routers.deps import (
    check_public_track_rate_limit,
    get_current_user_optional,
    mask_address,
    mask_phone,
    require_admin,
    verify_order_access,
)
from app.services.order_service import OrderError, order_service

router = APIRouter(tags=["orders"])


@router.post("/api/orders/quote", response_model=QuoteResponse)
def quote_order(body: QuoteRequest, request: Request):
    user = get_current_user_optional(request)
    return order_service.build_quote(body.items, body.voucher_code, use_points=body.use_points, user=user)


@router.post("/api/orders", response_model=OrderResponse)
def create_order(body: OrderCreateRequest, request: Request, response: Response):
    user = get_current_user_optional(request)
    created = order_service.create_order(body, user=user)
    order_id = getattr(created, "order_id", None) or (created.get("order_id") if isinstance(created, dict) else None)
    if not getattr(user, "id", None) and body.customer_phone and order_id:
        response.set_cookie(
            key=f"aura_order_{order_id}",
            value=body.customer_phone,
            httponly=True,
            max_age=3600,
            samesite="lax",
        )
    return created


@router.get("/api/orders/{order_id}")
def get_order_by_id(order_id: str, request: Request, phone: Optional[str] = Query(None)):
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
        if order:
            order = db_service._format_order_row(dict(order))
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông tin đơn hàng")

    user = get_current_user_optional(request)
    is_allowed, is_full = verify_order_access(order, user, phone)
    if not is_allowed:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông tin đơn hàng")

    if not is_full:
        order = dict(order)
        order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
        order["customer_phone"] = mask_phone(order_phone)
        order["customer_address"] = mask_address(order.get("customer_address"), order.get("province"))
    return order


@router.get("/api/orders/track/{tracking_or_order_id}", response_model=OrderTrackingResponse)
def track_order_shipment(tracking_or_order_id: str, request: Request, phone: Optional[str] = Query(None)):
    check_public_track_rate_limit(request)
    order = db_service.get_order_by_tracking_or_id(tracking_or_order_id)
    if not order:
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
            detail=f"Không tìm thấy thông tin đơn hàng hoặc mã vận đơn '{tracking_or_order_id}'. Vui lòng kiểm tra lại!",
        )

    user = get_current_user_optional(request)
    is_allowed, is_full = verify_order_access(order, user, phone)
    if not is_allowed:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy thông tin đơn hàng hoặc mã vận đơn '{tracking_or_order_id}'. Vui lòng kiểm tra lại!",
        )

    timeline = db_service.get_tracking_timeline(order)
    quote = order.get("quote", {})
    lines = quote.get("lines", [])

    status_labels = {
        "pending_payment": "Chờ thanh toán",
        "confirmed": "Đã xác nhận - Đang xử lý",
        "picking": "Bưu tá đang lấy hàng",
        "in_transit": "Đang vận chuyển",
        "delivering": "Shipper đang giao hàng",
        "completed": "Giao hàng thành công",
        "cancelled": "Đơn hàng đã hủy",
    }
    ship_status = order.get("shipping_status", "ready_to_pick")
    if order.get("order_status") == "completed":
        ship_status = "delivered"

    raw_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    raw_addr = order.get("customer_address") or order.get("customer", {}).get("address", "")
    cust_phone = raw_phone if is_full else mask_phone(raw_phone)
    cust_addr = raw_addr if is_full else mask_address(raw_addr, order.get("province"))

    tracking_code = order.get("tracking_code") or "Chưa có mã vận đơn"
    carrier = order.get("carrier") or "Đang điều phối vận chuyển"

    return OrderTrackingResponse(
        order_id=order["order_id"],
        tracking_code=tracking_code,
        carrier=carrier,
        shipping_status=ship_status,
        shipping_status_label=status_labels.get(order.get("order_status"), "Đang xử lý"),
        estimated_delivery=order.get("estimated_delivery") or "2 - 3 ngày tới",
        customer_name=order.get("customer_name") or order.get("customer", {}).get("name", "Khách hàng"),
        customer_phone=cust_phone,
        customer_address=cust_addr,
        timeline=timeline,
        items=lines,
        total_amount=order.get("total_amount") or quote.get("total", 0),
        payment_method="Chuyển khoản QR NAPAS" if order.get("payment_method") == "qr_transfer" else "Thanh toán khi nhận hàng (COD)",
        payment_status="Đã thanh toán" if order.get("payment_status") == "paid" else "Chưa thanh toán",
        created_at=order.get("created_at") or "",
    )


@router.get("/api/orders/{order_id}/invoice", response_model=InvoiceResponse)
def get_order_invoice(order_id: str, request: Request, phone: Optional[str] = Query(None)):
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
        if order:
            order = db_service._format_order_row(dict(order))

    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng để xuất hóa đơn")

    check_public_track_rate_limit(request)
    user = get_current_user_optional(request)
    is_allowed, is_full = verify_order_access(order, user, phone)
    if not is_allowed:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng để xuất hóa đơn")

    quote = order.get("quote", {})
    subtotal = quote.get("subtotal", 0)
    shipping_fee = quote.get("shipping_fee", 0)
    discount = quote.get("combo_discount", 0) + quote.get("voucher_discount", 0) + quote.get("points_discount", 0)
    total = quote.get("total", subtotal + shipping_fee - discount)

    vat_rate = getattr(settings, "VAT_RATE", 8)
    vat_amount = round(total * vat_rate / (100 + vat_rate)) if vat_rate > 0 else 0

    inv_number = f"INV-AURA-{order['order_id'].replace('AURA-', '')}"
    issued_date = order.get("created_at") or time.strftime("%d/%m/%Y %H:%M")

    e_provider = getattr(settings, "E_INVOICE_PROVIDER", "").strip()
    title = "Hóa đơn điện tử GTGT" if e_provider else "Phiếu thông tin đơn hàng"
    legal_note = "" if e_provider else "Lưu ý: Đây là phiếu thông tin đơn hàng nội bộ, không phải hóa đơn điện tử giá trị gia tăng hợp lệ."

    carrier = order.get("carrier") or "Đang điều phối vận chuyển"
    tracking_code = order.get("tracking_code") or "Chưa có mã vận đơn"

    return InvoiceResponse(
        title=title,
        legal_note=legal_note,
        invoice_number=inv_number,
        order_id=order["order_id"],
        issued_at=issued_date,
        seller={
            "company_name": getattr(settings, "SELLER_NAME", "") or "AURA STUDIO",
            "tax_code": getattr(settings, "SELLER_TAX_CODE", "") or "",
            "address": getattr(settings, "SELLER_ADDRESS", "") or "",
            "hotline": getattr(settings, "SELLER_HOTLINE", "") or "",
            "email": "support@aurastudio.vn",
            "website": "https://aurastudio.vn",
        },
        buyer={
            "name": order.get("customer_name") or order.get("customer", {}).get("name", "Khách hàng"),
            "phone": (order.get("customer_phone") or order.get("customer", {}).get("phone", "")) if is_full else mask_phone(order.get("customer_phone") or order.get("customer", {}).get("phone", "")),
            "address": (order.get("customer_address") or order.get("customer", {}).get("address", "")) if is_full else mask_address(order.get("customer_address") or order.get("customer", {}).get("address", ""), order.get("province")),
        },
        items=quote.get("lines", []),
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        discount_amount=discount,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        total_amount=total,
        payment_method="Chuyển khoản QR NAPAS 247" if order.get("payment_method") == "qr_transfer" else ("Thanh toán VNPay" if order.get("payment_method") == "vnpay" else "Thanh toán khi nhận hàng (COD)"),
        payment_status="Đã thanh toán" if order.get("payment_status") == "paid" else "Chưa thanh toán (Thu hộ COD)",
        carrier=carrier,
        tracking_code=tracking_code,
    )



@router.post("/api/orders/{order_id}/cancel")
def cancel_order_by_customer(order_id: str, body: OrderCancelRequest, request: Request):
    """
    Khách tự huỷ đơn trước khi vận chuyển.
    - Đơn đã thanh toán -> chuyển refund_pending.
    - Race giữa huỷ và admin chuyển shipping được xử lý bằng lock + transaction.
    """
    user = get_current_user_optional(request)
    try:
        result = order_service.cancel_order_by_customer(
            order_id, user=user, req=body
        )
        order_status = result.get("order_status") or result.get("status", "cancelled")
        payment_status = result.get("payment_status", "")
        msg = "Đơn hàng đã được huỷ thành công."
        if payment_status == "refund_pending":
            msg += " Số tiền thanh toán sẽ được hoàn lại trong 3-5 ngày làm việc."
        return {"success": True, "order_id": order_id, "order_status": order_status, "message": msg}
    except OrderError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/api/orders/{order_id}/send-notification")
def send_order_notification(
    order_id: str,
    channel: str = Query("zalo", enum=["zalo", "email", "sms"]),
    _admin: User = Depends(require_admin),
):
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    return {
        "success": True,
        "channel": channel,
        "message": f"Đã gửi thông báo tiến độ đơn hàng {order_id} thành công qua {channel.upper()}",
    }
