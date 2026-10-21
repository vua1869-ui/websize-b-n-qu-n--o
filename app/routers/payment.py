import datetime as dt
import hashlib
import hmac
import html
import json
import logging
import os
import re
import urllib.parse
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
    get_client_ip,
    get_current_user_optional,
    normalize_phone,
    require_admin,
    sign_guest_order_cookie,
    verify_guest_order_cookie,
    verify_order_access,
)
from app.services.momo_service import momo_service
from app.services.order_service import order_service
from app.services.vnpay_service import get_vnpay_response_message, vnpay_service

logger = logging.getLogger("aura.payment")

router = APIRouter(tags=["payment"])


class VNPayCreatePaymentRequest(BaseModel):
    order_id: str
    bank_code: Optional[str] = None
    phone: Optional[str] = None


class MoMoCreatePaymentRequest(BaseModel):
    order_id: str
    phone: Optional[str] = None



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
    elif res in ("cancelled", "refund_pending"):
        return {
            "success": False,
            "order_id": order_id,
            "status": "refund_pending",
            "message": f"Đơn hàng '{order_id}' đã bị hủy trước đó. Giao dịch đã được ghi nhận vào danh sách cần hoàn tiền (refund_pending).",
        }
    elif res == "invalid_amount":
        raise HTTPException(status_code=400, detail=f"Số tiền thanh toán ({amount}) không khớp với giá trị đơn hàng")
    elif res == "already_paid":
        return {
            "success": True,
            "order_id": order_id,
            "already_paid": True,
            "message": f"Giao dịch cho đơn {order_id} đã được xác nhận thanh toán trước đó (idempotent).",
        }
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
        # Đơn vãng lai: Admin có quyền xem, người dùng khác bắt buộc cung cấp SĐT khớp với đơn hoặc cookie HMAC hợp lệ
        if not (user and user.role == "admin"):
            order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
            cookie_token = request.cookies.get(f"aura_order_{order_id}")
            has_cookie_access = verify_guest_order_cookie(cookie_token, order_id, order_phone)
            has_phone_access = bool(phone and normalize_phone(phone) == normalize_phone(order_phone))
            if not (has_cookie_access or has_phone_access):
                raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại hoặc cần cung cấp số điện thoại xác thực")

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

    # Kiểm tra trạng thái đơn hàng
    if order.get("payment_status") == "paid":
        raise HTTPException(status_code=400, detail="Đơn hàng này đã được thanh toán")

    if order.get("status") == "cancelled" or order.get("order_status") == "cancelled":
        raise HTTPException(status_code=400, detail="Đơn hàng đã bị hủy, không thể tạo link thanh toán")

    # Kiểm tra quyền sở hữu đơn hàng
    user = get_current_user_optional(request)
    cookie_token = request.cookies.get(f"aura_order_{body.order_id}")
    order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    has_cookie = verify_guest_order_cookie(cookie_token, body.order_id, order_phone)
    phone_to_check = body.phone or (order_phone if has_cookie else None)
    is_allowed, _ = verify_order_access(order, user, phone_to_check)
    if not is_allowed:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập hoặc thanh toán đơn hàng này")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))

    # Xử lý IP client chuẩn xác kể cả khi chạy sau reverse proxy qua get_client_ip
    client_ip = get_client_ip(request)

    is_cfg = vnpay_service.is_configured()
    if not is_cfg and settings.APP_ENV.strip().lower() == "production":
        raise HTTPException(
            status_code=503,
            detail="Cổng thanh toán VNPay chưa được cấu hình trên môi trường Production"
        )

    mock_url = f"/api/payment/vnpay/mock-gateway?order_id={body.order_id}"
    if not is_cfg:
        payment_url = mock_url
    else:
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
        "mock_url": mock_url,
    }


@router.get("/api/payment/vnpay/mock-gateway", response_class=HTMLResponse)
def vnpay_mock_gateway(order_id: str = Query(...)):
    if settings.APP_ENV.strip().lower() == "production":
        raise HTTPException(status_code=403, detail="Cổng thanh toán mô phỏng bị vô hiệu hóa trên môi trường Production")

    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    safe_order_id = html.escape(str(order_id))
    amount_vnd = f"{amount:,}".replace(",", ".") + "đ"

    secret_key = str(settings.VNPAY_HASH_SECRET).strip() or "MOCK_VNPAY_SECRET_FOR_DEV_ONLY"

    # Tạo link xác nhận thành công chuẩn chữ ký HMAC-SHA512
    vnp_params = {
        "vnp_Amount": str(amount * 100),
        "vnp_BankCode": "NCB",
        "vnp_BankTranNo": f"VNP{uuid.uuid4().hex[:8].upper()}",
        "vnp_CardType": "ATM",
        "vnp_OrderInfo": f"Thanh toan don hang {order_id}",
        "vnp_PayDate": dt.datetime.now().strftime("%Y%m%d%H%M%S"),
        "vnp_ResponseCode": "00",
        "vnp_TmnCode": str(settings.VNPAY_TMN_CODE).strip() or "VNPAYDEMO",
        "vnp_TransactionNo": f"VNPAY-{order_id}",
        "vnp_TransactionStatus": "00",
        "vnp_TxnRef": order_id,
    }
    inputData = sorted(vnp_params.items())
    queryString = "&".join(f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in inputData)
    secure_hash = hmac.new(
        secret_key.encode("utf-8"),
        queryString.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    success_url = f"/api/payment/vnpay/return?{queryString}&vnp_SecureHash={secure_hash}"

    # Tạo link hủy thanh toán chuẩn chữ ký HMAC-SHA512 (mã lỗi 24)
    vnp_cancel_params = {
        "vnp_Amount": str(amount * 100),
        "vnp_OrderInfo": f"Thanh toan don hang {order_id}",
        "vnp_PayDate": dt.datetime.now().strftime("%Y%m%d%H%M%S"),
        "vnp_ResponseCode": "24",
        "vnp_TmnCode": str(settings.VNPAY_TMN_CODE).strip() or "VNPAYDEMO",
        "vnp_TransactionNo": "0",
        "vnp_TransactionStatus": "02",
        "vnp_TxnRef": order_id,
    }
    cancel_inputData = sorted(vnp_cancel_params.items())
    cancel_queryString = "&".join(f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in cancel_inputData)
    cancel_hash = hmac.new(
        secret_key.encode("utf-8"),
        cancel_queryString.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    cancel_url = f"/api/payment/vnpay/return?{cancel_queryString}&vnp_SecureHash={cancel_hash}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Cổng Thanh Toán VNPay Sandbox (Mô phỏng) • AURA STUDIO</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f8fafc; color: #1e293b; margin: 0; padding: 20px; }}
            .container {{ max-width: 520px; margin: 40px auto; background: #fff; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); overflow: hidden; border: 1px solid #e2e8f0; }}
            .header {{ background: #005baa; color: #fff; padding: 24px; text-align: center; }}
            .header h1 {{ margin: 0 0 6px; font-size: 20px; font-weight: 700; }}
            .header p {{ margin: 0; opacity: 0.9; font-size: 13px; }}
            .content {{ padding: 24px; }}
            .info-row {{ display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #f1f5f9; font-size: 14px; }}
            .info-row:last-child {{ border-bottom: none; }}
            .label {{ color: #64748b; font-weight: 500; }}
            .value {{ font-weight: 600; color: #0f172a; text-align: right; }}
            .amount {{ color: #005baa; font-size: 20px; font-weight: 700; }}
            .badge {{ display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; background: #e0f2fe; color: #0369a1; }}
            .actions {{ padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 10px; background: #f8fafc; border-top: 1px solid #e2e8f0; }}
            .btn {{ display: block; width: 100%; box-sizing: border-box; text-align: center; padding: 12px 20px; border-radius: 8px; font-weight: 600; font-size: 14px; text-decoration: none; transition: 0.2s; cursor: pointer; border: none; }}
            .btn-primary {{ background: #005baa; color: #fff; }}
            .btn-primary:hover {{ background: #004c8f; }}
            .btn-secondary {{ background: #e2e8f0; color: #475569; }}
            .btn-secondary:hover {{ background: #cbd5e1; }}
            .notice {{ margin-top: 15px; font-size: 12px; color: #94a3b8; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>CỔNG THANH TOÁN VNPAY</h1>
                <p>Môi trường thanh toán Sandbox mô phỏng nội bộ</p>
            </div>
            <div class="content">
                <div class="info-row">
                    <span class="label">Đơn vị thụ hưởng</span>
                    <span class="value">AURA STUDIO</span>
                </div>
                <div class="info-row">
                    <span class="label">Mã đơn hàng</span>
                    <span class="value">{safe_order_id}</span>
                </div>
                <div class="info-row">
                    <span class="label">Số tiền thanh toán</span>
                    <span class="value amount">{amount_vnd}</span>
                </div>
                <div class="info-row">
                    <span class="label">Ngân hàng mô phỏng</span>
                    <span class="value"><span class="badge">NCB / Thẻ ATM Nội Địa</span></span>
                </div>
            </div>
            <div class="actions">
                <a href="{success_url}" class="btn btn-primary">XÁC NHẬN THANH TOÁN THÀNH CÔNG</a>
                <a href="{cancel_url}" class="btn btn-secondary">HỦY THANH TOÁN</a>
                <div class="notice">Trang mô phỏng nội bộ của AURA STUDIO hỗ trợ kiểm thử cổng thanh toán VNPay 2.1.0.</div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


def _error_page(
    title: str,
    message: str,
    status_code: int = 400,
    color: str = "#e11d48",
    back_url: str = "/",
    retry_url: Optional[str] = None,
    retry_text: str = "Thử thanh toán lại",
    icon: str = "⚠️",
) -> HTMLResponse:
    """Helper gom chung render trang lỗi HTML có charset UTF-8 chuẩn cho toàn bộ cổng thanh toán."""
    retry_btn = (
        f'<a href="{html.escape(retry_url)}" class="btn" style="background:#005baa;margin-right:8px;">{html.escape(retry_text)}</a>'
        if retry_url
        else ""
    )
    body_content = message if ("<p>" in message or "<div" in message) else f"<p>{message}</p>"
    content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(title)} • AURA STUDIO</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#FAF8F5;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
    .card{{background:#fff;border:1px solid #E5E2DC;border-radius:4px;padding:48px 40px;max-width:480px;width:100%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.06)}}
    .icon{{font-size:48px;margin-bottom:16px}}
    h2{{font-size:20px;font-weight:700;color:{color};margin-bottom:12px}}
    p{{font-size:14px;color:#555;line-height:1.6;margin-bottom:8px}}
    strong{{color:#111}}
    .btn{{display:inline-block;margin-top:28px;padding:11px 24px;background:#111;color:#fff;text-decoration:none;border-radius:3px;font-size:14px;font-weight:600;letter-spacing:.02em;transition:background .2s}}
    .btn:hover{{background:#333}}
    .btn-outline{{background:transparent;border:1px solid #111;color:#111;margin-left:6px}}
    .btn-outline:hover{{background:#111;color:#fff}}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h2>{html.escape(title)}</h2>
    {body_content}
    <div style="margin-top: 24px;">
      {retry_btn}
      <a href="{html.escape(back_url)}" class="btn btn-outline">Về trang chủ</a>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=content, status_code=status_code, media_type="text/html; charset=utf-8")


def _payment_error_html(
    title: str,
    body_html: str,
    status: int = 400,
    back_url: str = "/",
    retry_url: Optional[str] = None,
    retry_text: str = "Thử thanh toán lại",
) -> HTMLResponse:
    return _error_page(
        title=title,
        message=body_html,
        status_code=status,
        color="#111",
        back_url=back_url,
        retry_url=retry_url,
        retry_text=retry_text,
    )


@router.get("/api/payment/vnpay/return")
def vnpay_payment_return(request: Request):
    params = dict(request.query_params)
    is_valid, vnp_data = vnpay_service.verify_response(params)

    accept_header = request.headers.get("accept", "")
    is_json_request = "application/json" in accept_header.lower()
    is_browser_html = not is_json_request

    if not is_valid:
        if is_browser_html:
            return _payment_error_html(
                title="Lỗi bảo mật thanh toán",
                body_html="""<div class="icon">🔒</div>
                  <h2>Lỗi bảo mật thanh toán</h2>
                  <p>Chữ ký bảo mật VNPay không hợp lệ.<br>Vui lòng không chỉnh sửa URL phản hồi hoặc kiểm tra lại cấu hình bí mật.</p>""",
                status=400,
            )
        raise HTTPException(status_code=400, detail="Chữ ký phản hồi VNPay không hợp lệ")

    order_id = vnp_data.get("vnp_TxnRef", "")
    response_code = vnp_data.get("vnp_ResponseCode", "")
    trans_status = vnp_data.get("vnp_TransactionStatus", "00")
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
            return _payment_error_html(
                title="Không tìm thấy đơn hàng",
                body_html=f"""<div class="icon">📋</div>
                  <h2>Không tìm thấy đơn hàng</h2>
                  <p>Mã đơn hàng <strong>{safe_order_id}</strong> không tồn tại trong hệ thống.</p>""",
                status=404,
            )
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{order_id}'")

    order_amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    if amount != order_amount:
        if is_browser_html:
            return _payment_error_html(
                title="Lỗi số tiền thanh toán",
                body_html=f"""<div class="icon">⚠️</div>
                  <h2>Số tiền không khớp</h2>
                  <p>Số tiền VNPay: <strong>{amount:,}đ</strong></p>
                  <p>Giá trị đơn hàng: <strong>{order_amount:,}đ</strong></p>
                  <p>Vui lòng liên hệ bộ phận hỗ trợ khách hàng.</p>""",
                status=400,
                retry_url=f"/order-success/{order_id}",
            )
        raise HTTPException(status_code=400, detail="Số tiền thanh toán không khớp với đơn hàng")

    is_success = (response_code == "00" and trans_status in ("00", ""))

    if is_success:
        confirm_res = db_service.confirm_payment(
            order_id=order_id,
            amount=amount,
            transaction_code=transaction_no,
            payment_channel="vnpay",
        )
        if confirm_res in ("cancelled", "refund_pending"):
            if is_browser_html:
                return _payment_error_html(
                    title="Đơn hàng đã bị hủy",
                    body_html=f"""<div class="icon">❌</div>
                      <h2>Đơn hàng đã bị hủy</h2>
                      <p>Đơn hàng <strong>{safe_order_id}</strong> đã bị hủy trước đó.</p>
                      <p>Hệ thống đã tự động ghi nhận thanh toán {amount:,}đ và đưa vào danh sách hoàn tiền cho quý khách.</p>""",
                    status=200,
                    back_url="/",
                )
            return {
                "success": False,
                "order_id": order_id,
                "status": "refund_pending",
                "message": "Đơn hàng đã bị hủy, thanh toán được ghi nhận để hoàn tiền",
            }

        if order.get("order_status") == "pending_payment" or order.get("status") == "pending_payment":
            order_service.update_order_status(order_id, "confirmed")
        if is_browser_html:
            resp = RedirectResponse(url=f"/order-success/{order_id}", status_code=302)
            phone_val = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
            if phone_val:
                cookie_val = sign_guest_order_cookie(order_id, phone_val)
                resp.set_cookie(f"aura_order_{order_id}", cookie_val, httponly=True, max_age=3600, samesite="lax")
            return resp
        return {
            "success": True,
            "order_id": order_id,
            "response_code": response_code,
            "transaction_status": trans_status,
            "payment_status": "paid",
            "order_status": "confirmed",
            "message": "Thanh toán VNPay thành công!",
        }
    else:
        err_msg = get_vnpay_response_message(response_code)
        safe_err_msg = html.escape(err_msg)
        is_user_cancel = (response_code == "24")
        title = "Giao dịch đã bị hủy" if is_user_cancel else "Thanh toán chưa hoàn tất"
        icon = "↩️" if is_user_cancel else "⏳"

        if is_browser_html:
            return _payment_error_html(
                title=title,
                body_html=f"""<div class="icon">{icon}</div>
                  <h2>{html.escape(title)}</h2>
                  <p>Mã đơn hàng: <strong>{safe_order_id}</strong></p>
                  <p>{safe_err_msg}</p>
                  <p style="color:#888;font-size:12px;margin-top:6px;">Mã phản hồi VNPay: {safe_response_code}</p>""",
                status=200,
                retry_url=f"/order-success/{order_id}",
                retry_text="Thanh toán lại",
            )
        return {
            "success": False,
            "order_id": order_id,
            "response_code": response_code,
            "transaction_status": trans_status,
            "payment_status": "unpaid",
            "order_status": order.get("order_status") or order.get("status") or "pending_payment",
            "message": err_msg,
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

    # Chỉ kiểm tra payment_status == 'paid' để chống ghi nhận lặp (Idempotent)
    if order.get("payment_status") == "paid":
        return {"RspCode": "02", "Message": "Order already confirmed"}

    response_code = vnp_data.get("vnp_ResponseCode")
    trans_status = vnp_data.get("vnp_TransactionStatus", "00")
    transaction_no = vnp_data.get("vnp_TransactionNo") or f"VNPAY-{order_id}"

    is_success = (response_code == "00" and trans_status in ("00", ""))

    if is_success:
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
        elif confirm_res in ("cancelled", "refund_pending"):
            # Đơn hàng đã bị hủy, thanh toán được đưa vào danh sách hoàn tiền.
            # Trả về RspCode 00 để cổng VNPay xác nhận webhook thành công và không thử lại.
            return {"RspCode": "00", "Message": "Confirm Success"}

        if order.get("order_status") == "pending_payment" or order.get("status") == "pending_payment":
            order_service.update_order_status(order_id, "confirmed")
        return {"RspCode": "00", "Message": "Confirm Success"}
    else:
        # Giao dịch thất bại tại cổng VNPay (khách hủy hoặc lỗi thẻ)
        # Xác nhận đã tiếp nhận thông tin từ IPN để VNPay ngừng gửi lại
        return {"RspCode": "00", "Message": "Confirm Success"}


# ==========================================
# Cổng thanh toán MoMo Sandbox & Production
# ==========================================
@router.post("/api/payment/momo/create-payment-url")
def momo_create_payment_url(body: MoMoCreatePaymentRequest, request: Request):
    order = db_service.get_order_by_id(body.order_id)
    if not order:
        order = order_service.get_order_by_id(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

    # Kiểm tra quyền sở hữu đơn hàng
    user = get_current_user_optional(request)
    cookie_token = request.cookies.get(f"aura_order_{body.order_id}")
    order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    has_cookie = verify_guest_order_cookie(cookie_token, body.order_id, order_phone)
    phone_to_check = body.phone or (order_phone if has_cookie else None)
    is_allowed, _ = verify_order_access(order, user, phone_to_check)
    if not is_allowed:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập hoặc thanh toán đơn hàng này")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))

    res = {}
    sec = (settings.MOMO_SECRET_KEY or "").strip()
    acc = (settings.MOMO_ACCESS_KEY or "").strip()
    if sec and acc and "change" not in sec.lower():
        try:
            res = momo_service.build_payment_url(
                order_id=body.order_id,
                amount=amount,
                order_desc=f"Thanh toan don hang {body.order_id}",
            )
        except Exception as exc:
            logger.warning("Lỗi tạo URL thanh toán MoMo: %s", exc)
            res = {"success": False, "message": str(exc)}

    # Nếu chưa có API MoMo thật hoặc key Sandbox bị lỗi -> Fallback sang luồng thanh toán QR/giả lập MoMo nội bộ
    if not res.get("success"):
        mock_url = f"/api/payment/momo/mock-gateway?order_id={body.order_id}"
        phone_num = getattr(settings, "MOMO_PHONE", "") or "0775972171"
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=2|99|{phone_num}|||0|0|{amount}|AURA%20{body.order_id}|transfer_myqr"
        return {
            "success": True,
            "order_id": body.order_id,
            "payment_url": mock_url,
            "qr_code_url": qr_url,
            "deeplink": mock_url,
            "is_simulation": True,
            "message": "Đang chuyển hướng sang cổng thanh toán MoMo...",
        }

    return {
        "success": True,
        "order_id": body.order_id,
        "payment_url": res.get("payment_url"),
        "qr_code_url": res.get("qr_code_url"),
        "deeplink": res.get("deeplink"),
    }


@router.get("/api/payment/momo/mock-gateway", response_class=HTMLResponse)
def momo_mock_gateway(order_id: str = Query(...)):
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    safe_order_id = html.escape(str(order_id))
    amount_vnd = f"{amount:,}".replace(",", ".") + "đ"

    success_url = f"/api/payment/momo/return?orderId={safe_order_id}&amount={amount}&resultCode=0&transId=MOMO-{safe_order_id}&is_simulation=true"
    cancel_url = f"/api/payment/momo/return?orderId={safe_order_id}&amount={amount}&resultCode=1006&is_simulation=true"
    phone_num = getattr(settings, "MOMO_PHONE", "") or "0775972171"
    qr_src = f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=2|99|{phone_num}|||0|0|{amount}|AURA%20{order_id}|transfer_myqr"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Cổng Thanh Toán MoMo (Mô phỏng) • AURA STUDIO</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #fff1f2; color: #1e293b; margin: 0; padding: 20px; }}
            .container {{ max-width: 480px; margin: 40px auto; background: #fff; border-radius: 16px; box-shadow: 0 4px 25px rgba(165, 0, 100, 0.12); overflow: hidden; border: 1px solid #fecdd3; }}
            .header {{ background: #a50064; color: #fff; padding: 24px; text-align: center; }}
            .header h1 {{ margin: 0 0 6px; font-size: 20px; font-weight: 700; letter-spacing: 0.5px; }}
            .header p {{ margin: 0; opacity: 0.9; font-size: 13px; }}
            .qr-box {{ text-align: center; padding: 24px 20px 10px; background: #fafafa; border-bottom: 1px solid #f1f5f9; }}
            .qr-box img {{ width: 180px; height: 180px; border-radius: 8px; border: 1px solid #e2e8f0; background: #fff; padding: 6px; }}
            .content {{ padding: 20px 24px; }}
            .info-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f1f5f9; font-size: 14px; }}
            .info-row:last-child {{ border-bottom: none; }}
            .label {{ color: #64748b; font-weight: 500; }}
            .value {{ font-weight: 600; color: #0f172a; text-align: right; }}
            .amount {{ color: #a50064; font-size: 20px; font-weight: 700; }}
            .actions {{ padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 10px; background: #fff; }}
            .btn {{ display: block; width: 100%; box-sizing: border-box; text-align: center; padding: 12px 20px; border-radius: 8px; font-weight: 600; font-size: 14px; text-decoration: none; transition: 0.2s; cursor: pointer; border: none; }}
            .btn-primary {{ background: #a50064; color: #fff; }}
            .btn-primary:hover {{ background: #880052; }}
            .btn-secondary {{ background: #f1f5f9; color: #64748b; }}
            .btn-secondary:hover {{ background: #e2e8f0; }}
            .notice {{ margin-top: 15px; font-size: 12px; color: #94a3b8; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>VÍ ĐIỆN TỬ MOMO</h1>
                <p>Cổng thanh toán MoMo QR / App (Mô phỏng nội bộ)</p>
            </div>
            <div class="qr-box">
                <img src="{qr_src}" alt="MoMo QR Code" />
                <p style="margin: 8px 0 0; font-size: 12px; color: #64748b;">Quét mã MoMo để thanh toán</p>
            </div>
            <div class="content">
                <div class="info-row">
                    <span class="label">Đơn vị nhận</span>
                    <span class="value">AURA STUDIO</span>
                </div>
                <div class="info-row">
                    <span class="label">Mã đơn hàng</span>
                    <span class="value">{safe_order_id}</span>
                </div>
                <div class="info-row">
                    <span class="label">Số tiền</span>
                    <span class="value amount">{amount_vnd}</span>
                </div>
            </div>
            <div class="actions">
                <a href="{success_url}" class="btn btn-primary">XÁC NHẬN ĐÃ THANH TOÁN QUA MOMO</a>
                <a href="{cancel_url}" class="btn btn-secondary">HỦY THANH TOÁN</a>
                <div class="notice">Giao dịch mô phỏng an toàn cho kiểm thử tích hợp MoMo.</div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get("/api/payment/momo/return")
def momo_payment_return(request: Request):
    params = dict(request.query_params)
    is_valid, momo_data = momo_service.verify_signature(params)

    accept_header = request.headers.get("accept", "")
    is_json_request = "application/json" in accept_header.lower()
    is_browser_html = not is_json_request

    if not is_valid:
        if is_browser_html:
            return _error_page(
                title="Lỗi bảo mật thanh toán",
                message="Chữ ký bảo mật MoMo không hợp lệ hoặc dữ liệu phản hồi bị can thiệp.",
                status_code=400,
                color="#e11d48",
            )
        raise HTTPException(status_code=400, detail="Chữ ký phản hồi MoMo không hợp lệ")

    order_id = momo_data.get("orderId", "")
    result_code = str(momo_data.get("resultCode", ""))
    trans_id = momo_data.get("transId") or f"MOMO-{order_id}"

    try:
        amount = int(momo_data.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0

    safe_order_id = html.escape(str(order_id))
    safe_result_code = html.escape(str(result_code))

    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)

    if not order:
        if is_browser_html:
            return _error_page(
                title="Lỗi đơn hàng",
                message=f"Không tìm thấy mã đơn hàng: <strong>{safe_order_id}</strong>",
                status_code=404,
                color="#e11d48",
            )
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{order_id}'")

    order_amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    if amount != order_amount:
        if is_browser_html:
            return _error_page(
                title="Lỗi số tiền thanh toán",
                message=f"Số tiền thanh toán ({amount:,}đ) không khớp với giá trị đơn hàng ({order_amount:,}đ).",
                status_code=400,
                color="#e11d48",
            )
        raise HTTPException(status_code=400, detail="Số tiền thanh toán không khớp với đơn hàng")

    if result_code == "0":
        confirm_res = db_service.confirm_payment(
            order_id=order_id,
            amount=amount,
            transaction_code=str(trans_id),
            payment_channel="momo",
        )
        if confirm_res in ("cancelled", "refund_pending"):
            if is_browser_html:
                return _error_page(
                    title="Đơn hàng đã bị hủy",
                    message=f"Đơn hàng <strong>{safe_order_id}</strong> đã bị hủy trước đó. Hệ thống đã ghi nhận thanh toán chờ hoàn tiền cho quý khách.",
                    status_code=200,
                    color="#e11d48",
                )
            return {
                "success": False,
                "order_id": order_id,
                "status": "refund_pending",
                "message": "Đơn hàng đã bị hủy, thanh toán được ghi nhận để hoàn tiền",
            }

        if order.get("order_status") == "pending_payment" or order.get("status") == "pending_payment":
            order_service.update_order_status(order_id, "confirmed")
        if is_browser_html:
            resp = RedirectResponse(url=f"/order-success/{order_id}", status_code=302)
            phone_val = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
            if phone_val:
                cookie_val = sign_guest_order_cookie(order_id, phone_val)
                resp.set_cookie(f"aura_order_{order_id}", cookie_val, httponly=True, max_age=3600, samesite="lax")
            return resp
        return {
            "success": True,
            "order_id": order_id,
            "result_code": result_code,
            "payment_status": "paid",
            "order_status": "confirmed",
            "message": "Thanh toán MoMo thành công!",
        }
    else:
        if is_browser_html:
            return _error_page(
                title="Giao dịch chưa hoàn tất",
                message=f"Mã đơn hàng: <strong>{safe_order_id}</strong><br>Mã phản hồi MoMo: <strong>{safe_result_code}</strong><br>Giao dịch thanh toán qua MoMo chưa thành công hoặc bạn đã hủy thao tác.",
                status_code=200,
                color="#d97706",
            )
        return {
            "success": False,
            "order_id": order_id,
            "result_code": result_code,
            "payment_status": "unpaid",
            "order_status": "pending_payment",
            "message": f"Giao dịch MoMo không thành công (Mã lỗi: {result_code})",
        }


@router.post("/api/payment/momo/ipn")
async def momo_ipn(request: Request):
    """
    Webhook tiếp nhận Instant Payment Notification (IPN) từ MoMo Gateway.
    MoMo gửi dữ liệu qua POST JSON.
    """
    try:
        params = await request.json()
    except Exception:
        try:
            form = await request.form()
            params = dict(form)
        except Exception:
            params = dict(request.query_params)

    is_valid, momo_data = momo_service.verify_signature(params)
    if not is_valid:
        return {"message": "Invalid signature", "resultCode": 97}

    order_id = momo_data.get("orderId")
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        return {"message": "Order not found", "resultCode": 1}

    order_amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    try:
        amount = int(momo_data.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0

    if amount != order_amount:
        return {"message": "Invalid amount", "resultCode": 4}

    if order.get("payment_status") == "paid" or order.get("status") == "confirmed" or order.get("order_status") == "confirmed":
        return {"message": "Order already confirmed", "resultCode": 0}

    result_code = str(momo_data.get("resultCode", ""))
    trans_id = momo_data.get("transId") or f"MOMO-{order_id}"

    if result_code == "0":
        confirm_res = db_service.confirm_payment(
            order_id=order_id,
            amount=amount,
            transaction_code=str(trans_id),
            payment_channel="momo",
        )
        if confirm_res == "already_paid":
            return {"message": "Order already confirmed", "resultCode": 0}
        elif confirm_res == "invalid_amount":
            return {"message": "Invalid amount", "resultCode": 4}
        elif confirm_res == "not_found":
            return {"message": "Order not found", "resultCode": 1}
        elif confirm_res in ("cancelled", "refund_pending"):
            return {"message": "Order cancelled, payment marked for refund", "resultCode": 0}

        if order.get("order_status") == "pending_payment" or order.get("status") == "pending_payment":
            order_service.update_order_status(order_id, "confirmed")
        return {"message": "Success", "resultCode": 0}
    else:
        return {"message": "Payment failed acknowledged", "resultCode": 0}


# ==========================================
# Ngân hàng Quân Đội (MB Bank) — Tự động nhận diện biến động thanh toán 100% qua SePay
# ==========================================

@router.get("/payment/momo/{order_id}")
def momo_redirect_to_mbank(order_id: str):
    """Chuyển hướng từ đường dẫn cũ sang phương thức thanh toán MB Bank tự động."""
    return RedirectResponse(url=f"/payment/mbank/{order_id}", status_code=302)


@router.get("/payment/mbank/{order_id}", response_class=HTMLResponse)
def mbank_payment_page(order_id: str, request: Request):
    """Trang Chuyển khoản Ngân hàng MB Bank (Tự động xác nhận) — Polling 3s/lần và tự chuyển hướng khi SePay báo paid."""
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    bank_name = (getattr(settings, "MBBANK_BANK_NAME", "") or "Ngân hàng TMCP Quân Đội (MB Bank)").strip()
    bank_account = (getattr(settings, "MBBANK_ACCOUNT_NUMBER", "") or "0835731473").strip()
    bank_owner = (getattr(settings, "MBBANK_ACCOUNT_NAME", "") or "VU TIEN ANH").strip()
    mb_qr_static = (getattr(settings, "MBBANK_QR_IMAGE", "") or "/static/images/MB-qr.jpg").strip()

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    transfer_content = str(order_id).upper() if str(order_id).upper().startswith("AURA-") else f"AURA-{order_id}"

    safe_order_id = html.escape(str(order_id))
    amount_vnd = f"{amount:,}".replace(",", ".") + "đ"
    safe_bank_name = html.escape(bank_name)
    safe_bank_account = html.escape(bank_account)
    safe_bank_owner = html.escape(bank_owner)
    safe_content = html.escape(transfer_content)

    vietqr_fallback = f"https://img.vietqr.io/image/MB-{bank_account}-compact2.png?amount={amount}&addInfo={urllib.parse.quote(transfer_content)}&accountName={urllib.parse.quote(bank_owner)}"

    # Kiểm tra file QR có sẵn
    has_static = False
    if mb_qr_static:
        if mb_qr_static.startswith("http://") or mb_qr_static.startswith("https://"):
            has_static = True
        else:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidate_paths = [
                os.path.join(app_dir, mb_qr_static.lstrip("/")),
                os.path.join(os.path.dirname(app_dir), mb_qr_static.lstrip("/")),
                os.path.join(app_dir, "static", "images", "MB-qr.jpg"),
                os.path.join(app_dir, "static", "images", "MB-qr"),
                os.path.join(app_dir, "static", "img", "MB-qr.jpg"),
            ]
            has_static = any(os.path.isfile(cp) for cp in candidate_paths)

    qr_img_src = html.escape(mb_qr_static) if has_static else vietqr_fallback
    is_debug = bool(settings.DEBUG)

    phone_val = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    phone_param = f"?phone={urllib.parse.quote(phone_val)}" if phone_val else ""
    target_success_url = f"/order-success/{safe_order_id}{phone_param}"

    html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Chuyển khoản MB Bank • {safe_order_id} • AURA STUDIO</title>
  <meta name="description" content="Chuyển khoản Ngân hàng MB Bank tự động xác nhận đơn hàng {safe_order_id}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    :root{{
      --mb-navy:#002855;
      --mb-blue:#0052cc;
      --mb-light:#f0f5ff;
      --mb-border:#d0e2ff;
      --mb-star:#00d287;
      --text-main:#111827;
      --text-muted:#6b7280;
    }}
    body{{
      font-family:'Be Vietnam Pro',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      background:linear-gradient(135deg,#eef4ff 0%,#dbeafe 50%,#f0fdf4 100%);
      min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px
    }}
    .card{{
      background:#fff;border-radius:24px;
      box-shadow:0 20px 60px rgba(0,40,85,.16),0 4px 16px rgba(0,0,0,.06);
      max-width:460px;width:100%;overflow:hidden;animation:su .45s cubic-bezier(.16,1,.3,1);position:relative
    }}
    @keyframes su{{from{{opacity:0;transform:translateY(24px)}}to{{opacity:1;transform:none}}}}
    .hdr{{
      background:linear-gradient(135deg,var(--mb-navy) 0%,#003882 100%);color:#fff;
      padding:24px 24px 34px;text-align:center;position:relative
    }}
    .hdr::after{{
      content:'';position:absolute;bottom:-1px;left:0;right:0;height:22px;
      background:#fff;border-radius:22px 22px 0 0
    }}
    .logo{{display:inline-flex;align-items:center;gap:10px;margin-bottom:8px}}
    .logo .ic{{
      width:44px;height:44px;background:rgba(255,255,255,.15);border-radius:12px;
      display:flex;align-items:center;justify-content:center;font-size:24px
    }}
    .logo .br{{font-size:21px;font-weight:800;letter-spacing:-.5px}}
    .hdr p{{font-size:13px;opacity:.9}}
    .radar-bar{{
      display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.18);
      backdrop-filter:blur(4px);padding:5px 14px;border-radius:20px;font-size:12px;font-weight:600;margin-top:10px
    }}
    .dot{{width:8px;height:8px;border-radius:50%;background:#00d287;animation:blink 1.2s ease-in-out infinite}}
    @keyframes blink{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.4;transform:scale(.8)}}}}
    .body{{padding:18px 24px 0}}
    .qr-wrap{{
      display:flex;flex-direction:column;align-items:center;padding:16px;
      background:var(--mb-light);border-radius:18px;border:2px solid var(--mb-border);
      margin-bottom:16px;position:relative
    }}
    .qr-img{{
      width:210px;height:210px;border-radius:14px;border:3px solid #fff;
      box-shadow:0 6px 20px rgba(0,40,85,.15);object-fit:contain;background:#fff
    }}
    .qr-lbl{{margin-top:10px;font-size:12px;color:var(--mb-navy);font-weight:600}}
    .tbl{{width:100%;border-collapse:collapse;margin-bottom:14px}}
    .tbl tr{{border-bottom:1px solid #f3f4f6}}
    .tbl tr:last-child{{border-bottom:none}}
    .tbl td{{padding:10px 4px;font-size:13.5px}}
    .tbl .l{{color:var(--text-muted);width:38%}}
    .tbl .v{{font-weight:700;color:var(--text-main);text-align:right}}
    .tbl .v.amt{{color:var(--mb-navy);font-size:18px}}
    .cp{{
      display:inline-flex;align-items:center;gap:4px;background:#f3f4f6;
      border:1px solid #e5e7eb;border-radius:6px;padding:3px 8px;font-size:11.5px;
      font-weight:600;color:#374151;cursor:pointer;transition:all .15s;margin-left:6px;vertical-align:middle
    }}
    .cp:hover{{background:var(--mb-light);border-color:var(--mb-blue);color:var(--mb-navy)}}
    .cp.ok{{background:#ecfdf5;border-color:#10b981;color:#047857}}
    .ftr{{padding:12px 24px 24px}}
    .auto-notice{{
      background:linear-gradient(135deg,#ecfdf5,#f0fdf4);border:1px solid #a7f3d0;
      border-radius:12px;padding:12px 14px;font-size:12.5px;color:#065f46;line-height:1.5;margin-bottom:14px;display:flex;gap:10px
    }}
    .btn-check{{
      display:flex;align-items:center;justify-content:center;gap:8px;width:100%;background:#fff;color:var(--mb-navy);
      border:2px solid var(--mb-navy);text-align:center;padding:12px 20px;border-radius:13px;
      font-size:14px;font-weight:700;cursor:pointer;transition:all .2s;
      margin-bottom:10px;font-family:inherit
    }}
    .btn-check:hover{{background:var(--mb-light)}}
    .btn-check:disabled{{opacity:.6;cursor:not-allowed}}
    .cancel{{display:block;text-align:center;color:#9ca3af;font-size:13px;text-decoration:none;padding:6px;transition:color .2s}}
    .cancel:hover{{color:#374151;text-decoration:underline}}
    .ov{{
      display:none;position:fixed;inset:0;background:rgba(255,255,255,.95);
      backdrop-filter:blur(6px);z-index:999;align-items:center;justify-content:center;
      flex-direction:column;gap:16px;text-align:center;padding:20px
    }}
    .ov.on{{display:flex}}
    .sp{{
      width:48px;height:48px;border:4px solid rgba(0,40,85,.15);
      border-top-color:var(--mb-navy);border-radius:50%;animation:spin .8s linear infinite
    }}
    @keyframes spin{{to{{transform:rotate(360deg)}}}}
    .succ-icon{{
      width:68px;height:68px;background:#10b981;color:#fff;border-radius:50%;
      display:flex;align-items:center;justify-content:center;font-size:38px;box-shadow:0 10px 25px rgba(16,185,129,.35);
      animation:pop .4s cubic-bezier(.175,.885,.32,1.275)
    }}
    @keyframes pop{{from{{transform:scale(0)}}to{{transform:scale(1)}}}}
    .toast-box{{
      display:none;position:fixed;top:20px;left:50%;transform:translateX(-50%);
      background:#1f2937;color:#fff;padding:12px 20px;border-radius:12px;font-size:13.5px;
      z-index:1000;box-shadow:0 10px 30px rgba(0,0,0,.25);max-width:90%;text-align:center;line-height:1.45
    }}
    .toast-box.warn{{background:#92400e;border:1px solid #f59e0b}}
    .toast-box.on{{display:block;animation:slideDown .3s ease}}
    @keyframes slideDown{{from{{top:0;opacity:0}}to{{top:20px;opacity:1}}}}
    .ctag{{
      color:var(--mb-navy);background:var(--mb-light);padding:3px 8px;
      border-radius:6px;border:1px solid var(--mb-border);font-family:monospace;font-size:14px;font-weight:700
    }}
    .test-box{{
      background:#fffbeb;border:1px dashed #f59e0b;border-radius:10px;padding:8px 12px;
      margin-top:10px;text-align:center;font-size:12px;color:#92400e
    }}
    .btn-sim{{
      background:#f59e0b;color:#fff;border:none;padding:6px 14px;border-radius:6px;
      font-size:12px;font-weight:700;cursor:pointer;margin-top:4px
    }}
    .btn-sim:hover{{background:#d97706}}
  </style>
</head>
<body>
  <div class="toast-box" id="toast"></div>
  <div class="ov" id="ov">
    <div id="ov-loading" style="display:flex;flex-direction:column;align-items:center;gap:12px">
      <div class="sp"></div>
      <p style="color:var(--mb-navy);font-weight:700;font-size:15px" id="ov-msg">Đang kết nối hệ thống MB Bank...</p>
    </div>
    <div id="ov-success" style="display:none;flex-direction:column;align-items:center;gap:12px">
      <div class="succ-icon">✓</div>
      <h2 style="color:#065f46;font-size:19px;font-weight:800">ĐÃ NHẬN THANH TOÁN MB BANK THÀNH CÔNG!</h2>
      <p style="color:#047857;font-size:14px">Hệ thống SePay đã tự động xác nhận số tiền. Đang chuyển hướng...</p>
    </div>
  </div>

  <div class="card">
    <div class="hdr">
      <div class="logo"><span class="ic">🏦</span><span class="br">MB BANK</span></div>
      <p>Chuyển khoản Ngân hàng TMCP Quân Đội (Tự động xác nhận)</p>
      <div class="radar-bar">
        <span class="dot"></span>
        <span id="live-txt">Lắng nghe biến động số dư tự động (3s/lần)</span>
      </div>
    </div>
    <div class="body">
      <div class="qr-wrap">
        <img src="{qr_img_src}" alt="MB Bank QR Code" class="qr-img"
             onerror="this.src='{vietqr_fallback}'" />
        <span class="qr-lbl">📱 Mở App MB Bank / Mọi ngân hàng → Quét mã → Chuyển khoản</span>
      </div>
      <table class="tbl">
        <tr>
          <td class="l">Ngân hàng</td>
          <td class="v">{safe_bank_name}</td>
        </tr>
        <tr>
          <td class="l">Số tài khoản</td>
          <td class="v">{safe_bank_account}
            <button class="cp" onclick="cp('{safe_bank_account}',this)">📋 Copy</button>
          </td>
        </tr>
        <tr>
          <td class="l">Chủ tài khoản</td>
          <td class="v">{safe_bank_owner}
            <button class="cp" onclick="cp('{safe_bank_owner}',this)">📋 Copy</button>
          </td>
        </tr>
        <tr>
          <td class="l">Số tiền</td>
          <td class="v amt">{amount_vnd}
            <button class="cp" onclick="cp('{amount}',this)">📋 Copy</button>
          </td>
        </tr>
        <tr>
          <td class="l">Nội dung CK</td>
          <td class="v"><span class="ctag">{safe_content}</span>
            <button class="cp" onclick="cp('{safe_content}',this)">📋 Copy</button>
          </td>
        </tr>
      </table>
    </div>
    <div class="ftr">
      <div class="auto-notice">
        <span>⚡</span>
        <div><strong>Hệ thống tự động xác nhận:</strong> Khi bạn hoàn tất chuyển tiền, SePay sẽ gửi webhook ngầm về hệ thống và màn hình sẽ <strong>tự động chuyển hướng ngay</strong> sau vài giây mà bạn không cần ấn xác nhận thủ công!</div>
      </div>
      <button class="btn-check" id="btn-check" onclick="checkPaymentStatus()">
        <span>🔄</span> <span>Kiểm tra trạng thái thanh toán ngay</span>
      </button>
      <a href="/" class="cancel">← Quay lại trang chủ / Hủy</a>

      {'''
      <div class="test-box">
        <div>🧪 <strong>Chế độ Kiểm thử (DEBUG):</strong></div>
        <div>Bấm nút bên dưới để giả lập hệ thống nhận tiền MB Bank thành công và xem luồng tự động nhảy trang:</div>
        <button class="btn-sim" onclick="simulatePay()">⚡ Giả lập nhận tiền MB Bank ngay</button>
      </div>
      ''' if is_debug else ''}
    </div>
  </div>

  <script>
    const orderId = "{safe_order_id}";
    let isSuccessHandled = false;

    function toast(msg, isWarn = true) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.className = 'toast-box ' + (isWarn ? 'warn' : '') + ' on';
      setTimeout(() => {{ t.classList.remove('on'); }}, 3800);
    }}

    function cp(t, b) {{
      navigator.clipboard.writeText(t).then(() => {{
        const o = b.textContent; b.textContent = '✓ Đã copy!'; b.classList.add('ok');
        setTimeout(() => {{ b.textContent = o; b.classList.remove('ok'); }}, 1800);
      }}).catch(() => {{
        const a = document.createElement('textarea'); a.value = t;
        a.style.position = 'fixed'; a.style.opacity = '0';
        document.body.appendChild(a); a.select(); document.execCommand('copy');
        document.body.removeChild(a);
        b.textContent = '✓ Đã copy!'; b.classList.add('ok');
        setTimeout(() => {{ b.textContent = '📋 Copy'; b.classList.remove('ok'); }}, 1800);
      }});
    }}

    function handlePaymentSuccess(redirectUrl) {{
      if (isSuccessHandled) return;
      isSuccessHandled = true;
      clearInterval(pollInterval);
      document.getElementById('ov-loading').style.display = 'none';
      document.getElementById('ov-success').style.display = 'flex';
      document.getElementById('ov').classList.add('on');
      setTimeout(() => {{
        window.location.href = redirectUrl || ('{target_success_url}');
      }}, 1200);
    }}

    // Polling tự động mỗi 3 giây kiểm tra trạng thái thanh toán MB Bank
    const pollInterval = setInterval(async () => {{
      if (isSuccessHandled) return;
      try {{
        const res = await fetch('/api/orders/' + encodeURIComponent(orderId) + '/status');
        if (!res.ok) return;
        const d = await res.json();
        if (d && (d.is_paid || d.payment_status === 'paid')) {{
          handlePaymentSuccess(d.redirect_url);
        }}
      }} catch (err) {{}}
    }}, 3000);

    // Khách bấm nút chủ động kiểm tra
    async function checkPaymentStatus() {{
      if (isSuccessHandled) return;
      const btn = document.getElementById('btn-check');
      btn.disabled = true;
      btn.innerHTML = '<span>⏳</span> <span>Đang đối soát biến động số dư...</span>';

      try {{
        const res = await fetch('/api/orders/' + encodeURIComponent(orderId) + '/confirm-mbank', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ order_id: orderId }})
        }});
        const d = await res.json();

        if (d && d.is_paid) {{
          handlePaymentSuccess(d.redirect_url);
        }} else {{
          toast(d.message || 'Hệ thống chưa ghi nhận tiền vào tài khoản MB Bank. Vui lòng hoàn tất chuyển khoản đúng cú pháp và đợi trong giây lát!', true);
          btn.disabled = false;
          btn.innerHTML = '<span>🔄</span> <span>Kiểm tra trạng thái thanh toán ngay</span>';
        }}
      }} catch (e) {{
        toast('Lỗi kết nối kiểm tra thanh toán. Vui lòng thử lại sau giây lát!', true);
        btn.disabled = false;
        btn.innerHTML = '<span>🔄</span> <span>Kiểm tra trạng thái thanh toán ngay</span>';
      }}
    }}

    async function simulatePay() {{
      document.getElementById('ov-msg').textContent = 'Đang kích hoạt giả lập MB Bank nhận tiền...';
      document.getElementById('ov').classList.add('on');
      try {{
        const res = await fetch('/api/payment/mbank/simulate-pay/' + encodeURIComponent(orderId), {{
          method: 'POST'
        }});
        const d = await res.json();
        if (d && d.success) {{
          handlePaymentSuccess(d.redirect_url);
        }} else {{
          document.getElementById('ov').classList.remove('on');
          alert('Lỗi giả lập: ' + (d.message || 'Không thành công'));
        }}
      }} catch (err) {{
        document.getElementById('ov').classList.remove('on');
        alert('Lỗi kích hoạt test');
      }}
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content, media_type="text/html; charset=utf-8")


@router.get("/api/orders/{order_id}/status")
@router.get("/api/payment/mbank/status/{order_id}")
@router.get("/api/payment/momo/status/{order_id}")
def get_order_payment_status_realtime(order_id: str, request: Request):
    """API kiểm tra trạng thái thanh toán đơn hàng theo thời gian thực (hỗ trợ Frontend Polling 3s/lần)."""
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    is_paid = (order.get("payment_status") == "paid")
    phone_val = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    phone_param = f"?phone={urllib.parse.quote(phone_val)}" if phone_val else ""
    return {
        "success": True,
        "order_id": order_id,
        "payment_status": order.get("payment_status", "unpaid"),
        "order_status": order.get("order_status") or order.get("status", "confirmed"),
        "is_paid": is_paid,
        "redirect_url": f"/order-success/{order_id}{phone_param}",
    }


@router.post("/api/orders/{order_id}/confirm-mbank")
@router.post("/api/orders/{order_id}/confirm-momo")
def confirm_mbank_payment(order_id: str, request: Request):
    """
    Khách bấm 'Kiểm tra trạng thái thanh toán MB Bank'.
    - Nếu THỰC SỰ đã nhận được tiền (paid): trả về redirect_url sang order-success.
    - Nếu CHƯA nhận được tiền (unpaid): TUYỆT ĐỐI không cho nhảy sang order-success, thông báo chờ đối soát.
    """
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    phone_val = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    phone_param = f"?phone={urllib.parse.quote(phone_val)}" if phone_val else ""

    if order.get("payment_status") == "paid":
        return {
            "success": True,
            "is_paid": True,
            "order_id": order_id,
            "status": "paid",
            "message": "Thanh toán MB Bank đã được hệ thống ghi nhận thành công!",
            "redirect_url": f"/order-success/{order_id}{phone_param}",
        }

    # Chưa thanh toán: ghi chú lại lượt bấm kiểm tra, trả về is_paid: false
    try:
        import datetime as _dt
        note_time = _dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        new_note = f"[{note_time}] Khách ấn kiểm tra MB Bank — Hệ thống chưa ghi nhận biến động số dư SePay."
        existing_note = order.get("internal_note") or ""
        combined_note = f"{existing_note}\n{new_note}".strip() if existing_note else new_note
        db_service.update_order_internal_note(order_id, combined_note)
    except Exception as exc:
        logger.warning("Could not update order internal note for %s: %s", order_id, exc)

    return {
        "success": False,
        "is_paid": False,
        "order_id": order_id,
        "status": "awaiting_payment",
        "message": (
            "Hệ thống chưa ghi nhận tiền vào tài khoản MB Bank cho đơn hàng này. "
            "Nếu bạn vừa chuyển tiền, SePay sẽ gửi thông báo và "
            "trang sẽ TỰ ĐỘNG CHUYỂN HƯỚNG ngay khi nhận được tiền!"
        ),
        "redirect_url": None,
    }


@router.post("/api/payment/mbank/simulate-pay/{order_id}")
@router.post("/api/payment/momo/simulate-pay/{order_id}")
def simulate_mbank_payment(order_id: str, request: Request):
    """
    Kích hoạt giả lập nhận tiền MB Bank thành công cho đơn hàng (hỗ trợ test luồng tự động).
    Chỉ cho phép khi DEBUG=True hoặc user là admin.
    """
    if not settings.DEBUG:
        user = get_current_user_optional(request)
        if not user or user.role != "admin":
            raise HTTPException(status_code=403, detail="Chỉ cho phép trong môi trường phát triển hoặc bởi Quản trị viên")

    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    tx_code = f"MBANK-SIM-{uuid.uuid4().hex[:8].upper()}"
    res = db_service.confirm_payment(
        order_id=order_id,
        amount=amount,
        transaction_code=tx_code,
        payment_channel="mbank",
    )
    phone_val = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
    phone_param = f"?phone={urllib.parse.quote(phone_val)}" if phone_val else ""

    return {
        "success": True,
        "order_id": order_id,
        "status": str(res),
        "message": "Đã ghi nhận thanh toán MB Bank thành công!",
        "redirect_url": f"/order-success/{order_id}{phone_param}",
    }


def _extract_order_id_from_sepay_text(text: str) -> Optional[str]:
    """
    Tách mã đơn hàng từ nội dung chuyển khoản SePay.
    Hỗ trợ các mẫu:
    - AURA-261020-ABCDEF123456 (chuẩn hiện tại)
    - AURA-[Mã_Đơn] hoặc AURA [Mã_Đơn]
    - Mã đơn độc lập trong database
    """
    if not text:
        return None

    # 1. Định dạng chuẩn: AURA-YYMMDD-HEX (ví dụ: AURA-261020-A1B2C3D4E5F6)
    m1 = re.search(r"AURA[-_][0-9]{6}[-_][A-Za-z0-9]+", text, re.IGNORECASE)
    if m1:
        candidate = m1.group(0).upper().replace("_", "-")
        if db_service.get_order_by_id(candidate) or order_service.get_order_by_id(candidate):
            return candidate

    # 2. Định dạng tổng quát AURA-[Mã_Đơn] hoặc AURA [Mã_Đơn]
    m2 = re.search(r"AURA[-_ ]+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if m2:
        candidate_code = m2.group(1).strip().upper()
        if db_service.get_order_by_id(candidate_code) or order_service.get_order_by_id(candidate_code):
            return candidate_code
        with_prefix = f"AURA-{candidate_code}" if not candidate_code.startswith("AURA-") else candidate_code
        if db_service.get_order_by_id(with_prefix) or order_service.get_order_by_id(with_prefix):
            return with_prefix
        return with_prefix

    # 3. Quét từng từ trong nội dung tìm AURA
    words = text.strip().split()
    for i, w in enumerate(words):
        if w.upper() == "AURA" and i + 1 < len(words):
            next_word = words[i + 1].strip().upper()
            if db_service.get_order_by_id(next_word):
                return next_word
            if db_service.get_order_by_id(f"AURA-{next_word}"):
                return f"AURA-{next_word}"
            return f"AURA-{next_word}" if not next_word.startswith("AURA-") else next_word

    return None


@router.post("/api/payment/sepay/webhook")
@router.post("/api/payment/momo/webhook")
async def sepay_webhook_handler(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    API hứng dữ liệu Webhook biến động số dư từ SePay (sepay.vn).
    Nhiệm vụ:
    1. Nhận dữ liệu POST dạng JSON từ SePay (gateway, transactionDate, accountNumber, transferAmount, transferType, content, referenceCode,...).
    2. Kiểm tra nội dung chuyển khoản (content / description) để tách lấy mã đơn hàng.
    3. Cập nhật trạng thái đơn hàng trong Database từ 'Chờ thanh toán' sang 'Đã thanh toán' (Paid).
    4. Trả về phản hồi JSON {"success": true} cho SePay.
    """
    # 0. Kiểm tra API Key nếu hệ thống có cấu hình SEPAY_API_KEY
    if getattr(settings, "SEPAY_API_KEY", None) and settings.SEPAY_API_KEY.strip():
        expected_key = settings.SEPAY_API_KEY.strip()
        auth_header = authorization or request.headers.get("Authorization") or ""
        if not auth_header or (expected_key not in auth_header and f"Apikey {expected_key}" != auth_header):
            logger.warning("SePay webhook rejected: Invalid Authorization header")
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid SePay API Key")

    try:
        raw_body = await request.body()
        data = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except Exception as e:
        logger.warning("Lỗi đọc payload SePay webhook: %s", e)
        raise HTTPException(status_code=400, detail="Dữ liệu payload webhook không hợp lệ (yêu cầu JSON)") from e

    logger.info("Nhận Webhook từ SePay: %s", data)

    # 1. Trích xuất các trường theo đặc tả SePay
    gateway = data.get("gateway") or "SePay"
    account_number = data.get("accountNumber") or ""
    transfer_type = (data.get("transferType") or "in").strip().lower()
    content = data.get("content") or ""
    description = data.get("description") or ""
    reference_code = data.get("referenceCode") or data.get("id") or f"SP-{uuid.uuid4().hex[:8].upper()}"

    # Bỏ qua nếu là giao dịch tiền ra (transferType = "out")
    if transfer_type == "out":
        return {
            "success": True,
            "message": "Bỏ qua giao dịch tiền ra (transferType=out)",
        }

    # Số tiền chuyển khoản
    raw_amount = data.get("transferAmount") if data.get("transferAmount") is not None else data.get("amount")
    try:
        transfer_amount = int(raw_amount)
    except (ValueError, TypeError):
        transfer_amount = 0

    if transfer_amount <= 0:
        return {
            "success": False,
            "message": "Số tiền giao dịch không hợp lệ (phải lớn hơn 0)",
        }

    # 2. Tách lấy mã đơn hàng từ nội dung chuyển khoản (content / description / order_id / code)
    order_id = _extract_order_id_from_sepay_text(content) or _extract_order_id_from_sepay_text(description)
    if not order_id and data.get("order_id"):
        order_id = str(data.get("order_id")).strip().upper()
    if not order_id and data.get("code"):
        order_id = _extract_order_id_from_sepay_text(str(data.get("code")))

    if not order_id:
        logger.warning("SePay webhook không tìm thấy mã đơn AURA trong content: '%s'", content)
        return {
            "success": False,
            "message": "Không tìm thấy mã đơn hàng AURA trong nội dung chuyển khoản",
            "content": content,
        }

    # 3. Tìm đơn hàng trong Database
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        logger.warning("SePay webhook: Không tìm thấy đơn hàng '%s' trong Database", order_id)
        return {
            "success": False,
            "order_id": order_id,
            "message": f"Không tìm thấy đơn hàng '{order_id}' trong hệ thống",
        }

    # 4. Cập nhật trạng thái đơn hàng sang Đã thanh toán (Paid)
    tx_code = str(reference_code)
    confirm_res = db_service.confirm_payment(
        order_id=order_id,
        amount=transfer_amount,
        transaction_code=tx_code,
        payment_channel=f"sepay_{gateway.lower()}" if gateway else "sepay",
    )

    now_str = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    if confirm_res == "invalid_amount":
        expected_amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
        logger.warning(
            "SePay webhook: Số tiền không khớp cho đơn %s (nhận: %s, cần: %s)",
            order_id, transfer_amount, expected_amount,
        )
        return {
            "success": False,
            "order_id": order_id,
            "message": f"Số tiền thanh toán ({transfer_amount}đ) không khớp giá trị đơn ({expected_amount}đ)",
        }
    elif confirm_res in ("cancelled", "refund_pending"):
        return {
            "success": True,
            "order_id": order_id,
            "status": "refund_pending",
            "message": "Đơn hàng đã hủy trước đó, đã ghi nhận yêu cầu hoàn tiền",
        }
    elif confirm_res == "already_paid":
        return {
            "success": True,
            "order_id": order_id,
            "status": "already_paid",
            "message": "Đơn hàng đã được xác nhận thanh toán trước đó (idempotent)",
        }

    # Ghi nhận ghi chú nội bộ chi tiết giao dịch SePay
    note_msg = (
        f"[{now_str}] ĐÃ NHẬN TIỀN THẬT QUA SEPAY ({gateway}) - "
        f"Số tiền: {transfer_amount:,}đ - STK: {account_number} - Mã GD: {tx_code}"
    )
    existing_note = order.get("internal_note") or ""
    combined_note = f"{existing_note}\n{note_msg}".strip() if existing_note else note_msg
    db_service.update_order_internal_note(order_id, combined_note)

    logger.info("SePay Webhook xác nhận thành công đơn %s - %s đ", order_id, transfer_amount)

    # 5. Trả về phản hồi JSON {"success": true} cho SePay
    return {
        "success": True,
        "order_id": order_id,
        "payment_status": "paid",
        "gateway": gateway,
        "transfer_amount": transfer_amount,
        "transaction_code": tx_code,
        "message": f"Đã cập nhật đơn hàng {order_id} sang trạng thái Đã thanh toán (Paid) thành công!",
    }
