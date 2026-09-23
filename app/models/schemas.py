import re
from typing import List, Optional, Dict, Literal

from pydantic import BaseModel, Field, computed_field, field_validator


# ==========================================
# Sản phẩm
# ==========================================
class ProductColor(BaseModel):
    name: str
    hex: str


class Product(BaseModel):
    id: str
    name: str
    category: str
    category_name: str
    gender: str
    price: int
    original_price: int
    flash_sale: bool = False
    flash_sale_price: Optional[int] = None
    sold_count: int = 0
    stock_total: int = 100
    stock: int = 50
    rating: float
    reviews_count: int
    location: str = "TP. Hồ Chí Minh"
    images: List[str]
    sizes: List[str]
    colors: List[ProductColor]
    description: str
    material: str
    style: str
    occasions: List[str]
    tags: List[str]
    is_hot: bool = False
    is_new: bool = False

    @computed_field  # type: ignore[misc]
    @property
    def final_price(self) -> int:
        """Giá khách thực sự phải trả (server là nguồn sự thật duy nhất)."""
        if self.flash_sale and self.flash_sale_price:
            return self.flash_sale_price
        return self.price

    @computed_field  # type: ignore[misc]
    @property
    def discount_percent(self) -> int:
        if self.original_price <= 0:
            return 0
        return max(0, round((1 - self.final_price / self.original_price) * 100))

    @computed_field  # type: ignore[misc]
    @property
    def in_stock(self) -> bool:
        return self.stock > 0


class Category(BaseModel):
    id: str
    name: str
    icon: str
    count: int


class FlashSaleResponse(BaseModel):
    ends_at: int  # epoch milliseconds
    server_now: int  # epoch milliseconds (để client bù lệch đồng hồ)
    items: List[Product]


class Voucher(BaseModel):
    code: str
    title: str
    kind: Literal["amount", "percent", "shipping"]
    value: int = 0  # số tiền (amount) hoặc % (percent)
    max_discount: Optional[int] = None  # trần giảm cho voucher %
    min_order: int = 0
    badge: str
    expire_in: str

    @computed_field  # type: ignore[misc]
    @property
    def discount_display(self) -> str:
        if self.kind == "shipping":
            return "Miễn phí vận chuyển"
        if self.kind == "percent":
            cap = f" (tối đa {self.max_discount // 1000}K)" if self.max_discount else ""
            return f"Giảm {self.value}%{cap}"
        return f"Giảm {self.value // 1000}K"


class VideoItem(BaseModel):
    id: str
    title: str
    author: str
    author_avatar: str
    likes: str
    comments: str
    shares: str
    video_url: str
    poster_image: str
    tagged_product: Product


# ==========================================
# AI
# ==========================================
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = []  # lịch sử hội thoại (không gồm tin nhắn hiện tại)
    user_message: str = Field(min_length=1, max_length=1000)
    engine: Optional[str] = None  # None = theo cấu hình server
    context_product_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    engine_used: str
    recommended_products: List[Product] = []
    quick_suggestions: List[str] = []


class SizeRecommendRequest(BaseModel):
    height_cm: float = Field(ge=100, le=230)
    weight_kg: float = Field(ge=25, le=200)
    gender: Literal["unisex", "nam", "nu"] = "unisex"
    fit_preference: Literal["slim", "regular", "oversize"] = "regular"
    product_id: Optional[str] = None


class SizeRecommendResponse(BaseModel):
    recommended_size: str
    alternative_size: Optional[str] = None
    available_sizes: List[str] = []
    bmi: float
    bmi_category: str
    fit_advice: str
    measurements_estimated: Dict[str, str]
    note: Optional[str] = None


class OutfitRequest(BaseModel):
    product_id: Optional[str] = None
    occasion: Optional[str] = None
    gender: Optional[str] = None
    variant: int = Field(default=0, ge=0, le=20)  # đổi số này để "phối lại"


class OutfitResponse(BaseModel):
    outfit_name: str
    style_concept: str
    style_tip: str
    items: List[Product]
    total_price: int
    discounted_combo_price: int
    discount_percentage: int
    combo_token: str  # chữ ký để server xác nhận ưu đãi combo khi đặt hàng


class LiveCommentRequest(BaseModel):
    user_name: str = Field(default="Khách", max_length=40)
    comment: str = Field(min_length=1, max_length=300)


class LiveCommentResponse(BaseModel):
    host_name: str
    reply: str
    pinned_product: Optional[Product] = None


# ==========================================
# Đơn hàng
# ==========================================
class OrderItem(BaseModel):
    """Client CHỈ gửi định danh + lựa chọn. Giá/tên/ảnh luôn lấy từ server."""

    product_id: str
    size: str
    color: str
    quantity: int = Field(ge=1, le=99)
    combo_token: Optional[str] = None


class QuoteRequest(BaseModel):
    items: List[OrderItem] = Field(min_length=1, max_length=50)
    voucher_code: Optional[str] = Field(default=None, max_length=30)


class QuoteLine(BaseModel):
    product_id: str
    name: str
    image: str
    size: str
    color: str
    quantity: int
    unit_price: int
    line_total: int
    combo: bool = False


class QuoteResponse(BaseModel):
    lines: List[QuoteLine]
    subtotal: int
    combo_discount: int
    voucher_code: Optional[str] = None
    voucher_discount: int
    voucher_message: Optional[str] = None
    shipping_fee: int
    shipping_discount: int
    total: int


class VoucherCheckRequest(BaseModel):
    code: str = Field(max_length=30)
    subtotal: int = Field(ge=0)


class VoucherCheckResponse(BaseModel):
    valid: bool
    message: str


PHONE_RE = re.compile(r"^(?:0|\+?84)\d{9}$")


class OrderCreateRequest(BaseModel):
    customer_name: str = Field(min_length=2, max_length=80)
    customer_phone: str
    customer_address: str = Field(min_length=8, max_length=250)
    customer_note: Optional[str] = Field(default=None, max_length=300)
    payment_method: Literal["cod", "qr_transfer"] = "cod"
    items: List[OrderItem] = Field(min_length=1, max_length=50)
    voucher_code: Optional[str] = Field(default=None, max_length=30)

    @field_validator("customer_name", "customer_address")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s.\-]", "", v)
        if not PHONE_RE.match(cleaned):
            raise ValueError("Số điện thoại không hợp lệ (ví dụ: 0987654321)")
        return cleaned


class OrderResponse(BaseModel):
    order_id: str
    status: str
    quote: QuoteResponse
    customer_name: str
    customer_phone: str
    customer_address: str
    payment_method: str
    created_at: str
    message: str


# ==========================================
# AI Trend Detection & Recommendation
# ==========================================
class TrendItem(BaseModel):
    keyword: str
    score: float
    growth_rate: float
    search_interest: float
    category: Optional[str] = None
    status: Literal["rising", "stable", "declining"]
    source: str  # "google_trends" | "cached" | "demo"
    updated_at: str


class TrendingProduct(BaseModel):
    product: Product
    trend_keyword: str
    trend_score: float
    match_score: float
    final_score: float
    reason: str
    rank: int = 1


class TrendDebugResponse(BaseModel):
    last_update: str
    source: str
    cache_status: str
    total_trends: int
    total_trending_products: int
    cache_age_seconds: float


class TrendRefreshResponse(BaseModel):
    success: bool
    source: str
    trends_count: int
    products_count: int
    updated_at: str
