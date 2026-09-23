import os
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
