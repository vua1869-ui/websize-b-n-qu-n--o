import os
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DEFAULT_DB_PATH = os.path.join(BASE_DIR, "app", "data", "aura_store.db").replace(os.sep, "/")


class Settings(BaseSettings):
    """Cấu hình đọc từ biến môi trường hoặc file .env ở thư mục gốc dự án."""

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

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

    # ---- Cổng thanh toán VNPay Sandbox ----
    # Lấy tại VNPay Merchant Portal (sandbox dùng được ngay, xem tài liệu VNPay)
    VNPAY_TMN_CODE: str = ""
    VNPAY_HASH_SECRET: str = ""  # PHẢI đặt trong .env — xem .env.example
    VNPAY_URL: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    VNPAY_RETURN_URL: str = "http://127.0.0.1:8000/api/payment/vnpay/return"

    # Domain public chính thức của hệ thống (dùng dựng link reset mật khẩu an toàn, không phụ thuộc Host header)
    PUBLIC_BASE_URL: str = "http://127.0.0.1:8000"

    from pydantic import model_validator

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        """Nếu APP_ENV là production: bắt buộc các khóa secret phải được đổi, không dùng SQLite hay 127.0.0.1."""
        if self.APP_ENV.strip().lower() == "production":
            errors = []
            if not self.SECRET_KEY or self.SECRET_KEY.strip() == "dev-only-change-me" or len(self.SECRET_KEY.strip()) < 16:
                errors.append("- SECRET_KEY chưa được đặt hoặc vẫn dùng giá trị mặc định dev ('dev-only-change-me'). Bắt buộc tối thiểu 16 ký tự.")

            if not self.PAYMENT_WEBHOOK_SECRET or self.PAYMENT_WEBHOOK_SECRET.strip() == "dev-payment-webhook-secret":
                errors.append("- PAYMENT_WEBHOOK_SECRET chưa được đặt hoặc vẫn dùng giá trị mặc định dev ('dev-payment-webhook-secret').")

            if not self.VNPAY_HASH_SECRET or not self.VNPAY_HASH_SECRET.strip():
                errors.append("- VNPAY_HASH_SECRET chưa được đặt hoặc bị rỗng. Khóa rỗng cho phép kẻ tấn công giả mạo chữ ký giao dịch.")

            if not self.PUBLIC_BASE_URL or "127.0.0.1" in self.PUBLIC_BASE_URL or "localhost" in self.PUBLIC_BASE_URL.lower():
                errors.append("- PUBLIC_BASE_URL chưa được đặt hoặc vẫn trỏ về 127.0.0.1 hoặc localhost. Cần thay bằng domain chính thức của hệ thống.")

            if "sqlite" in self.DATABASE_URL.lower():
                errors.append("- DATABASE_URL vẫn dùng SQLite. Khi triển khai Production bắt buộc sử dụng cơ sở dữ liệu như PostgreSQL.")

            if "127.0.0.1" in self.VNPAY_RETURN_URL or "localhost" in self.VNPAY_RETURN_URL:
                errors.append("- VNPAY_RETURN_URL vẫn trỏ về 127.0.0.1 hoặc localhost. Cần thay bằng domain chính thức của hệ thống.")

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

    # ---- Cloudinary Image Storage ----
    # Lấy tại cloudinary.com/console
    # CẢNH BÁO: Nếu bạn từng commit key thật, hãy vào Cloudinary Console và Regenerate API Secret ngay!
    CLOUDINARY_CLOUD_NAME: str = ""  # ví dụ: your-cloud-name
    CLOUDINARY_API_KEY: str = ""     # PHẢI đặt trong .env — xem .env.example
    CLOUDINARY_API_SECRET: str = ""  # PHẢI đặt trong .env — xem .env.example

    # ---- AI ----
    # auto: thử Gemini (nếu có key) -> Ollama -> bộ máy luật nội bộ
    AI_ENGINE: str = "auto"  # auto | gemini | ollama | rules
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "llama3.2:latest"
    OLLAMA_TIMEOUT: float = 45.0
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TIMEOUT: float = 20.0

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
