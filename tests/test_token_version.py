import hashlib
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import PasswordResetTokenDB, UserDB
from app.services.user_service import user_service

client = TestClient(app)


def test_change_password_increments_token_version_and_invalidates_old_token():
    """Đổi mật khẩu làm tăng token_version và lập tức vô hiệu hoá token cũ."""
    uid = uuid.uuid4().hex[:6]
    username = f"tv_cp_{uid}"
    email = f"tv_cp_{uid}@example.com"

    # 1. Đăng ký user
    u = user_service.register_user(
        name=f"TV CP {uid}",
        email=email,
        username=username,
        password="oldpassword123"
    )
    assert u.token_version == 1

    # 2. Tạo session token cũ
    old_token = user_service.create_session_token(u)
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # Token cũ đang hoạt động tốt
    res_me_1 = client.get("/api/auth/me", headers=old_headers)
    assert res_me_1.status_code == 200

    # 3. Đổi mật khẩu
    res_cp = client.post("/api/auth/change-password", json={
        "old_password": "oldpassword123",
        "new_password": "newpassword456"
    }, headers=old_headers)
    assert res_cp.status_code == 200

    # Kiểm tra token_version trong DB đã tăng lên 2
    u_updated = user_service.get_by_id(u.id)
    assert u_updated.token_version == 2

    # 4. Token cũ (phiên bản 1) lập tức bị từ chối 401 Unauthorized
    res_me_2 = client.get("/api/auth/me", headers=old_headers)
    assert res_me_2.status_code == 401

    # 5. Đăng nhập lại bằng mật khẩu mới -> Nhận token mới (phiên bản 2) -> Truy cập lại được
    res_login = client.post("/api/auth/login", json={"username": username, "password": "newpassword456"})
    assert res_login.status_code == 200
    new_token = res_login.json()["token"]

    res_me_3 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert res_me_3.status_code == 200


def test_reset_password_increments_token_version_and_invalidates_old_token():
    """Reset mật khẩu làm tăng token_version và vô hiệu hoá token cũ."""
    uid = uuid.uuid4().hex[:6]
    username = f"tv_rst_{uid}"
    email = f"tv_rst_{uid}@example.com"

    u = user_service.register_user(
        name=f"TV RST {uid}",
        email=email,
        username=username,
        password="password123"
    )

    old_token = user_service.create_session_token(u)
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # Verify active
    assert client.get("/api/auth/me", headers=old_headers).status_code == 200

    # Reset password
    raw_token = f"reset_tv_token_{uid}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with get_db_session() as session:
        entry = PasswordResetTokenDB(
            user_id=u.id,
            token_hash=token_hash,
            expires_at=time.time() + 1800,
            used=0,
            created_at="now"
        )
        session.add(entry)

    res_rst = client.post("/api/auth/reset-password", json={
        "token": raw_token,
        "new_password": "resetpassword789",
        "confirm_password": "resetpassword789"
    })
    assert res_rst.status_code == 200

    # Old token version is now invalid
    assert client.get("/api/auth/me", headers=old_headers).status_code == 401


def test_admin_lock_user_increments_token_version_and_blocks_access():
    """Admin khóa tài khoản làm tăng token_version và vô hiệu hóa các token cũ."""
    uid = uuid.uuid4().hex[:6]
    username = f"tv_lock_{uid}"
    email = f"tv_lock_{uid}@example.com"

    u = user_service.register_user(
        name=f"TV Lock {uid}",
        email=email,
        username=username,
        password="password123"
    )

    old_token = user_service.create_session_token(u)
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # Verify active
    assert client.get("/api/auth/me", headers=old_headers).status_code == 200

    # Admin khóa tài khoản user
    user_service.toggle_status(u.id, is_active=False)

    # Token cũ lập tức bị chặn 401 (do vừa sai version vừa tài khoản bị disabled)
    assert client.get("/api/auth/me", headers=old_headers).status_code == 401

    # Thử đăng nhập lại khi tài khoản đang bị khóa -> 403 Forbidden
    res_login_locked = client.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert res_login_locked.status_code == 403
    assert "bị khóa" in res_login_locked.json()["detail"]

    # Admin mở lại tài khoản
    user_service.toggle_status(u.id, is_active=True)

    # Token cũ vẫn bị 401 vì token_version đã tăng
    assert client.get("/api/auth/me", headers=old_headers).status_code == 401

    # Người dùng đăng nhập lại thành công và lấy token mới
    res_login_unlocked = client.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert res_login_unlocked.status_code == 200
    new_token = res_login_unlocked.json()["token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
