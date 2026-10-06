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


def test_forgot_password_nonexistent_email_returns_same_message():
    """Trường hợp 1: Email không tồn tại -> Trả cùng 1 thông báo để chống dò tài khoản."""
    fake_email = f"nonexistent_{uuid.uuid4().hex[:8]}@example.com"
    
    res = client.post("/api/auth/forgot-password", json={"email": fake_email})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "Nếu email tồn tại trong hệ thống" in data["message"]

    # Kiểm tra với email thật (admin@aurastudio.vn hoặc user@aurastudio.vn)
    real_email = "admin@aurastudio.vn"
    res_real = client.post("/api/auth/forgot-password", json={"email": real_email})
    assert res_real.status_code == 200
    # Phải trả về thông báo Y HỆT như email không tồn tại
    assert res_real.json()["message"] == data["message"]


def test_reset_password_invalid_token():
    """Trường hợp 2: Mã token sai / không hợp lệ -> nhận 400 Bad Request."""
    res = client.post("/api/auth/reset-password", json={
        "token": "invalid_fake_token_9999",
        "new_password": "newpassword123",
        "confirm_password": "newpassword123"
    })
    assert res.status_code == 400
    assert "không hợp lệ" in res.json()["detail"]


def test_reset_password_expired_token():
    """Trường hợp 3: Mã token đã hết hạn (quá 30 phút) -> nhận 400 Bad Request."""
    # 1. Đăng ký user test
    uid = uuid.uuid4().hex[:6]
    email = f"expire_test_{uid}@example.com"
    user_service.register_user(
        name=f"Expire Test {uid}",
        email=email,
        username=f"exp_{uid}",
        password="oldpassword123"
    )

    # 2. Yêu cầu reset mật khẩu
    res_forgot = client.post("/api/auth/forgot-password", json={"email": email})
    assert res_forgot.status_code == 200

    # 3. Can thiệp DB để chỉnh expires_at về thời điểm trong quá khứ
    with get_db_session() as session:
        u = session.query(UserDB).filter(UserDB.email == email).first()
        reset_entry = session.query(PasswordResetTokenDB).filter(PasswordResetTokenDB.user_id == u.id).order_by(PasswordResetTokenDB.id.desc()).first()
        assert reset_entry is not None
        # Chỉnh expired về 100 giây trước
        reset_entry.expires_at = time.time() - 100
        # Lấy hash token giả định
        dummy_raw_token = "dummy_token_hash_expired"
        reset_entry.token_hash = hashlib.sha256(dummy_raw_token.encode()).hexdigest()

    # 4. Thử reset với token đã hết hạn -> 400
    res_reset = client.post("/api/auth/reset-password", json={
        "token": dummy_raw_token,
        "new_password": "newpassword123",
        "confirm_password": "newpassword123"
    })
    assert res_reset.status_code == 400
    assert "hết hạn" in res_reset.json()["detail"]


def test_reset_password_token_reuse():
    """Trường hợp 4: Token dùng lại lần thứ 2 -> nhận 400 Bad Request."""
    # 1. Đăng ký user test
    uid = uuid.uuid4().hex[:6]
    email = f"reuse_test_{uid}@example.com"
    user_service.register_user(
        name=f"Reuse Test {uid}",
        email=email,
        username=f"reuse_{uid}",
        password="oldpassword123"
    )

    # 2. Tạo token ngẫu nhiên và chèn DB
    raw_token = f"reuse_token_{uid}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with get_db_session() as session:
        u = session.query(UserDB).filter(UserDB.email == email).first()
        entry = PasswordResetTokenDB(
            user_id=u.id,
            token_hash=token_hash,
            expires_at=time.time() + 1800,
            used=0,
            created_at="now"
        )
        session.add(entry)

    # 3. Sử dụng token lần 1 -> Thành công (200 OK)
    res_1 = client.post("/api/auth/reset-password", json={
        "token": raw_token,
        "new_password": "newpassword123",
        "confirm_password": "newpassword123"
    })
    assert res_1.status_code == 200
    assert res_1.json()["success"] is True

    # 4. Cố tình sử dụng lại token lần 2 -> Thất bại (400 Bad Request)
    res_2 = client.post("/api/auth/reset-password", json={
        "token": raw_token,
        "new_password": "anotherpassword456",
        "confirm_password": "anotherpassword456"
    })
    assert res_2.status_code == 400
    assert "đã được sử dụng" in res_2.json()["detail"]


def test_reset_password_invalidates_old_sessions():
    """Trường hợp 5: Đặt lại mật khẩu thành công sẽ vô hiệu hóa tất cả phiên đăng nhập cũ."""
    # 1. Đăng ký user
    uid = uuid.uuid4().hex[:6]
    username = f"sess_{uid}"
    email = f"sess_{uid}@example.com"
    u = user_service.register_user(
        name=f"Session Test {uid}",
        email=email,
        username=username,
        password="password123"
    )

    # 2. Tạo phiên đăng nhập (token) trước khi đổi mật khẩu
    old_token = user_service.create_session_token(u)
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # Đảm bảo token cũ truy cập được API
    me_res_before = client.get("/api/auth/me", headers=old_headers)
    assert me_res_before.status_code == 200
    assert me_res_before.json()["username"] == username

    # 3. Tạo token reset mật khẩu & tiến hành reset
    raw_token = f"reset_session_token_{uid}"
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

    # Chờ 1s để timestamp thay đổi chắc chắn
    time.sleep(1.0)

    res_reset = client.post("/api/auth/reset-password", json={
        "token": raw_token,
        "new_password": "newpassword456",
        "confirm_password": "newpassword456"
    })
    assert res_reset.status_code == 200

    # 4. Thử dùng token cũ truy cập /api/auth/me -> Phải bị từ chối 401 Unauthorized!
    me_res_after = client.get("/api/auth/me", headers=old_headers)
    assert me_res_after.status_code == 401
    assert "Vui lòng đăng nhập" in me_res_after.json()["detail"]

    # 5. Đăng nhập lại bằng mật khẩu mới -> Lấy token mới và truy cập thành công
    res_login_new = client.post("/api/auth/login", json={"username": username, "password": "newpassword456"})
    assert res_login_new.status_code == 200
    new_token = res_login_new.json()["token"]
    me_res_new = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me_res_new.status_code == 200
