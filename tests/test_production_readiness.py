"""
Kiểm thử tính sẵn sàng triển khai Production:
1. Endpoint /healthz kiểm tra kết nối CSDL và trả về đúng status code (200 khi OK, 503 khi lỗi).
2. X-Request-ID được tạo/bảo toàn và gắn vào response header cũng như context logging.
3. APP_ENV=production từ chối khởi động nếu DEMO_DATA=True hoặc SECRET_KEY mặc định.
4. Tích hợp Sentry được kích hoạt khi có SENTRY_DSN.
"""
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.logging import request_id_ctx
from app.main import app

client = TestClient(app)


# ============================================================================
# 1. TEST /healthz KIỂM TRA DATABASE
# ============================================================================

def test_healthz_endpoint_healthy():
    """Endpoint /healthz kiểm tra DB thành công trả về 200 OK và status ok."""
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert "app_env" in data
    assert "version" in data


def test_healthz_endpoint_database_down():
    """Nếu kết nối cơ sở dữ liệu bị ngắt, /healthz phải trả về 503 Service Unavailable."""
    with patch("sqlite3.connect", side_effect=Exception("Database lock error or timeout")):
        res = client.get("/healthz")
        assert res.status_code == 503
        data = res.json()
        assert data["status"] == "unhealthy"
        assert data["database"] == "disconnected"
        assert "error" in data


# ============================================================================
# 2. TEST REQUEST-ID TRONG HEADER VÀ LOGGING
# ============================================================================

def test_request_id_generated_and_returned():
    """Nếu client không gửi X-Request-ID, server tự động sinh và trả về trong header."""
    res = client.get("/healthz")
    assert res.status_code == 200
    assert "x-request-id" in res.headers
    assert len(res.headers["x-request-id"]) >= 8


def test_request_id_preserved_when_provided():
    """Nếu client gửi X-Request-ID, server phải bảo toàn ID đó trong response header."""
    custom_req_id = "test-custom-request-id-12345"
    res = client.get("/healthz", headers={"X-Request-ID": custom_req_id})
    assert res.status_code == 200
    assert res.headers.get("x-request-id") == custom_req_id


def test_request_id_in_log_formatter():
    """Kiểm tra ContextVar request_id được inject vào log formatter."""
    from app.core.logging import SimpleFormatter
    import logging

    token = request_id_ctx.set("req-test-999")
    try:
        formatter = SimpleFormatter(fmt="[%(levelname)s] [%(request_id)s]: %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Test log message",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "[req-test-999]" in formatted
    finally:
        request_id_ctx.reset(token)


# ============================================================================
# 3. TEST TỪ CHỐI KHỞI ĐỘNG KHI APP_ENV=production NẾU KHÔNG AN TOÀN
# ============================================================================

def test_production_rejects_demo_data():
    """APP_ENV=production bắt buộc DEMO_DATA=False. Nếu DEMO_DATA=True -> từ chối khởi động."""
    with pytest.raises(ValueError) as excinfo:
        Settings(
            APP_ENV="production",
            DEMO_DATA=True,
            SECRET_KEY="a-very-secure-random-production-secret-key-32chars",
            PAYMENT_WEBHOOK_SECRET="a-very-secure-random-webhook-secret-32chars",
            VNPAY_HASH_SECRET="prod-vnpay-hash-secret",
            PUBLIC_BASE_URL="https://aurastudio.vn",
            DATABASE_URL="postgresql://user:pass@localhost:5432/auradb",
            VNPAY_RETURN_URL="https://aurastudio.vn/payment/return",
            EMAIL_BACKEND="smtp",
        )
    assert "DEMO_DATA=true không được phép trong môi trường Production" in str(excinfo.value)


def test_production_rejects_default_secret_key():
    """APP_ENV=production bắt buộc SECRET_KEY không được là giá trị mặc định dev."""
    with pytest.raises(ValueError) as excinfo:
        Settings(
            APP_ENV="production",
            DEMO_DATA=False,
            SECRET_KEY="dev-only-change-me",
            PAYMENT_WEBHOOK_SECRET="a-very-secure-random-webhook-secret-32chars",
            VNPAY_HASH_SECRET="prod-vnpay-hash-secret",
            PUBLIC_BASE_URL="https://aurastudio.vn",
            DATABASE_URL="postgresql://user:pass@localhost:5432/auradb",
            VNPAY_RETURN_URL="https://aurastudio.vn/payment/return",
            EMAIL_BACKEND="smtp",
        )
    assert "SECRET_KEY chưa được đặt hoặc vẫn dùng giá trị mặc định" in str(excinfo.value)


def test_production_rejects_short_secret_key():
    """APP_ENV=production bắt buộc SECRET_KEY tối thiểu 16 ký tự."""
    with pytest.raises(ValueError) as excinfo:
        Settings(
            APP_ENV="production",
            DEMO_DATA=False,
            SECRET_KEY="short-secret",
            PAYMENT_WEBHOOK_SECRET="a-very-secure-random-webhook-secret-32chars",
            VNPAY_HASH_SECRET="prod-vnpay-hash-secret",
            PUBLIC_BASE_URL="https://aurastudio.vn",
            DATABASE_URL="postgresql://user:pass@localhost:5432/auradb",
            VNPAY_RETURN_URL="https://aurastudio.vn/payment/return",
            EMAIL_BACKEND="smtp",
        )
    assert "SECRET_KEY chưa được đặt hoặc vẫn dùng giá trị mặc định" in str(excinfo.value)


# ============================================================================
# 4. TEST SENTRY INITIALIZATION KHI CÓ SENTRY_DSN
# ============================================================================

def test_sentry_initialization_with_dsn():
    """Kiểm tra sentry_sdk.init được gọi khi SENTRY_DSN được cấu hình."""
    import sentry_sdk
    from app.config import settings

    with patch.object(sentry_sdk, "init") as mock_sentry_init:
        test_dsn = "https://examplePublicKey@o0.ingest.sentry.io/0"
        with patch.object(settings, "SENTRY_DSN", test_dsn):
            sentry_sdk.init(
                dsn=settings.SENTRY_DSN,
                environment="production",
                traces_sample_rate=0.1,
            )
            mock_sentry_init.assert_called_once()
            args, kwargs = mock_sentry_init.call_args
            assert kwargs["dsn"] == test_dsn
            assert kwargs["environment"] == "production"
