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
from typing import Any, Dict, List, Optional

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

        after_voucher = after_combo
        if voucher:
            if voucher.kind == "amount":
                voucher_discount = min(voucher.value, after_combo)
            elif voucher.kind == "percent":
                voucher_discount = after_combo * voucher.value // 100
                if voucher.max_discount:
                    voucher_discount = min(voucher_discount, voucher.max_discount)
            after_voucher = max(0, after_combo - voucher_discount)
            if voucher_message is None:
                voucher_message = f"Đã áp dụng mã {voucher.code}"

        # ---- Hạng thành viên Loyalty & Miễn phí vận chuyển ----
        from app.db.database import db_service
        loyalty = None
        if user and getattr(user, "id", None):
            loyalty = db_service.get_user_loyalty(user.id)

        # ---- Phí vận chuyển ----
        shipping_fee = 0 if after_combo >= settings.FREE_SHIPPING_THRESHOLD else settings.SHIPPING_FEE
        shipping_discount = 0

        # Nếu là hội viên VIP Gold hoặc Diamond: miễn phí vận chuyển 100% mọi đơn hàng
        if loyalty and loyalty.get("free_shipping_all_orders"):
            shipping_discount = shipping_fee

        if voucher and voucher.kind == "shipping":
            shipping_discount = shipping_fee
            if shipping_fee == 0:
                voucher_message = "Đơn của bạn đã được miễn phí vận chuyển sẵn"

        # ---- Dùng điểm tích lũy AURA Club (1 điểm = 1.000 VNĐ) ----
        points_used = 0
        points_discount = 0
        if use_points > 0:
            if not loyalty:
                if strict_voucher:
                    raise OrderError("Vui lòng đăng nhập để sử dụng điểm thưởng AURA Club", 401)
            else:
                max_pts_avail = loyalty.get("points_balance", 0)
                max_pts_order = after_voucher // 1000
                points_used = max(0, min(use_points, max_pts_avail, max_pts_order))
                points_discount = points_used * 1000

        # ---- Tích lũy điểm dự kiến nhận được từ đơn hàng ----
        earn_rate = loyalty.get("earn_rate_percent", 3) if loyalty else 3
        net_payable_for_points = max(0, after_voucher - points_discount)
        points_earned = int((net_payable_for_points * earn_rate) / 100 / 1000)

        total = after_voucher - points_discount + shipping_fee - shipping_discount
        return QuoteResponse(
            lines=lines, subtotal=subtotal, combo_discount=combo_discount,
            voucher_code=applied_code, voucher_discount=voucher_discount,
            voucher_message=voucher_message,
            points_used=points_used,
            points_discount=points_discount,
            points_earned=points_earned,
            shipping_fee=shipping_fee,
            shipping_discount=shipping_discount, total=max(0, total),
        )

    # ---------- Tạo đơn ----------
    def create_order(self, req: OrderCreateRequest, user: Optional[Any] = None) -> OrderResponse:
        quote = self.build_quote(
            req.items,
            req.voucher_code,
            strict_voucher=True,
            use_points=getattr(req, "use_points", 0),
            user=user,
        )

        # Trừ kho biến thể (Màu x Size) nguyên tử trong CSDL SQLite
        variant_items = [
            {"product_id": line.product_id, "color": line.color, "size": line.size, "quantity": line.quantity}
            for line in quote.lines
        ]
        from app.db.database import db_service
        ok, stock_err = db_service.check_and_deduct_variants_stock(variant_items)
        if not ok:
            raise OrderError(stock_err or "Không đủ hàng trong kho", 409)

        # Đồng bộ bộ nhớ đệm
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
            "order_id": order_id, "status": status,
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
            self._append(record)
            db_service.save_order(record)

            # Xử lý trừ điểm và cộng điểm tích lũy cho người dùng
            if user and getattr(user, "id", None):
                if quote.points_used > 0:
                    db_service.deduct_loyalty_points(user.id, quote.points_used, order_id)
                net_paid = max(0, quote.subtotal - quote.combo_discount - quote.voucher_discount - quote.points_discount)
                db_service.update_user_total_spent_and_tier(user.id, net_paid)
                if quote.points_earned > 0:
                    db_service.add_loyalty_points(user.id, quote.points_earned, "earn", f"Tích điểm từ đơn hàng {order_id}", order_id=order_id)
        except OSError:
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

    # ---------- Quản lý đơn hàng Admin ----------
    def get_orders(self, status: Optional[str] = None, search: Optional[str] = None,
                   limit: int = 50, offset: int = 0) -> List[dict]:
        self._ensure_demo_orders()
        if not os.path.exists(self.orders_path):
            return []

        orders = []
        with open(self.orders_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        orders.append(json.loads(line))
                    except Exception:
                        continue

        # Mới nhất lên đầu
        orders.reverse()

        if status and status != "all":
            orders = [o for o in orders if o.get("status") == status]

        if search and search.strip():
            s = search.strip().lower()
            orders = [
                o for o in orders
                if s in o.get("order_id", "").lower()
                or s in o.get("customer", {}).get("name", "").lower()
                or s in o.get("customer", {}).get("phone", "").lower()
            ]

        return orders[offset: offset + limit]

    def get_order_by_id(self, order_id: str) -> Optional[dict]:
        self._ensure_demo_orders()
        if not os.path.exists(self.orders_path):
            return None
        with open(self.orders_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        o = json.loads(line)
                        if o.get("order_id") == order_id:
                            return o
                    except Exception:
                        continue
        return None

    def update_order_status(self, order_id: str, new_status: str) -> Optional[dict]:
        with self._lock:
            if not os.path.exists(self.orders_path):
                return None
            records = []
            target = None
            with open(self.orders_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rec = json.loads(line)
                            if rec.get("order_id") == order_id:
                                old_status = rec.get("status")
                                rec["status"] = new_status
                                target = rec
                                # Nếu hủy đơn -> hoàn kho
                                if new_status == "cancelled" and old_status != "cancelled":
                                    needed = {l["product_id"]: l["quantity"] for l in rec["quote"]["lines"]}
                                    product_service.release_stock(needed)
                            records.append(rec)
                        except Exception:
                            continue

            if target:
                with open(self.orders_path, "w", encoding="utf-8") as f:
                    for r in records:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            return target

    def get_admin_stats(self) -> dict:
        self._ensure_demo_orders()
        total_orders = 0
        total_revenue = 0
        status_counts = {"pending": 0, "confirmed": 0, "completed": 0, "cancelled": 0}

        if os.path.exists(self.orders_path):
            with open(self.orders_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rec = json.loads(line)
                            total_orders += 1
                            st = rec.get("status", "pending")
                            status_counts[st] = status_counts.get(st, 0) + 1
                            if st != "cancelled":
                                total_revenue += rec.get("quote", {}).get("total", 0)
                        except Exception:
                            continue

        return {
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "status_counts": status_counts,
        }

    def _ensure_demo_orders(self):
        """Khởi tạo các đơn hàng demo ban đầu nếu chưa có file đơn hàng."""
        if os.path.exists(self.orders_path) and os.path.getsize(self.orders_path) > 0:
            return

        demo_customers = [
            ("Lê Thị Thảo", "0912345678", "45 Lê Lợi, Quận 1, TP. Hồ Chí Minh", "Gọi trước khi giao", "cod", "completed", "prod_001", "M", "Be / Kem"),
            ("Nguyễn Văn Hùng", "0981234567", "120 Cầu Giấy, Hà Nội", "Giao giờ hành chính", "qr_transfer", "completed", "prod_002", "S", "Trắng Ngọc Trai"),
            ("Trần Minh Tuấn", "0908765432", "88 Nguyễn Thị Minh Khai, Đà Nẵng", "Để hàng ở bảo vệ", "cod", "confirmed", "prod_003", "L", "Đen Tuyển"),
            ("Phạm Hồng Nhung", "0976543210", "15 Hai Bà Trưng, Hoàn Kiếm, Hà Nội", "", "cod", "completed", "prod_004", "M", "Đỏ Rượu Vang"),
            ("Đặng Tiến Anh", "0965432109", "36 Trần Hưng Đạo, Quận 5, TP. Hồ Chí Minh", "Giao buổi chiều", "qr_transfer", "confirmed", "prod_005", "XL", "Trắng Basic"),
            ("Vũ Bích Ngọc", "0943210987", "72 Bạch Đằng, Hải Châu, Đà Nẵng", "", "cod", "pending_payment", "prod_006", "30", "Xanh Vintage Wash"),
            ("Đỗ Gia Bảo", "0932109876", "29 Nguyễn Trãi, Thanh Xuân, Hà Nội", "Cho xem hàng", "cod", "completed", "prod_008", "M", "Nâu Chocolate"),
            ("Ngô Phương Linh", "0921098765", "105 Cách Mạng Tháng 8, Quận 3, TP. Hồ Chí Minh", "", "qr_transfer", "completed", "prod_009", "M", "Đen Tuyền"),
            ("Hoàng Quốc Việt", "0918765432", "214 Phố Huế, Hai Bà Trưng, Hà Nội", "Hàng dễ vỡ", "cod", "confirmed", "prod_010", "L", "Xám Xi Măng"),
            ("Trịnh Thu Trang", "0987654322", "58 Nguyễn Văn Linh, Đà Nẵng", "Gọi trước 15 phút", "cod", "cancelled", "prod_012", "S", "Xanh Rêu Pastel"),
            ("Bùi Thanh Tùng", "0971234568", "19 Quang Trung, Hà Đông, Hà Nội", "", "qr_transfer", "completed", "prod_091", "L", "Đen Washed"),
            ("Mai Phương Thảo", "0962345679", "33 Hai Bà Trưng, Quận 1, TP. Hồ Chí Minh", "Giao gấp sáng mai", "cod", "confirmed", "prod_096", "M", "Hồng Baby"),
        ]

        now = datetime.datetime.now()
        os.makedirs(os.path.dirname(self.orders_path), exist_ok=True)
        with open(self.orders_path, "w", encoding="utf-8") as f:
            for idx, (name, phone, addr, note, method, st, pid, sz, col) in enumerate(demo_customers, 1):
                p = product_service.get_by_id(pid)
                if not p:
                    continue
                p_price = p.final_price
                sub = p_price
                fee = 0 if sub >= settings.FREE_SHIPPING_THRESHOLD else settings.SHIPPING_FEE
                tot = sub + fee
                order_time = (now - datetime.timedelta(days=idx // 2, hours=idx * 2)).strftime("%d/%m/%Y %H:%M")
                order_id = f"AURA-{now.strftime('%y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

                carrier = "Giao Hàng Nhanh (GHN Express)" if idx % 2 == 1 else "Giao Hàng Tiết Kiệm (GHTK)"
                tracking_code = f"GHN-VN-{order_id[-6:]}" if "GHN" in carrier else f"GHTK-VN-{order_id[-6:]}"
                ship_status = "delivered" if st == "completed" else "in_transit" if st == "confirmed" else "pending_confirm"
                est_del = (now - datetime.timedelta(days=idx // 2 - 2)).strftime("%d/%m/%Y") if st == "completed" else (now + datetime.timedelta(days=2)).strftime("%d/%m/%Y")

                rec = {
                    "order_id": order_id,
                    "status": st,
                    "carrier": carrier,
                    "tracking_code": tracking_code,
                    "shipping_status": ship_status,
                    "estimated_delivery": est_del,
                    "created_at": order_time,
                    "customer": {"name": name, "phone": phone, "address": addr, "note": note},
                    "payment_method": method,
                    "quote": {
                        "lines": [{
                            "product_id": p.id,
                            "name": p.name,
                            "image": p.images[0] if p.images else "",
                            "size": sz,
                            "color": col,
                            "quantity": 1,
                            "unit_price": p_price,
                            "line_total": p_price,
                            "combo": False
                        }],
                        "subtotal": sub,
                        "combo_discount": 0,
                        "voucher_code": None,
                        "voucher_discount": 0,
                        "voucher_message": None,
                        "shipping_fee": fee,
                        "shipping_discount": 0,
                        "total": tot
                    }
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


order_service = OrderService()
