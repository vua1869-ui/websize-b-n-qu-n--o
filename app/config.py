import logging
import os

from typing import Optional
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DEFAULT_DB_PATH = os.path.join(BASE_DIR, "app", "data", "aura_store.db").replace(os.sep, "/")


class Settings(BaseSettings):
    """Cấu hình đọc từ biến môi trường hoặc file .env ở thư mục gốc dự án."""

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug_value(cls, value):
        """Accept common environment labels while keeping DEBUG a boolean."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production", "off", "no"}:
                return False
            if normalized in {"debug", "dev", "development", "on", "yes"}:
                return True
        return value

    APP_NAME: str = "AURA STUDIO"
    VERSION: str = "2.1.0"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    APP_ENV: str = "dev"  # dev | production
    DEBUG: bool = False

    # Khóa ký token combo. ĐỔI giá trị này khi triển khai thật.
    SECRET_KEY: str = "dev-only-change-me"

    # Khóa bí mật xác thực webhook thanh toán
    PAYMENT_WEBHOOK_SECRET: str = "dev-payment-webhook-secret"

    # Database connection string (SQLite mặc định, chuyển sang PostgreSQL khi production)
    DATABASE_URL: str = f"sqlite:///{DEFAULT_DB_PATH}"

    # ---- Cổng thanh toán VNPay Sandbox & Refund ----
    # Lấy tại VNPay Merchant Portal (sandbox dùng được ngay, xem tài liệu VNPay)
    VNPAY_TMN_CODE: str = ""
    VNPAY_HASH_SECRET: str = ""  # PHẢI đặt trong .env — xem .env.example
    VNPAY_URL: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    VNPAY_RETURN_URL: str = "http://127.0.0.1:8000/api/payment/vnpay/return"
    VNPAY_API_URL: str = "https://sandbox.vnpayment.vn/merchant_webapi/api/transaction"

    # ---- Cổng thanh toán MoMo Sandbox & Refund (MoMo v2 Gateway API) ----
    MOMO_PARTNER_CODE: str = "MOMO"
    MOMO_ACCESS_KEY: str = "F8BBA842ECF85"
    MOMO_SECRET_KEY: str = "K951B6PE1waDMi640xX08PD3vg6EkVlz"
    MOMO_API_URL: str = "https://test-payment.momo.vn/v2/gateway/api/create"
    MOMO_REFUND_URL: str = "https://test-payment.momo.vn/v2/gateway/api/refund"
    MOMO_RETURN_URL: str = "http://127.0.0.1:8000/api/payment/momo/return"
    MOMO_IPN_URL: str = "http://127.0.0.1:8000/api/payment/momo/ipn"

    # Domain public chính thức của hệ thống (dùng dựng link reset mật khẩu an toàn, không phụ thuộc Host header)
    PUBLIC_BASE_URL: str = "http://127.0.0.1:8000"

    # ---- Sentry Error Tracking ----
    SENTRY_DSN: Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        """Nếu APP_ENV là production: bắt buộc các khóa secret phải được đổi, không dùng SQLite hay 127.0.0.1."""
        if self.APP_ENV.strip().lower() == "production":
            errors = []
            default_secrets = {
                "dev-only-change-me",
                "dev-payment-webhook-secret",
                "secret",
                "changeme",
                "default",
                "admin",
                "123456",
            }
            sec = (self.SECRET_KEY or "").strip()
            if not sec or sec in default_secrets or len(sec) < 16 or sec.lower().startswith("dev-"):
                errors.append("- SECRET_KEY chưa được đặt hoặc vẫn dùng giá trị mặc định dev ('dev-only-change-me'). Bắt buộc tối thiểu 16 ký tự và không dùng secret mặc định.")

            webhook_sec = (self.PAYMENT_WEBHOOK_SECRET or "").strip()
            if not webhook_sec or webhook_sec in default_secrets or webhook_sec.lower().startswith("dev-"):
                errors.append("- PAYMENT_WEBHOOK_SECRET chưa được đặt hoặc vẫn dùng giá trị mặc định dev ('dev-payment-webhook-secret').")

            if not self.VNPAY_HASH_SECRET or not self.VNPAY_HASH_SECRET.strip():
                errors.append("- VNPAY_HASH_SECRET chưa được đặt hoặc bị rỗng. Khóa rỗng cho phép kẻ tấn công giả mạo chữ ký giao dịch.")

            if not self.PUBLIC_BASE_URL or "127.0.0.1" in self.PUBLIC_BASE_URL or "localhost" in self.PUBLIC_BASE_URL.lower():
                errors.append("- PUBLIC_BASE_URL chưa được đặt hoặc vẫn trỏ về 127.0.0.1 hoặc localhost. Cần thay bằng domain chính thức của hệ thống.")

            if "sqlite" in self.DATABASE_URL.lower():
                errors.append("- DATABASE_URL vẫn dùng SQLite. Khi triển khai Production bắt buộc sử dụng cơ sở dữ liệu như PostgreSQL.")

            is_vnpay_localhost = not self.VNPAY_RETURN_URL or "127.0.0.1" in self.VNPAY_RETURN_URL or "localhost" in self.VNPAY_RETURN_URL.lower()
            if is_vnpay_localhost:
                errors.append("- VNPAY_RETURN_URL vẫn trỏ về 127.0.0.1 hoặc localhost. Cần thay bằng domain chính thức của hệ thống.")

            is_momo_localhost = not self.MOMO_RETURN_URL or "127.0.0.1" in self.MOMO_RETURN_URL or "localhost" in self.MOMO_RETURN_URL.lower()
            if is_momo_localhost:
                errors.append("- MOMO_RETURN_URL vẫn trỏ về 127.0.0.1 hoặc localhost. Cần thay bằng domain chính thức của hệ thống.")

            if self.EMAIL_BACKEND == "console":
                errors.append("- EMAIL_BACKEND không được dùng 'console' khi triển khai Production.")

            if bool(self.DEMO_DATA):
                errors.append("- DEMO_DATA=true không được phép trong môi trường Production. Bắt buộc tắt (DEMO_DATA=false).")

            if errors:
                err_msg = (
                    "\n" + "=" * 80 + "\n"
                    "🚨 KHÔNG THỂ KHỞI ĐỘNG CẤU HÌNH SẢN XUẤT (APP_ENV=production):\n"
                    "Phát hiện các thông số cấu hình không an toàn:\n"
                    + "\n".join(errors) + "\n"
                    "Vui lòng bổ sung/thay đổi đầy đủ trong file .env hoặc biến môi trường trước khi chạy.\n"
                    + "=" * 80 + "\n"
                )
                raise ValueError(err_msg)
        return self

    # ---- Logging & Observability ----
    LOG_LEVEL: str = "INFO"

    # ---- Dữ liệu Demo Social Proof ----
    DEMO_DATA: bool = False

    # ---- Cloudinary Image Storage ----
    # Lấy tại cloudinary.com/console
    # CẢNH BÁO: Nếu bạn từng commit key thật, hãy vào Cloudinary Console và Regenerate API Secret ngay!
    CLOUDINARY_CLOUD_NAME: str = ""  # ví dụ: your-cloud-name
    CLOUDINARY_API_KEY: str = ""     # PHẢI đặt trong .env — xem .env.example
    CLOUDINARY_API_SECRET: str = ""  # PHẢI đặt trong .env — xem .env.example

    # ---- AI Stylist ----
    # auto: thử Gemini (nếu có key) -> Ollama -> bộ máy luật nội bộ
    AI_ENGINE: str = "auto"  # auto | gemini | ollama | rules
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "llama3.2:latest"
    OLLAMA_TIMEOUT: float = 45.0
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TIMEOUT: float = 20.0
    AI_PROMPT_MAX_PRODUCTS: int = 30
    AI_MAX_CONCURRENCY: int = 4

    # ---- Bán hàng ----
    SHIPPING_FEE: int = 30000
    FREE_SHIPPING_THRESHOLD: int = 299000
    COMBO_DISCOUNT_PERCENT: int = 15
    MAX_QTY_PER_LINE: int = 10

    # Giới hạn số request AI / phút / IP
    AI_RATE_LIMIT_PER_MIN: int = 30

    # ---- AI Trend Detection ----
    TREND_ENABLED: bool = True
    TREND_COUNTRY: str = "VN"
    TREND_TIMEZONE: int = 420  # Asia/Ho_Chi_Minh (UTC+7, 420 phút)
    TREND_CACHE_TTL: int = 21600  # 6 giờ (giây)
    TREND_LIMIT: int = 10
    TREND_PRODUCT_LIMIT: int = 8
    TREND_SOURCE: str = "auto"  # auto | google_trends | demo
    TREND_REFRESH_TIMEOUT: float = 12.0
    TREND_RISING_THRESHOLD: float = 20.0
    TREND_DECLINING_THRESHOLD: float = -20.0
    TREND_ANCHOR_KEYWORD: str = "thời trang"

    # ---- Tích điểm AURA Club ----
    LOYALTY_EARN_RATE_SILVER: float = 0.03
    LOYALTY_EARN_RATE_GOLD: float = 0.05
    LOYALTY_EARN_RATE_DIAMOND: float = 0.08

    # ---- Hóa đơn & Đơn hàng ----
    SELLER_NAME: str = "CÔNG TY CỔ PHẦN THỜI TRANG AURA STUDIO VIỆT NAM"
    SELLER_TAX_CODE: str = "0317894562"
    SELLER_ADDRESS: str = "Số 186 Hai Bà Trưng, Phường Đa Kao, Quận 1, TP. Hồ Chí Minh"
    SELLER_HOTLINE: str = "1900 8866 (8:00 - 22:00)"
    VAT_RATE: int = 8
    E_INVOICE_PROVIDER: str = ""

    # ---- Thông tin ngân hàng nhận thanh toán VietQR ----
    BANK_ID: str = "MB"
    BANK_ACCOUNT_NO: str = "0900000001"
    BANK_ACCOUNT_NAME: str = "AURA STUDIO"

    # ---- Bảo mật & Proxy ----
    TRUSTED_PROXY_HEADERS: bool = False

    # ---- Email Sender ----
    EMAIL_BACKEND: str = "console"  # console | smtp | resend
    EMAIL_FROM: str = "AURA Studio <no-reply@aurastudio.vn>"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TLS: bool = True
    RESEND_API_KEY: str = ""


settings = Settings()
