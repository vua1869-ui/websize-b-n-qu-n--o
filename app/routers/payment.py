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
from app.services.mb_paygate_service import MBPaygateError, mb_paygate_service
from app.services.order_service import order_service

logger = logging.getLogger("aura.payment")

router = APIRouter(tags=["payment"])


class MBPaygateCreatePaymentRequest(BaseModel):
    order_id: str
    phone: Optional[str] = None


@router.post("/api/payment/mb-paygate/create-order")
def mb_paygate_create_order(body: MBPaygateCreatePaymentRequest, request: Request):
    order = db_service.get_order_by_id(body.order_id)
    if not order:
        order = order_service.get_order_by_id(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

    user = get_current_user_optional(request)
    phone = body.phone or request.cookies.get(f"aura_order_{body.order_id}")
    allowed, _ = verify_order_access(order, user, phone)
    if not allowed:
        raise HTTPException(status_code=403, detail="Không có quyền thanh toán đơn hàng này")
    if order.get("payment_status") == "paid":
        raise HTTPException(status_code=409, detail="Đơn hàng đã thanh toán")

    amount = int(order.get("total_amount") or 0)
    try:
        client_ip = request.client.host if request.client else ""
        result = mb_paygate_service.create_order(body.order_id, amount, client_ip)
    except MBPaygateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    error_code = str(result.get("error_code", result.get("response_code", result.get("code", ""))))
    if error_code not in ("", "0", "00", "000"):
        raise HTTPException(status_code=502, detail="MB Paygate từ chối tạo giao dịch")
    return {
        "success": True,
        "payment_url": result.get("qr_url") or result.get("payment_url"),
        "qr_string": result.get("QrString") or result.get("qr_string"),
        "order_id": body.order_id,
    }


@router.post("/api/payment/mb-paygate/ipn")
async def mb_paygate_ipn(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        data = dict(urllib.parse.parse_qsl(raw.decode("utf-8", errors="ignore"), keep_blank_values=True))
    if not isinstance(data, dict) or not mb_paygate_service.verify(data):
        raise HTTPException(status_code=401, detail="Chữ ký MB Paygate không hợp lệ")

    order_id = str(
        data.get("order_reference")
        or data.get("merchant_order_reference")
        or data.get("pg_order_reference")
        or ""
    )
    transaction_code = str(data.get("pg_transaction_number") or data.get("transaction_reference_id") or "")
    try:
        amount = int(data.get("pg_amount", data.get("amount", 0)))
    except (TypeError, ValueError):
        amount = 0

    success = str(data.get("error_code", "")) in ("0", "00", "000")
    if not order_id or not transaction_code:
        raise HTTPException(status_code=400, detail="Thiếu mã đơn hoặc mã giao dịch MB")
    if not success:
        return {"success": True, "status": "payment_failed"}

    result = db_service.confirm_payment(
        order_id,
        amount,
        transaction_code,
        "mb_paygate",
        {
            "transaction_reference_id": transaction_code,
            "pg_paytime": data.get("pg_paytime"),
            "pg_order_reference": data.get("pg_order_reference"),
            "ipn": data,
        },
    )
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")
    if result == "invalid_amount":
        raise HTTPException(status_code=400, detail="Số tiền giao dịch không khớp")
    return {"success": True, "status": str(result), "order_id": order_id}


@router.get("/api/payment/mb-paygate/return")
def mb_paygate_return(order_reference: str = Query("")):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", order_reference):
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url=f"/order-success/{urllib.parse.quote(order_reference)}", status_code=303)


@router.get("/api/payment/mb-paygate/cancel")
def mb_paygate_cancel(order_reference: str = Query("")):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", order_reference):
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(
        url=f"/order-success/{urllib.parse.quote(order_reference)}?payment_cancelled=1",
        status_code=303,
    )


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


@router.get("/payment/mbank/{order_id}", response_class=HTMLResponse)
def mbank_payment_page(order_id: str, request: Request):
    """Trang Chuyển khoản Ngân hàng MB Bank (Tự động xác nhận) — Polling 3s/lần và tự chuyển hướng khi SePay báo paid."""
    order = db_service.get_order_by_id(order_id)
    if not order:
        order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    bank_name = settings.MBBANK_BANK_NAME.strip()
    bank_account = settings.MBBANK_ACCOUNT_NUMBER.strip()
    bank_owner = settings.MBBANK_ACCOUNT_NAME.strip()
    mb_qr_static = settings.MBBANK_QR_IMAGE.strip()
    bank_code = settings.MBBANK_BANK_CODE.strip()
    if not bank_account or not bank_owner:
        raise HTTPException(status_code=503, detail="Thông tin tài khoản MB Bank chưa được cấu hình")

    amount = int(order.get("total_amount") or order.get("quote", {}).get("total", 0))
    transfer_content = str(order_id).upper() if str(order_id).upper().startswith("AURA-") else f"AURA-{order_id}"

    safe_order_id = html.escape(str(order_id))
    amount_vnd = f"{amount:,}".replace(",", ".") + "đ"
    safe_bank_name = html.escape(bank_name)
    safe_bank_account = html.escape(bank_account)
    safe_bank_owner = html.escape(bank_owner)
    safe_content = html.escape(transfer_content)

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
            ]
            has_static = any(os.path.isfile(cp) for cp in candidate_paths)

    vietqr_fallback = (
        f"https://img.vietqr.io/image/{urllib.parse.quote(bank_code)}-{urllib.parse.quote(bank_account)}"
        f"-compact2.png?amount={amount}&addInfo={urllib.parse.quote(transfer_content)}"
        f"&accountName={urllib.parse.quote(bank_owner)}"
    )
    qr_img_src = html.escape(mb_qr_static) if has_static else html.escape(vietqr_fallback)
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
             onerror="this.onerror=null;this.src='{html.escape(vietqr_fallback)}'" />
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
