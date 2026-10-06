import hashlib
import hmac
import html
import json
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import settings
from app.db.database import db_service
from app.models.schemas import PaymentWebhookPayload, User
from app.routers.deps import (
    check_public_track_rate_limit,
    get_current_user_optional,
    normalize_phone,
    require_admin,
)
from app.services.order_service import order_service
from app.services.vnpay_service import vnpay_service

router = APIRouter(tags=["payment"])


class VNPayCreatePaymentRequest(BaseModel):
    order_id: str
    bank_code: Optional[str] = None


def check_simulate_enabled():
    if not settings.DEBUG or settings.APP_ENV.strip().lower() == "production":
        raise HTTPException(status_code=404, detail="Endpoint không tồn tại")


@router.post("/api/payment/webhook")
async def payment_webhook(
    request: Request,
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
):
    raw_body = await request.body()
    if not x_signature:
        raise HTTPException(status_code=401, detail="Thiếu chữ ký xác thực X-Signature")

    computed_sig = hmac.new(
        settings.PAYMENT_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(x_signature.lower(), computed_sig.lower()):
        raise HTTPException(status_code=401, detail="Chữ ký webhook không hợp lệ")

    try:
        data = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
        body = PaymentWebhookPayload(**data)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Dữ liệu payload webhook không hợp lệ") from e

    order_id = body.order_id
    if not order_id and body.content:
        m = re.search(r"(AURA[-_]\d{6}[-_][A-Za-z0-9]+)", body.content, re.IGNORECASE)
        if m:
            order_id = m.group(1).upper()
        else:
            parts = body.content.strip().split()
            for i, p in enumerate(parts):
                if p.upper() == "AURA" and i + 1 < len(parts):
                    order_id = parts[i + 1].strip()
                    break

    if not order_id:
        raise HTTPException(status_code=400, detail="Không tìm thấy mã đơn hàng trong payload webhook")

    amount = body.amount if body.amount is not None else body.transferAmount
    if amount is None or amount <= 0:
        raise HTTPException(status_code=400, detail="Thiếu số tiền thanh toán (amount) hợp lệ")

    tx_code = body.transaction_code or body.referenceCode or f"TX-{uuid.uuid4().hex[:8].upper()}"
    res = db_service.confirm_payment(
        order_id=order_id,
        amount=amount,
        transaction_code=tx_code,
        payment_channel=body.channel or "vietqr",
    )
    if res == "not_found":
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{order_id}'")
    elif res == "cancelled":
        raise HTTPException(status_code=400, detail=f"Đơn hàng '{order_id}' đã bị hủy, không thể xác nhận thanh toán")
    elif res == "invalid_amount":
        raise HTTPException(status_code=400, detail=f"Số tiền thanh toán ({amount}) không khớp với giá trị đơn hàng")
    return {"success": True, "order_id": order_id, "message": f"Đã xác nhận thanh toán đơn {order_id} thành công!"}


@router.get("/api/payment/check-status/{order_id}")
def check_order_payment_status(order_id: str, request: Request, phone: Optional[str] = Query(None)):
    check_public_track_rate_limit(request)
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

    user = get_current_user_optional(request)
    order_user_id = order.get("user_id")
    if order_user_id:
        if not user or (user.role != "admin" and str(user.id) != str(order_user_id)):
            raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
    else:
        if phone:
            order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
            if normalize_phone(phone) != normalize_phone(order_phone):
                raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

    status = db_service.get_order_payment_status(order_id)
    if not status.get("exists"):
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
    return status


@router.post("/api/payment/simulate-success/{order_id}", dependencies=[Depends(check_simulate_enabled)])
def simulate_payment_success(order_id: str, _admin: User = Depends(require_admin)):
    order = db_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    tx_code = f"MB-{uuid.uuid4().hex[:8].upper()}"
    res = db_service.confirm_payment(order_id=order_id, amount=amount, transaction_code=tx_code, payment_channel="simulation")
    if res == "cancelled":
        raise HTTPException(status_code=400, detail="Đơn hàng đã bị hủy, không thể xác nhận thanh toán")
    elif res == "invalid_amount":
        raise HTTPException(status_code=400, detail="Số tiền thanh toán không khớp")
    return {
        "success": True,
        "order_id": order_id,
        "transaction_code": tx_code,
        "status": "paid",
        "payment_status": "paid",
        "order_status": "confirmed",
        "message": "Thanh toán QR thành công! Trạng thái đơn hàng đã tự động xác nhận.",
    }


@router.post("/api/payment/vnpay/create-payment-url")
def vnpay_create_payment_url(body: VNPayCreatePaymentRequest, request: Request):
    order = db_service.get_order_by_id(body.order_id)
    if not order:
        order = order_service.get_order_by_id(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    client_ip = request.client.host if request.client else "127.0.0.1"
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


@router.get("/api/payment/vnpay/return")
def vnpay_payment_return(request: Request):
    params = dict(request.query_params)
    is_valid, vnp_data = vnpay_service.verify_response(params)

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

    order_id = vnp_data.get("vnp_TxnRef", "")
    response_code = vnp_data.get("vnp_ResponseCode", "")
    transaction_no = vnp_data.get("vnp_TransactionNo") or f"VNPAY-{order_id}"

    try:
        amount_raw = int(vnp_data.get("vnp_Amount", 0))
        amount = amount_raw // 100
    except (ValueError, TypeError):
        amount = 0

    safe_order_id = html.escape(str(order_id))
    safe_response_code = html.escape(str(response_code))

    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)

    if not order:
        if is_browser_html:
            return HTMLResponse(
                content=f"""
                <div style="font-family:sans-serif;text-align:center;padding:50px;">
                    <h2 style="color:#e11d48;">Lỗi đơn hàng</h2>
                    <p>Không tìm thấy mã đơn hàng: <strong>{safe_order_id}</strong></p>
                    <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                </div>
                """,
                status_code=404,
            )
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{order_id}'")

    order_amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    if amount != order_amount:
        if is_browser_html:
            return HTMLResponse(
                content=f"""
                <div style="font-family:sans-serif;text-align:center;padding:50px;">
                    <h2 style="color:#e11d48;">Lỗi số tiền thanh toán</h2>
                    <p>Số tiền thanh toán ({amount}đ) không khớp với giá trị đơn hàng ({order_amount}đ).</p>
                    <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                </div>
                """,
                status_code=400,
            )
        raise HTTPException(status_code=400, detail="Số tiền thanh toán không khớp với đơn hàng")

    if response_code == "00":
        confirm_res = db_service.confirm_payment(
            order_id=order_id,
            amount=amount,
            transaction_code=transaction_no,
            payment_channel="vnpay",
        )
        if confirm_res == "cancelled":
            if is_browser_html:
                return HTMLResponse(
                    content=f"""
                    <div style="font-family:sans-serif;text-align:center;padding:50px;">
                        <h2 style="color:#e11d48;">Đơn hàng đã bị hủy</h2>
                        <p>Đơn hàng <strong>{safe_order_id}</strong> đã bị hủy trước đó.</p>
                        <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                    </div>
                    """,
                    status_code=400,
                )
            raise HTTPException(status_code=400, detail="Đơn hàng đã bị hủy")

        order_service.update_order_status(order_id, "confirmed")
        if is_browser_html:
            resp = RedirectResponse(url=f"/order-success/{order_id}", status_code=302)
            phone_val = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
            if phone_val:
                resp.set_cookie(f"aura_order_{order_id}", phone_val, httponly=True, max_age=3600, samesite="lax")
            return resp
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
                    <p>Mã đơn hàng: <strong>{safe_order_id}</strong></p>
                    <p>Mã phản hồi VNPay: <strong>{safe_response_code}</strong></p>
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


@router.api_route("/api/payment/vnpay/ipn", methods=["GET", "POST"])
async def vnpay_ipn(request: Request):
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
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        return {"RspCode": "01", "Message": "Order not found"}

    order_amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    try:
        vnp_amount = int(vnp_data.get("vnp_Amount", 0)) // 100
    except (ValueError, TypeError):
        vnp_amount = 0

    if vnp_amount != order_amount:
        return {"RspCode": "04", "Message": "Invalid amount"}

    if order.get("payment_status") == "paid" or order.get("status") == "confirmed" or order.get("order_status") == "confirmed":
        return {"RspCode": "02", "Message": "Order already confirmed"}

    response_code = vnp_data.get("vnp_ResponseCode")
    transaction_no = vnp_data.get("vnp_TransactionNo") or f"VNPAY-{order_id}"

    if response_code == "00":
        confirm_res = db_service.confirm_payment(
            order_id=order_id,
            amount=vnp_amount,
            transaction_code=transaction_no,
            payment_channel="vnpay",
        )
        if confirm_res == "already_paid":
            return {"RspCode": "02", "Message": "Order already confirmed"}
        elif confirm_res == "invalid_amount":
            return {"RspCode": "04", "Message": "Invalid amount"}
        elif confirm_res == "not_found":
            return {"RspCode": "01", "Message": "Order not found"}
        elif confirm_res == "cancelled":
            return {"RspCode": "02", "Message": "Order cancelled"}

        order_service.update_order_status(order_id, "confirmed")
        return {"RspCode": "00", "Message": "Confirm Success"}
    else:
        return {"RspCode": "00", "Message": "Payment failed acknowledged"}
