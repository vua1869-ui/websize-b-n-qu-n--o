
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import settings
from app.db.database import db_service
from app.models.schemas import LoyaltyHistoryResponse, LoyaltyStatusResponse, User
from app.routers.deps import get_current_user, get_current_user_optional

router = APIRouter(tags=["loyalty"])


@router.get("/api/loyalty/status", response_model=LoyaltyStatusResponse)
def get_loyalty_status(request: Request, user: User = Depends(get_current_user)):
    """Lấy trạng thái hạng thẻ thành viên, điểm tích lũy và đặc quyền AURA Club."""
    return db_service.get_user_loyalty(user.id)


@router.get("/api/loyalty/history", response_model=LoyaltyHistoryResponse)
def get_loyalty_history(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    """Lấy lịch sử cộng/trừ điểm thưởng của người dùng."""
    status = db_service.get_user_loyalty(user.id)
    history = db_service.get_loyalty_history(user.id, limit=limit)
    return {
        "points_balance": status["points_balance"],
        "total_spent": status["total_spent"],
        "tier": status["tier"],
        "transactions": history,
    }


@router.post("/api/loyalty/simulate-earn")
def simulate_loyalty_earn(
    points: int = Query(50, ge=1, le=1000),
    request: Request = None,
):
    """Chỉ hoạt động khi DEBUG=True, APP_ENV!=production và là admin. Ngoài ra trả 404."""
    if not settings.DEBUG or settings.APP_ENV.strip().lower() == "production":
        raise HTTPException(status_code=404, detail="Endpoint không tồn tại")
    user = get_current_user_optional(request)
    if not user or user.role != "admin" or not user.is_active:
        raise HTTPException(status_code=404, detail="Endpoint không tồn tại")
    new_bal = db_service.add_loyalty_points(
        user.id, points, "bonus", f"Điểm thưởng trải nghiệm sự kiện AURA (+{points} điểm)"
    )
    return {"success": True, "points_added": points, "new_balance": new_bal}
