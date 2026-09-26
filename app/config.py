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
    DEBUG: bool = True

    # Khóa ký token combo. ĐỔI giá trị này khi triển khai thật.
    SECRET_KEY: str = "dev-only-change-me"

    # Khóa bí mật xác thực webhook thanh toán
    PAYMENT_WEBHOOK_SECRET: str = "dev-payment-webhook-secret"

    # Database connection string (SQLite mặc định, dễ dàng chuyển đổi sang PostgreSQL)
    DATABASE_URL: str = f"sqlite:///{DEFAULT_DB_PATH}"

    # ---- Cổng thanh toán VNPay Sandbox ----
    VNPAY_TMN_CODE: str = "2QXUI4J4"
    VNPAY_HASH_SECRET: str = "RAIAVDAKACNZZCGTRTTGGBJQOXZDZXXX"
    VNPAY_URL: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    VNPAY_RETURN_URL: str = "http://127.0.0.1:8000/api/payment/vnpay/return"

    # ---- Cloudinary Image Storage ----
    CLOUDINARY_CLOUD_NAME: str = "jtquct7e"
    CLOUDINARY_API_KEY: str = "743289557242615"
    CLOUDINARY_API_SECRET: str = "3g0sbUI_7_NXZZWb-tIk4a4eqew"

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


settings = Settings()
