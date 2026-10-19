import datetime as dt
import hashlib
import hmac
import html
import json
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
    get_current_user_optional,
    normalize_phone,
    require_admin,
    verify_order_access,
)
from app.services.momo_service import momo_service
from app.services.order_service import order_service
from app.services.vnpay_service import get_vnpay_response_message, vnpay_service

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
        # Đơn vãng lai: Admin có quyền xem, người dùng khác bắt buộc cung cấp SĐT khớp với đơn
        if not (user and user.role == "admin"):
            cookie_phone = request.cookies.get(f"aura_order_{order_id}")
            check_phone = phone or cookie_phone
            if not check_phone:
                raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại hoặc cần cung cấp số điện thoại xác thực")
            order_phone = order.get("customer_phone") or order.get("customer", {}).get("phone", "")
            if normalize_phone(check_phone) != normalize_phone(order_phone):
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

    # Kiểm tra trạng thái đơn hàng
    if order.get("payment_status") == "paid":
        raise HTTPException(status_code=400, detail="Đơn hàng này đã được thanh toán")

    if order.get("status") == "cancelled" or order.get("order_status") == "cancelled":
        raise HTTPException(status_code=400, detail="Đơn hàng đã bị hủy, không thể tạo link thanh toán")

    # Kiểm tra quyền sở hữu đơn hàng
    user = get_current_user_optional(request)
    cookie_phone = request.cookies.get(f"aura_order_{body.order_id}")
    phone_to_check = body.phone or cookie_phone
    is_allowed, _ = verify_order_access(order, user, phone_to_check)
    if not is_allowed:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập hoặc thanh toán đơn hàng này")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))

    # Xử lý IP client chuẩn xác kể cả khi chạy sau reverse proxy (X-Forwarded-For)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    elif request.client and request.client.host:
        client_ip = request.client.host
    else:
        client_ip = "127.0.0.1"

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


def _payment_error_html(
    title: str,
    body_html: str,
    status: int = 400,
    back_url: str = "/",
    retry_url: Optional[str] = None,
    retry_text: str = "Thử thanh toán lại",
) -> HTMLResponse:
    """Helper render trang lỗi thanh toán có charset UTF-8 chuẩn, tránh lỗi encoding tiếng Việt."""
    retry_btn = (
        f'<a href="{html.escape(retry_url)}" class="btn" style="background:#005baa;margin-right:8px;">{html.escape(retry_text)}</a>'
        if retry_url
        else ""
    )
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
    h2{{font-size:20px;font-weight:700;color:#111;margin-bottom:12px}}
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
    {body_html}
    <div style="margin-top: 24px;">
      {retry_btn}
      <a href="{html.escape(back_url)}" class="btn btn-outline">Về trang chủ</a>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=content, status_code=status, media_type="text/html; charset=utf-8")


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
                resp.set_cookie(f"aura_order_{order_id}", phone_val, httponly=True, max_age=3600, samesite="lax")
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
    cookie_phone = request.cookies.get(f"aura_order_{body.order_id}")
    phone_to_check = body.phone or cookie_phone
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
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=2|99|0987654321|||0|0|{amount}|AURA%20{body.order_id}|transfer_myqr"
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
    qr_src = f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=2|99|0987654321|||0|0|{amount}|AURA%20{order_id}|transfer_myqr"

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
            return HTMLResponse(
                content="""
                <div style="font-family:sans-serif;text-align:center;padding:50px;">
                    <h2 style="color:#e11d48;">Lỗi bảo mật thanh toán</h2>
                    <p>Chữ ký bảo mật MoMo không hợp lệ hoặc dữ liệu phản hồi bị can thiệp.</p>
                    <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                </div>
                """,
                status_code=400,
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

    if result_code == "0":
        confirm_res = db_service.confirm_payment(
            order_id=order_id,
            amount=amount,
            transaction_code=str(trans_id),
            payment_channel="momo",
        )
        if confirm_res in ("cancelled", "refund_pending"):
            if is_browser_html:
                return HTMLResponse(
                    content=f"""
                    <div style="font-family:sans-serif;text-align:center;padding:50px;">
                        <h2 style="color:#e11d48;">Đơn hàng đã bị hủy</h2>
                        <p>Đơn hàng <strong>{safe_order_id}</strong> đã bị hủy trước đó. Hệ thống đã ghi nhận thanh toán chờ hoàn tiền cho quý khách.</p>
                        <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                    </div>
                    """,
                    status_code=200,
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
                resp.set_cookie(f"aura_order_{order_id}", phone_val, httponly=True, max_age=3600, samesite="lax")
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
            return HTMLResponse(
                content=f"""
                <div style="font-family:sans-serif;text-align:center;padding:50px;">
                    <h2 style="color:#d97706;">Giao dịch chưa hoàn tất</h2>
                    <p>Mã đơn hàng: <strong>{safe_order_id}</strong></p>
                    <p>Mã phản hồi MoMo: <strong>{safe_result_code}</strong></p>
                    <p>Giao dịch thanh toán qua MoMo chưa thành công hoặc bạn đã hủy thao tác.</p>
                    <a href="/" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#000;color:#fff;text-decoration:none;border-radius:8px;">Về trang chủ</a>
                </div>
                """
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

