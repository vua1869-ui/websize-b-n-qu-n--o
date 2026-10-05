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
            VNPAY_RETURN_URL="https://aurastudio.vn/api/payment/vnpay/return"
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
            VNPAY_RETURN_URL="https://aurastudio.vn/api/payment/vnpay/return"
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
            VNPAY_RETURN_URL="https://aurastudio.vn/api/payment/vnpay/return"
        )
    assert "DATABASE_URL vẫn dùng SQLite" in str(exc_info.value)


def test_production_env_refuses_local_vnpay_return_url():
    """Kiểm tra APP_ENV=production từ chối khi VNPAY_RETURN_URL vẫn trỏ 127.0.0.1 hoặc localhost."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            APP_ENV="production",
            SECRET_KEY="valid_prod_secret_key_123456789",
            PAYMENT_WEBHOOK_SECRET="valid_prod_webhook_secret_key_99",
            DATABASE_URL="postgresql://user:pass@localhost:5432/aura_prod",
            VNPAY_RETURN_URL="http://127.0.0.1:8000/api/payment/vnpay/return"
        )
    assert "VNPAY_RETURN_URL vẫn trỏ về 127.0.0.1" in str(exc_info.value)


def test_production_env_valid_config_passes():
    """Kiểm tra APP_ENV=production khởi động thành công khi cung cấp đầy đủ thông số an toàn."""
    s = Settings(
        APP_ENV="production",
        SECRET_KEY="valid_prod_secret_key_123456789",
        PAYMENT_WEBHOOK_SECRET="valid_prod_webhook_secret_key_99",
        DATABASE_URL="postgresql://user:pass@localhost:5432/aura_prod",
        VNPAY_RETURN_URL="https://aurastudio.vn/api/payment/vnpay/return"
    )
    assert s.APP_ENV == "production"
