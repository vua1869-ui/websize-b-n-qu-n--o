"""Toàn bộ logic tiền bạc nằm ở đây, và CHỈ chạy trên server.

Client chỉ gửi (product_id, size, color, quantity). Giá, khuyến mãi, phí ship
đều do server tính lại, nên khách không thể tự sửa giá trong trình duyệt.
"""
import datetime
import hashlib
import hmac
import json
import os
import threading
import uuid
from typing import Dict, List, Optional

from app.config import settings
from app.models.schemas import (
    OrderCreateRequest, OrderItem, OrderResponse, QuoteLine, QuoteResponse,
)
from app.services.product_service import product_service

DEFAULT_ORDERS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "orders.jsonl"
)


class OrderError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def make_combo_token(product_ids: List[str]) -> str:
    """Chữ ký xác nhận 'bộ đồ này do AI Stylist phối' -> được giảm giá combo."""
    payload = "|".join(sorted(set(product_ids))).encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()[:20]


def _money(n: int) -> str:
    return f"{n:,}".replace(",", ".") + "đ"


class OrderService:
    def __init__(self, orders_path: str = DEFAULT_ORDERS_PATH):
        self.orders_path = orders_path
        self._lock = threading.Lock()
        self._restore_stock_from_history()

    # ---------- Tính giá ----------
    def build_quote(self, items: List[OrderItem], voucher_code: Optional[str] = None,
                    strict_voucher: bool = False) -> QuoteResponse:
        lines: List[QuoteLine] = []
        qty_by_product: Dict[str, int] = {}

        for it in items:
            p = product_service.get_by_id(it.product_id)
            if not p:
                raise OrderError(f"Sản phẩm '{it.product_id}' không tồn tại", 400)
            if it.size not in p.sizes:
                raise OrderError(f"Size '{it.size}' không có cho '{p.name}'", 400)
            if it.color not in [c.name for c in p.colors]:
                raise OrderError(f"Màu '{it.color}' không có cho '{p.name}'", 400)
            if it.quantity > settings.MAX_QTY_PER_LINE:
                raise OrderError(
                    f"Mỗi phân loại chỉ mua tối đa {settings.MAX_QTY_PER_LINE} sản phẩm", 400)

            qty_by_product[p.id] = qty_by_product.get(p.id, 0) + it.quantity
            lines.append(QuoteLine(
                product_id=p.id, name=p.name, image=p.images[0], size=it.size,
                color=it.color, quantity=it.quantity, unit_price=p.final_price,
                line_total=p.final_price * it.quantity, combo=False,
            ))

        for pid, qty in qty_by_product.items():
            p = product_service.get_by_id(pid)
            if qty > p.stock:
                msg = (f"'{p.name}' chỉ còn {p.stock} sản phẩm" if p.stock > 0
                       else f"'{p.name}' đã hết hàng")
                raise OrderError(msg, 409)

        subtotal = sum(l.line_total for l in lines)

        # ---- Combo phối đồ: chỉ giảm nếu token khớp chữ ký của đúng bộ sản phẩm trong giỏ ----
        combo_discount = 0
        groups: Dict[str, List[int]] = {}
        for idx, it in enumerate(items):
            if it.combo_token:
                groups.setdefault(it.combo_token, []).append(idx)
        for token, idxs in groups.items():
            ids = {lines[i].product_id for i in idxs}
            if len(ids) >= 2 and make_combo_token(list(ids)) == token:
                group_total = sum(lines[i].line_total for i in idxs)
                combo_discount += group_total * settings.COMBO_DISCOUNT_PERCENT // 100
                for i in idxs:
                    lines[i].combo = True

        after_combo = subtotal - combo_discount

        # ---- Voucher ----
        voucher_discount = 0
        voucher_message: Optional[str] = None
        applied_code: Optional[str] = None
        voucher = None
        code = (voucher_code or "").strip().upper()
        if code:
            voucher = product_service.get_voucher(code)
            if not voucher:
                voucher_message = f"Mã '{code}' không tồn tại"
            elif after_combo < voucher.min_order:
                voucher_message = f"Mã {code} áp dụng cho đơn từ {_money(voucher.min_order)}"
                voucher = None
            else:
                applied_code = voucher.code
            if voucher_message and strict_voucher:
                raise OrderError(voucher_message, 400)

        # ---- Phí vận chuyển ----
        shipping_fee = 0 if after_combo >= settings.FREE_SHIPPING_THRESHOLD else settings.SHIPPING_FEE
        shipping_discount = 0

        if voucher:
            if voucher.kind == "amount":
                voucher_discount = min(voucher.value, after_combo)
            elif voucher.kind == "percent":
                voucher_discount = after_combo * voucher.value // 100
                if voucher.max_discount:
                    voucher_discount = min(voucher_discount, voucher.max_discount)
            elif voucher.kind == "shipping":
                shipping_discount = shipping_fee
                if shipping_fee == 0:
                    voucher_message = "Đơn của bạn đã được miễn phí vận chuyển sẵn"
            if voucher_message is None:
                voucher_message = f"Đã áp dụng mã {voucher.code}"

        total = after_combo - voucher_discount + shipping_fee - shipping_discount
        return QuoteResponse(
            lines=lines, subtotal=subtotal, combo_discount=combo_discount,
            voucher_code=applied_code, voucher_discount=voucher_discount,
            voucher_message=voucher_message, shipping_fee=shipping_fee,
            shipping_discount=shipping_discount, total=max(0, total),
        )

    # ---------- Tạo đơn ----------
    def create_order(self, req: OrderCreateRequest) -> OrderResponse:
        quote = self.build_quote(req.items, req.voucher_code, strict_voucher=True)

        needed: Dict[str, int] = {}
        for line in quote.lines:
            needed[line.product_id] = needed.get(line.product_id, 0) + line.quantity
        stock_error = product_service.reserve_stock(needed)
        if stock_error:
            raise OrderError(stock_error, 409)

        now = datetime.datetime.now()
        order_id = f"AURA-{now.strftime('%y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        status = "confirmed" if req.payment_method == "cod" else "pending_payment"
        record = {
            "order_id": order_id, "status": status,
            "created_at": now.isoformat(timespec="seconds"),
            "customer": {"name": req.customer_name, "phone": req.customer_phone,
                         "address": req.customer_address, "note": req.customer_note},
            "payment_method": req.payment_method,
            "quote": quote.model_dump(),
        }
        try:
            self._append(record)
        except OSError:
            product_service.release_stock(needed)  # hoàn kho nếu không ghi được đơn
            raise OrderError("Không thể lưu đơn hàng lúc này, vui lòng thử lại", 500)

        if req.payment_method == "cod":
            msg = "Đặt hàng thành công! Chúng tôi sẽ liên hệ xác nhận và giao hàng sớm nhất."
        else:
            msg = (f"Đã ghi nhận đơn. Vui lòng chuyển khoản {_money(quote.total)} với nội dung "
                   f"'{order_id}' để shop xác nhận đơn.")
        return OrderResponse(
            order_id=order_id, status=status, quote=quote,
            customer_name=req.customer_name, customer_phone=req.customer_phone,
            customer_address=req.customer_address, payment_method=req.payment_method,
            created_at=now.strftime("%d/%m/%Y %H:%M"), message=msg,
        )

    # ---------- Lưu trữ ----------
    def _append(self, record: dict):
        os.makedirs(os.path.dirname(self.orders_path), exist_ok=True)
        with self._lock:
            with open(self.orders_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _restore_stock_from_history(self):
        """Khởi động lại server không làm 'hồi' lại hàng đã bán."""
        if not os.path.exists(self.orders_path):
            return
        with open(self.orders_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("status") == "cancelled":
                        continue
                    for l in rec["quote"]["lines"]:
                        product_service.apply_historical_sale(l["product_id"], l["quantity"])
                except (ValueError, KeyError):
                    continue  # bỏ qua dòng hỏng, không làm sập server

    def count_orders(self) -> int:
        if not os.path.exists(self.orders_path):
            return 0
        with open(self.orders_path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())


order_service = OrderService()
