import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.db.models import CartItemDB
from app.db.session import get_db_session
from app.models.schemas import CartItemRequest, CartSyncRequest, User
from app.routers.deps import get_current_user
from app.services.product_service import product_service

router = APIRouter(tags=["cart"])


def _cart_row_to_dict(row: CartItemDB) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "product_id": row.product_id,
        "size": row.size,
        "color": row.color,
        "quantity": row.quantity,
        "combo_token": row.combo_token,
        "updated_at": row.updated_at,
    }


@router.get("/api/cart")
def get_cart(user: User = Depends(get_current_user)):
    """Lấy toàn bộ giỏ hàng của user đang đăng nhập."""
    with get_db_session() as session:
        items = session.query(CartItemDB).filter(CartItemDB.user_id == user.id).all()
        return [_cart_row_to_dict(r) for r in items]


@router.post("/api/cart")
def upsert_cart(body: CartItemRequest, user: User = Depends(get_current_user)):
    """
    Thêm mới hoặc cập nhật số lượng một dòng trong giỏ hàng.
    Kiểm tra product_id tồn tại, size và giới hạn tối đa 50 dòng giỏ hàng.
    """
    product = product_service.get_by_id(body.product_id)
    if not product:
        raise HTTPException(status_code=422, detail=f"Sản phẩm '{body.product_id}' không tồn tại")

    if product.sizes and body.size not in product.sizes:
        raise HTTPException(
            status_code=422,
            detail=f"Kích cỡ '{body.size}' không hợp lệ cho sản phẩm {product.name}",
        )

    color_val = (body.color or (product.colors[0] if product.colors else "Mặc định")).strip()
    qty = min(body.quantity, settings.MAX_QTY_PER_LINE)
    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    with get_db_session() as session:
        existing = session.query(CartItemDB).filter(
            CartItemDB.user_id == user.id,
            CartItemDB.product_id == body.product_id,
            CartItemDB.size == body.size,
            CartItemDB.color == color_val,
        ).first()

        if existing:
            existing.quantity = qty
            existing.combo_token = body.combo_token
            existing.updated_at = now_str
            session.flush()
            session.refresh(existing)
            return _cart_row_to_dict(existing)

        # Kiểm tra giới hạn 50 dòng
        count = session.query(CartItemDB).filter(CartItemDB.user_id == user.id).count()
        if count >= 50:
            raise HTTPException(
                status_code=422,
                detail="Giỏ hàng đã đạt giới hạn tối đa 50 sản phẩm",
            )

        new_item = CartItemDB(
            user_id=user.id,
            product_id=body.product_id,
            size=body.size,
            color=color_val,
            quantity=qty,
            combo_token=body.combo_token,
            updated_at=now_str,
        )
        session.add(new_item)
        session.flush()
        session.refresh(new_item)
        return _cart_row_to_dict(new_item)


@router.delete("/api/cart/{item_id}")
def delete_cart_item(item_id: int, user: User = Depends(get_current_user)):
    """Xóa một dòng khỏi giỏ hàng theo ID."""
    with get_db_session() as session:
        item = session.query(CartItemDB).filter(
            CartItemDB.id == item_id,
            CartItemDB.user_id == user.id,
        ).first()
        if not item:
            raise HTTPException(status_code=404, detail="Không tìm thấy dòng giỏ hàng")
        session.delete(item)
    return {"success": True, "message": f"Đã xóa dòng giỏ hàng #{item_id}"}


@router.post("/api/cart/sync")
def sync_cart(body: CartSyncRequest, user: User = Depends(get_current_user)):
    """
    Đồng bộ giỏ hàng từ client lên server.
    Tối đa 50 items, kiểm tra tính hợp lệ của từng sản phẩm.
    """
    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    added = 0

    with get_db_session() as session:
        current_count = session.query(CartItemDB).filter(CartItemDB.user_id == user.id).count()
        for it in body.items:
            if current_count >= 50:
                break
            product = product_service.get_by_id(it.product_id)
            if not product:
                continue

            color_val = (it.color or (product.colors[0] if product.colors else "Mặc định")).strip()
            qty = min(it.quantity, settings.MAX_QTY_PER_LINE)

            existing = session.query(CartItemDB).filter(
                CartItemDB.user_id == user.id,
                CartItemDB.product_id == it.product_id,
                CartItemDB.size == it.size,
                CartItemDB.color == color_val,
            ).first()

            if not existing:
                session.add(CartItemDB(
                    user_id=user.id,
                    product_id=it.product_id,
                    size=it.size,
                    color=color_val,
                    quantity=qty,
                    combo_token=it.combo_token,
                    updated_at=now_str,
                ))
                added += 1
                current_count += 1

    return {
        "success": True,
        "added": added,
        "message": f"Đã đồng bộ {added} sản phẩm từ client lên server",
    }
