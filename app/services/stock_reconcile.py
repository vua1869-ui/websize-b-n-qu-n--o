import logging
from typing import Any, Dict, List, Optional

from app.db.database import db_service, get_db_transaction
from app.services.product_service import product_service

logger = logging.getLogger("aura.stock_reconcile")


def reconcile_stock(apply_fix: bool = False, admin_id: Optional[str] = "system") -> Dict[str, Any]:
    """
    Đối soát tồn kho giữa CSDL products, product_variants và bộ nhớ RAM:
    (a) So products.stock với SUM(product_variants.stock)
    (b) Phát hiện variant stock < 0
    (c) So tồn kho RAM (product_service) với DB
    (d) Nếu apply_fix=True: sửa products.stock theo tổng variants, đồng bộ lại RAM và ghi audit_log.
    """
    product_variant_mismatches: List[Dict[str, Any]] = []
    negative_variants: List[Dict[str, Any]] = []
    ram_mismatches: List[Dict[str, Any]] = []

    with get_db_transaction(db_service.db_path) as conn:
        products = conn.execute("SELECT id, name, stock FROM products;").fetchall()
        variants = conn.execute("SELECT id, product_id, size, color, stock FROM product_variants;").fetchall()

    db_products = {p["id"]: dict(p) for p in products}

    # Gom variant theo product_id
    variants_by_pid: Dict[str, List[dict]] = {}
    variants_sum_by_pid: Dict[str, int] = {}
    for v in variants:
        v_dict = dict(v)
        pid = v_dict["product_id"]
        v_stock = int(v_dict["stock"] or 0)
        variants_by_pid.setdefault(pid, []).append(v_dict)
        variants_sum_by_pid[pid] = variants_sum_by_pid.get(pid, 0) + v_stock

        if v_stock < 0:
            negative_variants.append({
                "variant_id": v_dict["id"],
                "product_id": pid,
                "size": v_dict.get("size"),
                "color": v_dict.get("color"),
                "stock": v_stock,
            })

    # (a) So sánh products.stock với SUM(product_variants.stock)
    for pid, p in db_products.items():
        if pid in variants_by_pid:
            v_sum = variants_sum_by_pid.get(pid, 0)
            p_stock = int(p["stock"] or 0)
            if p_stock != v_sum:
                product_variant_mismatches.append({
                    "product_id": pid,
                    "name": p["name"],
                    "product_stock": p_stock,
                    "variants_sum": v_sum,
                    "diff": p_stock - v_sum,
                })

    # (c) So sánh tồn kho RAM với DB
    with product_service._lock:
        for pid, p_ram in product_service._by_id.items():
            db_p = db_products.get(pid)
            if db_p:
                ram_stk = int(p_ram.stock or 0)
                db_stk = int(db_p["stock"] or 0)
                if ram_stk != db_stk:
                    ram_mismatches.append({
                        "product_id": pid,
                        "name": p_ram.name,
                        "ram_stock": ram_stk,
                        "db_stock": db_stk,
                        "diff": ram_stk - db_stk,
                    })

    has_mismatch = bool(product_variant_mismatches or negative_variants or ram_mismatches)

    # (d) Sửa nếu apply_fix=True
    if apply_fix and product_variant_mismatches:
        audit_entries = []
        with product_service._lock:
            with get_db_transaction(db_service.db_path) as conn:
                for item in product_variant_mismatches:
                    pid = item["product_id"]
                    new_stock = item["variants_sum"]
                    old_stock = item["product_stock"]

                    conn.execute(
                        "UPDATE products SET stock = ? WHERE id = ?;",
                        (new_stock, pid),
                    )

                    # Cập nhật RAM
                    if pid in product_service._by_id:
                        product_service._by_id[pid].stock = new_stock

                    audit_entries.append((pid, old_stock, new_stock))

            for pid, old_stock, new_stock in audit_entries:
                try:
                    db_service.log_audit(
                        admin_id=admin_id or "system",
                        action="reconcile_stock",
                        target_type="product",
                        target_id=pid,
                        before_state={"stock": old_stock},
                        after_state={"stock": new_stock},
                    )
                except Exception as _ex:
                    logger.warning("Không thể ghi audit log cho reconcile: %s", _ex)

            # Đồng bộ lại các sản phẩm lệch RAM còn lại
            for item in ram_mismatches:
                pid = item["product_id"]
                if pid in product_service._by_id and pid in db_products:
                    # Lấy stock mới nhất đã fix hoặc nguyên bản từ DB
                    fixed_item = next((m for m in product_variant_mismatches if m["product_id"] == pid), None)
                    target_stock = fixed_item["variants_sum"] if fixed_item else item["db_stock"]
                    product_service._by_id[pid].stock = target_stock

    summary = (
        f"Lệch Variant-Product: {len(product_variant_mismatches)} | "
        f"Variant âm: {len(negative_variants)} | "
        f"Lệch RAM-DB: {len(ram_mismatches)}"
    )

    return {
        "has_mismatch": has_mismatch,
        "product_variant_mismatches": product_variant_mismatches,
        "negative_variants": negative_variants,
        "ram_mismatches": ram_mismatches,
        "fixed": apply_fix,
        "summary": summary,
    }
