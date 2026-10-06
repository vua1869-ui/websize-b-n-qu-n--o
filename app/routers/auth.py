from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from app.config import settings
from app.models.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    User,
    UserLoginRequest,
    UserProfileUpdateRequest,
    UserRegisterRequest,
)
from app.routers.deps import (
    check_forgot_password_rate_limit,
    check_login_rate_limit,
    check_register_rate_limit,
    get_client_ip,
    get_current_user,
    record_login_failure,
    reset_login_failures,
)
from app.services.user_service import user_service

router = APIRouter(tags=["auth"])


@router.post("/api/auth/register", response_model=AuthResponse)
def auth_register(body: UserRegisterRequest, request: Request, response: Response):
    ip = get_client_ip(request)
    check_register_rate_limit(ip)

    try:
        user = user_service.register(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    token = user_service.create_session_token(user)
    response.set_cookie(
        key="aura_session",
        value=token,
        httponly=True,
        secure=not settings.DEBUG,
        path="/",
        max_age=86400 * 7,
        samesite="lax",
    )
    return AuthResponse(token=token, user=user, message="Đăng ký tài khoản thành công!")


@router.post("/api/auth/login", response_model=AuthResponse)
def auth_login(body: UserLoginRequest, request: Request, response: Response):
    identifier = body.username or body.username_or_email
    if not identifier:
        raise HTTPException(status_code=422, detail="Vui lòng nhập tên đăng nhập hoặc email")

    ip = get_client_ip(request)
    check_login_rate_limit(ip, identifier)

    try:
        user = user_service.authenticate(identifier, body.password)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    if not user:
        record_login_failure(ip, identifier)
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không chính xác")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản này đã bị tạm khóa bởi quản trị viên")

    reset_login_failures(ip, identifier)
    token = user_service.create_session_token(user)
    response.set_cookie(
        key="aura_session",
        value=token,
        httponly=True,
        secure=not settings.DEBUG,
        path="/",
        max_age=86400 * 7,
        samesite="lax",
    )
    return AuthResponse(success=True, token=token, user=user, message="Đăng nhập thành công!")


@router.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(key="aura_session", path="/")
    return {"success": True, "message": "Đã đăng xuất thành công"}


@router.get("/api/auth/me", response_model=User)
def auth_me(user: User = Depends(get_current_user)):
    return user


@router.put("/api/auth/profile", response_model=User)
def auth_update_profile(body: UserProfileUpdateRequest, user: User = Depends(get_current_user)):
    updated = user_service.update_profile(
        user.id, full_name=body.full_name, phone=body.phone, address=body.address
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản người dùng")
    return updated


@router.post("/api/auth/change-password")
def auth_change_password(
    body: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),
):
    ok, msg = user_service.change_password(user.id, body.old_password, body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # Đổi mật khẩu xong thì cấp lại session cookie mới cho phiên hiện tại để không bị logout
    refreshed_user = user_service.get_by_id(user.id) or user
    new_token = user_service.create_session_token(refreshed_user)
    response.set_cookie(
        key="aura_session",
        value=new_token,
        httponly=True,
        secure=not settings.DEBUG,
        path="/",
        max_age=86400 * 7,
        samesite="lax",
    )
    return {"success": True, "message": msg}


@router.post("/api/auth/forgot-password")
def auth_forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    ip = get_client_ip(request)
    check_forgot_password_rate_limit(ip, body.email)
    base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    msg = user_service.request_password_reset(
        body.email, base_url=base_url, background_tasks=background_tasks
    )
    return {"success": True, "message": msg}


@router.post("/api/auth/reset-password")
def auth_reset_password(body: ResetPasswordRequest):
    ok, msg = user_service.verify_and_reset_password(body.token, body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@router.get("/api/auth/orders")
def auth_user_orders(
    user: User = Depends(get_current_user),
    limit: int = 20,
    offset: int = 0,
):
    from app.db.models import OrderDB
    from app.db.session import get_db_session
    from app.services.order_service import order_service

    with get_db_session() as session:
        orders = (
            session.query(OrderDB)
            .filter(OrderDB.user_id == user.id)
            .order_by(OrderDB.created_at.desc(), OrderDB.order_id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [order_service._format_order(o) for o in orders]
