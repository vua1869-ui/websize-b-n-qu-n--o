import pytest
from app.config import Settings


def test_production_env_refuses_default_secret_key():
    """Kiểm tra APP_ENV=production từ chối khi SECRET_KEY dùng mặc định dev."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            SECRET_KEY="dev-only-change-me",
            PAYMENT_WEBHOOK_SECRET="valid_prod_webhook_secret_key_99",
            DATABASE_URL="postgresql://user:pass@localhost:5432/aura_prod",
        )
    assert "SECRET_KEY chưa được đặt" in str(exc_info.value)


def test_production_env_refuses_default_webhook_secret():
    """Kiểm tra APP_ENV=production từ chối khi PAYMENT_WEBHOOK_SECRET dùng mặc định dev."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            SECRET_KEY="valid_prod_secret_key_123456789",
            PAYMENT_WEBHOOK_SECRET="dev-payment-webhook-secret",
            DATABASE_URL="postgresql://user:pass@localhost:5432/aura_prod",
        )
    assert "PAYMENT_WEBHOOK_SECRET chưa được đặt" in str(exc_info.value)


def test_production_env_refuses_sqlite_database():
    """Kiểm tra APP_ENV=production từ chối khi DATABASE_URL vẫn dùng SQLite."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            SECRET_KEY="valid_prod_secret_key_123456789",
            PAYMENT_WEBHOOK_SECRET="valid_prod_webhook_secret_key_99",
            DATABASE_URL="sqlite:///aura_store.db",
        )
    assert "DATABASE_URL vẫn dùng SQLite" in str(exc_info.value)


def test_production_env_refuses_localhost_public_base_url():
    """Kiểm tra APP_ENV=production từ chối khi PUBLIC_BASE_URL vẫn là localhost / 127.0.0.1."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            SECRET_KEY="valid_prod_secret_key_123456789",
            PAYMENT_WEBHOOK_SECRET="valid_prod_webhook_secret_key_99",
            DATABASE_URL="postgresql://user:pass@localhost:5432/aura_prod",
            PUBLIC_BASE_URL="http://127.0.0.1:8000"
        )
    assert "PUBLIC_BASE_URL chưa được đặt hoặc vẫn trỏ về 127.0.0.1" in str(exc_info.value)


def test_production_env_refuses_console_email_backend():
    """Kiểm tra APP_ENV=production từ chối EMAIL_BACKEND=console."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            SECRET_KEY="valid_prod_secret_key_123456789",
            PAYMENT_WEBHOOK_SECRET="valid_prod_webhook_secret_key_99",
            DATABASE_URL="postgresql://user:pass@localhost:5432/aura_prod",
            PUBLIC_BASE_URL="https://aurastudio.vn",
            EMAIL_BACKEND="console"
        )
    assert "EMAIL_BACKEND không được dùng 'console' khi triển khai Production" in str(exc_info.value)


def test_production_env_valid_config_passes():
    """Kiểm tra APP_ENV=production khởi động thành công khi cung cấp đầy đủ thông số an toàn."""
    s = Settings(
        APP_ENV="production",
        SECRET_KEY="valid_prod_secret_key_123456789",
        PAYMENT_WEBHOOK_SECRET="valid_prod_webhook_secret_key_99",
        DATABASE_URL="postgresql://user:pass@localhost:5432/aura_prod",
        PUBLIC_BASE_URL="https://aurastudio.vn",
        EMAIL_BACKEND="smtp"
    )
    assert s.APP_ENV == "production"
