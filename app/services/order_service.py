"""Toàn bộ logic tiền bạc nằm ở đây, và CHỈ chạy trên server.

Client chỉ gửi (product_id, size, color, quantity). Giá, khuyến mãi, phí ship
đều do server tính lại, nên khách không thể tự sửa giá trong trình duyệt.
Tất cả dữ liệu đơn hàng được lưu trữ và truy vấn qua SQLAlchemy ORM (Database ACID).
"""
import datetime
import hashlib
import hmac
import json
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_

from app.config import settings
from app.db.database import db_service
from app.db.models import OrderDB, OrderItemDB
from app.db.session import get_db_session
from app.models.schemas import (
    OrderCreateRequest, OrderItem, OrderResponse, QuoteLine, QuoteResponse,
)
from app.services.product_service import product_service


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
    def __init__(self, orders_path: Optional[str] = None):
        self.orders_path = orders_path
        self._lock = threading.Lock()
        self._ensure_demo_orders()
        self._restore_stock_from_history()

    def _ensure_demo_orders(self):
        """Đảm bảo CSDL có đơn hàng (tự động migration từ orders.jsonl nếu bảng orders trống)."""
        try:
            with get_db_session() as session:
                count = session.query(func.count(OrderDB.order_id)).scalar()
                if count == 0:
                    from scripts.migrate_to_db import run_migration
                    run_migration()
        except Exception:
            pass

    # ---------- Tính giá ----------
    def build_quote(self, items: List[OrderItem], voucher_code: Optional[str] = None,
                    strict_voucher: bool = False, use_points: int = 0,
                    user: Optional[Any] = None) -> QuoteResponse:
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
        applied_voucher_code: Optional[str] = None

        if voucher_code:
            code = voucher_code.strip().upper()
            v = product_service.get_voucher(code)
            if not v:
                if strict_voucher:
                    raise OrderError(f"Mã giảm giá '{code}' không tồn tại hoặc đã hết hạn", 400)
                voucher_message = f"Mã '{code}' không tồn tại hoặc đã hết hạn"
            elif after_combo < v.min_order:
                msg = f"Mã {code} chỉ áp dụng cho đơn từ {_money(v.min_order)}"
                if strict_voucher:
                    raise OrderError(msg, 400)
                voucher_message = msg
            else:
                applied_voucher_code = code
                if v.kind == "amount":
                    voucher_discount = min(v.value, after_combo)
                    voucher_message = f"Đã áp dụng mã {code}: giảm {_money(voucher_discount)}"
                elif v.kind == "percent":
                    raw = after_combo * v.value // 100
                    if v.max_discount:
                        raw = min(raw, v.max_discount)
                    voucher_discount = min(raw, after_combo)
                    voucher_message = f"Đã áp dụng mã {code}: giảm {v.value}% ({_money(voucher_discount)})"
                elif v.kind == "shipping":
                    pass

        # ---- Phí vận chuyển ----
        shipping_fee = 0 if subtotal >= settings.FREE_SHIPPING_THRESHOLD else settings.SHIPPING_FEE
        shipping_discount = 0

        # Đặc quyền VIP AURA Club: Miễn phí vận chuyển 100% cho hội viên Gold/Diamond
        user_tier = getattr(user, "tier", "Silver") if user else "Silver"
        if user and user_tier.lower() in ["gold", "diamond"]:
            shipping_discount = shipping_fee
        elif applied_voucher_code:
            v = product_service.get_voucher(applied_voucher_code)
            if v and v.kind == "shipping":
                shipping_discount = min(v.value if v.value > 0 else shipping_fee, shipping_fee)
                voucher_message = f"Đã áp dụng mã {applied_voucher_code}: miễn/giảm phí ship"

        # ---- Điểm thưởng AURA Loyalty Club ----
        points_used = 0
        points_discount = 0
        if use_points > 0 and user:
            user_points = getattr(user, "points_balance", 0) or 0
            if use_points > user_points:
                raise OrderError(f"Bạn chỉ có {user_points} điểm tích lũy, không đủ {use_points} điểm", 400)
            max_payable = max(0, after_combo - voucher_discount)
            max_points_allowed = max_payable // 1000
            points_to_apply = min(use_points, max_points_allowed)
            points_used = points_to_apply
            points_discount = points_to_apply * 1000

        total = max(0, after_combo - voucher_discount - points_discount) + shipping_fee - shipping_discount

        # Tích điểm cho đơn hàng: 1 điểm cho mỗi 100.000đ thanh toán
        net_spent = max(0, after_combo - voucher_discount - points_discount)
        points_earned = net_spent // 100000

        return QuoteResponse(
            lines=lines,
            subtotal=subtotal,
            combo_discount=combo_discount,
            voucher_code=applied_voucher_code,
            voucher_discount=voucher_discount,
            voucher_message=voucher_message,
            shipping_fee=shipping_fee,
            shipping_discount=shipping_discount,
            points_used=points_used,
            points_discount=points_discount,
            points_earned=points_earned,
            total=total,
            free_shipping_threshold=settings.FREE_SHIPPING_THRESHOLD,
        )

    # ---------- Đặt hàng ----------
    def create_order(self, req: OrderCreateRequest, user: Optional[Any] = None) -> OrderResponse:
        # Báo giá lại toàn bộ phía server
        quote = self.build_quote(
            req.items, req.voucher_code, strict_voucher=True,
            use_points=req.use_points, user=user
        )

        # Trừ tồn kho tạm thời trong bộ nhớ
        needed: Dict[str, int] = {}
        for line in quote.lines:
            needed[line.product_id] = needed.get(line.product_id, 0) + line.quantity
        with product_service._lock:
            for pid, qty in needed.items():
                p = product_service._by_id.get(pid)
                if p:
                    p.stock = max(0, p.stock - qty)
                    p.sold_count += qty

        now = datetime.datetime.now()
        order_id = f"AURA-{now.strftime('%y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        status = "confirmed" if req.payment_method == "cod" else "pending_payment"
        carrier = "Giao Hàng Nhanh (GHN Express)"
        tracking_code = f"GHN-VN-{order_id[-6:]}"
        shipping_status = "ready_to_pick" if status == "confirmed" else "pending_confirm"
        estimated_delivery = (now + datetime.timedelta(days=3)).strftime("%d/%m/%Y")

        record = {
            "order_id": order_id,
            "status": status,
            "user_id": getattr(user, "id", None) if user else None,
            "carrier": carrier,
            "tracking_code": tracking_code,
            "shipping_status": shipping_status,
            "estimated_delivery": estimated_delivery,
            "created_at": now.isoformat(timespec="seconds"),
            "customer": {
                "name": req.customer_name,
                "phone": req.customer_phone,
                "address": req.customer_address,
                "province": getattr(req, "province_name", None) or getattr(req, "province", None),
                "district": getattr(req, "district", None),
                "ward": getattr(req, "ward_name", None) or getattr(req, "ward", None),
                "province_code": getattr(req, "province_code", None),
                "ward_code": getattr(req, "ward_code", None),
                "specific_address": req.specific_address,
                "note": req.customer_note
            },
            "payment_method": req.payment_method,
            "quote": quote.model_dump(),
        }

        try:
            # Lưu trực tiếp vào Database (orders + order_items)
            db_service.save_order(record)

            if self.orders_path:
                try:
                    os.makedirs(os.path.dirname(self.orders_path), exist_ok=True)
                    with self._lock, open(self.orders_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                except Exception:
                    pass

            # Xử lý trừ điểm và cộng điểm tích lũy cho người dùng
            if user and getattr(user, "id", None):
                if quote.points_used > 0:
                    db_service.deduct_loyalty_points(user.id, quote.points_used, order_id)
                net_paid = max(0, quote.subtotal - quote.combo_discount - quote.voucher_discount - quote.points_discount)
                db_service.update_user_total_spent_and_tier(user.id, net_paid)
                if quote.points_earned > 0:
                    db_service.add_loyalty_points(
                        user.id, quote.points_earned, "earn",
                        f"Tích điểm từ đơn hàng {order_id}", order_id=order_id
                    )
        except Exception as e:
            product_service.release_stock(needed)  # hoàn kho nếu không ghi được đơn
            raise OrderError("Không thể lưu đơn hàng lúc này, vui lòng thử lại", 500)

        qr_code_url = None
        bank_info = None
        if req.payment_method == "qr_transfer":
            # Tạo mã VietQR chuẩn NAPAS tự động
            qr_code_url = (
                f"https://img.vietqr.io/image/MB-0900000001-compact2.png"
                f"?amount={quote.total}&addInfo=AURA%20{order_id}&accountName=AURA%20STUDIO"
            )
            bank_info = {
                "bank_name": "MBBank (Ngân hàng Quân Đội)",
                "account_number": "0900000001",
                "account_name": "AURA STUDIO",
                "amount": str(quote.total),
                "content": f"AURA {order_id}"
            }
            msg = (f"Đã ghi nhận đơn. Vui lòng quét mã VietQR hoặc chuyển khoản {_money(quote.total)} "
                   f"với nội dung 'AURA {order_id}' để hệ thống tự động xác nhận đơn.")
        else:
            msg = "Đặt hàng thành công! Chúng tôi sẽ liên hệ xác nhận và giao hàng sớm nhất."

        return OrderResponse(
            order_id=order_id, status=status, payment_status="paid" if status == "confirmed" else "unpaid",
            carrier=carrier, tracking_code=tracking_code, shipping_status=shipping_status,
            estimated_delivery=estimated_delivery,
            quote=quote, customer_name=req.customer_name, customer_phone=req.customer_phone,
            customer_address=req.customer_address, payment_method=req.payment_method,
            created_at=now.strftime("%d/%m/%Y %H:%M"), message=msg,
            qr_code_url=qr_code_url, bank_info=bank_info,
        )

    # ---------- Lưu trữ & Truy vấn Database ----------
    def _format_order(self, o: OrderDB) -> dict:
        quote = {}
        if o.quote_json:
            try:
                quote = json.loads(o.quote_json)
            except Exception:
                quote = {}
        if not quote:
            quote = {
                "total": o.total_amount,
                "subtotal": o.subtotal,
                "shipping_fee": o.shipping_fee,
                "lines": [],
            }

        if not quote.get("lines") and hasattr(o, "items") and o.items:
            quote["lines"] = [
                {
                    "product_id": item.product_id,
                    "name": item.product_name or item.product_id,
                    "image": "/static/images/placeholder.jpg",
                    "size": item.size,
                    "color": item.color,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "line_total": item.line_total,
                }
                for item in o.items
            ]

        return {
            "order_id": o.order_id,
            "status": o.order_status,
            "order_status": o.order_status,
            "payment_status": o.payment_status,
            "carrier": o.carrier or "Giao Hàng Nhanh (GHN Express)",
            "tracking_code": o.tracking_code or f"GHN-VN-{o.order_id[-6:]}",
            "shipping_status": o.shipping_status or (
                "ready_to_pick" if o.order_status in ["confirmed", "completed"] else "pending_confirm"
            ),
            "estimated_delivery": o.estimated_delivery or "2 - 3 ngày tới",
            "created_at": o.created_at,
            "paid_at": o.paid_at,
            "total_amount": o.total_amount,
            "subtotal": o.subtotal,
            "shipping_fee": o.shipping_fee,
            "discount_amount": o.discount_amount,
            "customer_name": o.customer_name,
            "customer_phone": o.customer_phone,
            "customer_address": o.customer_address,
            "customer": {
                "name": o.customer_name,
                "phone": o.customer_phone,
                "address": o.customer_address,
                "province": o.province,
                "district": o.district,
                "ward": o.ward,
                "specific_address": o.specific_address,
                "note": o.customer_note,
            },
            "payment_method": o.payment_method,
            "quote": quote,
        }

    def _restore_stock_from_history(self):
        """Khởi động lại server không làm 'hồi' lại hàng đã bán."""
        if self.orders_path and os.path.exists(self.orders_path):
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
                    except Exception:
                        continue
            return

        try:
            with get_db_session() as session:
                orders = session.query(OrderDB).filter(OrderDB.order_status != "cancelled").all()
                for o in orders:
                    if not o.quote_json:
                        continue
                    try:
                        q = json.loads(o.quote_json)
                        for l in q.get("lines", []):
                            product_service.apply_historical_sale(l["product_id"], l["quantity"])
                    except Exception:
                        continue
        except Exception:
            pass

    def count_orders(self) -> int:
        if self.orders_path is not None:
            if os.path.exists(self.orders_path):
                with open(self.orders_path, "r", encoding="utf-8") as f:
                    return sum(1 for line in f if line.strip())
            return 0
        with get_db_session() as session:
            return session.query(func.count(OrderDB.order_id)).scalar() or 0

    # ---------- Quản lý đơn hàng Admin ----------
    def get_orders(self, status: Optional[str] = None, search: Optional[str] = None,
                   limit: int = 50, offset: int = 0) -> List[dict]:
        self._ensure_demo_orders()
        with get_db_session() as session:
            query = session.query(OrderDB)
            if status and status != "all":
                if status == "pending":
                    query = query.filter(or_(OrderDB.order_status == "pending", OrderDB.order_status == "pending_payment"))
                else:
                    query = query.filter(OrderDB.order_status == status)

            if search and search.strip():
                s = f"%{search.strip().lower()}%"
                query = query.filter(
                    or_(
                        func.lower(OrderDB.order_id).like(s),
                        func.lower(OrderDB.customer_name).like(s),
                        func.lower(OrderDB.customer_phone).like(s),
                    )
                )

            orders = query.order_by(OrderDB.created_at.desc(), OrderDB.order_id.desc()).offset(offset).limit(limit).all()
            return [self._format_order(o) for o in orders]

    def get_order_by_id(self, order_id: str) -> Optional[dict]:
        self._ensure_demo_orders()
        with get_db_session() as session:
            o = session.query(OrderDB).filter(OrderDB.order_id == order_id).first()
            return self._format_order(o) if o else None

    def update_order_status(self, order_id: str, new_status: str) -> Optional[dict]:
        with get_db_session() as session:
            o = session.query(OrderDB).filter(OrderDB.order_id == order_id).first()
            if not o:
                return None

            old_status = o.order_status
            o.order_status = new_status
            if new_status == "confirmed" and o.payment_method != "cod":
                o.payment_status = "paid"
            if new_status == "completed":
                o.shipping_status = "delivered"
            elif new_status == "confirmed" and o.shipping_status in ["pending_confirm", None]:
                o.shipping_status = "ready_to_pick"

            # Nếu hủy đơn -> hoàn kho
            if new_status == "cancelled" and old_status != "cancelled":
                if o.quote_json:
                    try:
                        q = json.loads(o.quote_json)
                        needed = {l["product_id"]: l["quantity"] for l in q.get("lines", [])}
                        product_service.release_stock(needed)
                    except Exception:
                        pass

            session.flush()
            session.refresh(o)
            return self._format_order(o)

    def get_admin_stats(self) -> dict:
        self._ensure_demo_orders()
        with get_db_session() as session:
            total_orders = session.query(func.count(OrderDB.order_id)).scalar() or 0
            total_revenue = (
                session.query(func.sum(OrderDB.total_amount))
                .filter(OrderDB.order_status != "cancelled")
                .scalar()
            ) or 0

            rows = (
                session.query(OrderDB.order_status, func.count(OrderDB.order_id))
                .group_by(OrderDB.order_status)
                .all()
            )
            status_counts = {"pending": 0, "pending_payment": 0, "confirmed": 0, "completed": 0, "cancelled": 0}
            for st, cnt in rows:
                if st:
                    status_counts[st] = cnt
            # Hợp nhất pending_payment vào pending cho giao diện tổng quan
            status_counts["pending"] = status_counts.get("pending", 0) + status_counts.get("pending_payment", 0)

            return {
                "total_orders": total_orders,
                "total_revenue": int(total_revenue),
                "status_counts": status_counts,
            }


order_service = OrderService()
