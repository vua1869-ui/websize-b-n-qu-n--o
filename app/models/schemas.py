import datetime
import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


# ==========================================
# Sản phẩm
# ==========================================
class ProductColor(BaseModel):
    name: str
    hex: str


class ProductVariant(BaseModel):
    id: Optional[int] = None
    product_id: Optional[str] = None
    color: str
    color_hex: Optional[str] = None
    size: str
    stock: int = 10
    sku: Optional[str] = None


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
    flash_sale_start: Optional[datetime.datetime] = None
    flash_sale_end: Optional[datetime.datetime] = None
    is_active: bool = True
    deleted_at: Optional[str] = None
    sold_count: int = 0
    stock_total: int = 100
    stock: int = 50
    rating: float = 0.0
    reviews_count: int = 0
    location: str = "TP. Hồ Chí Minh"
    images: List[str]
    sizes: List[str]
    colors: List[ProductColor]
    variants: Optional[List[ProductVariant]] = None
    description: str
    material: str
    style: str
    occasions: List[str]
    tags: List[str]
    is_hot: bool = False
    is_new: bool = False

    @computed_field  # type: ignore[misc]
    @property
    def is_in_flash_sale(self) -> bool:
        if not (self.flash_sale and self.flash_sale_price):
            return False
        if not (self.flash_sale_start and self.flash_sale_end):
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        start = self.flash_sale_start
        end = self.flash_sale_end
        if start.tzinfo is None:
            start = start.replace(tzinfo=datetime.timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=datetime.timezone.utc)
        return start <= now <= end

    @computed_field  # type: ignore[misc]
    @property
    def final_price(self) -> int:
        """Giá khách thực sự phải trả (server là nguồn sự thật duy nhất)."""
        if self.is_in_flash_sale and self.flash_sale_price:
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
    expires_at: Optional[datetime.datetime] = None
    max_uses: Optional[int] = None
    max_uses_per_user: Optional[int] = None

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
    messages: List[ChatMessage] = Field(default=[], max_length=20)  # tối đa 20 lượt hội thoại gần nhất
    user_message: str = Field(min_length=1, max_length=1000)
    engine: Optional[str] = None  # Giữ tương thích frontend; server bỏ qua giá trị này và dùng config
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
    use_points: int = Field(default=0, ge=0)


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
    points_used: int = 0
    points_discount: int = 0
    points_earned: int = 0
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
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.]{3,30}$")
EMAIL_RE = re.compile(r"^[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+$")


class OrderCancelRequest(BaseModel):
    phone: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=250)


class OrderCreateRequest(BaseModel):
    customer_name: str = Field(min_length=2, max_length=80)
    customer_phone: str
    customer_email: Optional[str] = Field(default=None, max_length=120)
    customer_address: Optional[str] = Field(default=None, max_length=250)
    customer_note: Optional[str] = Field(default=None, max_length=300)
    province_code: Optional[Union[int, str]] = None
    ward_code: Optional[Union[int, str]] = None
    province_name: Optional[str] = Field(default=None, max_length=100)
    ward_name: Optional[str] = Field(default=None, max_length=100)
    province: Optional[str] = Field(default=None, max_length=100)
    ward: Optional[str] = Field(default=None, max_length=100)
    specific_address: Optional[str] = Field(default=None, max_length=250)
    payment_method: Literal["cod", "qr_transfer", "vnpay"] = "cod"
    items: List[OrderItem] = Field(min_length=1, max_length=50)
    voucher_code: Optional[str] = Field(default=None, max_length=30)
    use_points: int = Field(default=0, ge=0)

    @field_validator("customer_name")
    @classmethod
    def _strip_and_check_name(cls, v: str) -> str:
        v = v.strip()
        if "<" in v or ">" in v:
            raise ValueError("Tên khách hàng không được chứa ký tự < hoặc >")
        return v

    @field_validator("customer_email")
    @classmethod
    def _validate_customer_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not EMAIL_RE.match(v):
            raise ValueError("Email không hợp lệ (ví dụ: customer@example.com)")
        return v.lower()

    @field_validator("customer_note", "specific_address", "customer_address")
    @classmethod
    def _validate_safe_order_text(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if "<" in v or ">" in v:
                raise ValueError("Nội dung không được chứa ký tự < hoặc >")
            return v.strip()
        return None

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s.\-]", "", v)
        if not PHONE_RE.match(cleaned):
            raise ValueError("Số điện thoại không hợp lệ (ví dụ: 0987654321)")
        return cleaned

    @model_validator(mode="after")
    def _validate_locations_and_address(self) -> "OrderCreateRequest":
        from app.services.geo_service import geo_service
        # Nếu gửi province_code hoặc ward_code: bắt buộc kiểm tra theo địa giới 2 cấp mới
        if self.province_code is not None or self.ward_code is not None:
            if self.province_code is None or self.ward_code is None:
                raise ValueError("Vui lòng chọn đầy đủ Tỉnh/Thành phố và Xã/Phường")
            valid, err_msg, prov_obj, ward_obj = geo_service.validate_location(self.province_code, self.ward_code)
            if not valid:
                raise ValueError(err_msg or "Mã xã/phường không thuộc tỉnh/thành phố đã chọn")
            
            # Gán tên chính thức đã tra cứu từ dữ liệu chuẩn
            self.province_name = prov_obj["name"]
            self.ward_name = ward_obj["name"]
            self.province = prov_obj["name"]
            self.ward = ward_obj["name"]

            # Xây dựng địa chỉ đầy đủ dạng chuỗi hiển thị được
            street = (self.specific_address or "").strip()
            if street:
                self.customer_address = f"{street}, {self.ward_name}, {self.province_name}"
            elif not self.customer_address:
                self.customer_address = f"{self.ward_name}, {self.province_name}"

        # Kiểm tra customer_address tối thiểu
        if not self.customer_address or len(self.customer_address.strip()) < 8:
            raise ValueError("Vui lòng cung cấp địa chỉ nhận hàng đầy đủ")

        self.customer_address = self.customer_address.strip()
        return self


class OrderResponse(BaseModel):
    order_id: str
    status: str
    payment_status: str = "unpaid"
    carrier: str = "Giao Hàng Nhanh (GHN Express)"
    tracking_code: Optional[str] = None
    shipping_status: Optional[str] = "ready_to_pick"
    estimated_delivery: Optional[str] = None
    quote: QuoteResponse
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    customer_address: str
    payment_method: str
    created_at: str
    message: str
    qr_code_url: Optional[str] = None
    bank_info: Optional[Dict[str, str]] = None


# ==========================================
# Đánh giá & Bằng chứng Xã hội (Phase 2)
# ==========================================
class ProductReviewCreate(BaseModel):
    user_name: Optional[str] = Field(default=None, max_length=50)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(min_length=3, max_length=1000)
    height_cm: Optional[float] = Field(default=None, ge=100, le=230)
    weight_kg: Optional[float] = Field(default=None, ge=30, le=200)
    purchased_size: Optional[str] = None
    purchased_color: Optional[str] = None
    fit_feedback: Optional[str] = "Vừa vặn"


class ProductReviewItem(BaseModel):
    id: int
    product_id: str
    user_name: str
    rating: int
    comment: str
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    purchased_size: Optional[str] = None
    purchased_color: Optional[str] = None
    fit_feedback: Optional[str] = "Vừa vặn"
    is_verified_buyer: bool = False
    likes_count: int = 0
    created_at: str


class ProductReviewsResponse(BaseModel):
    summary: Dict[str, Any]
    reviews: List[ProductReviewItem]


# ==========================================
# Bảng số đo & Hướng dẫn chọn size (Phase 2)
# ==========================================
class SizeChartResponse(BaseModel):
    product_id: str
    product_name: str
    category_name: str
    unit: str = "cm"
    columns: List[str]
    rows: List[Dict[str, Any]]
    measuring_guide: List[Dict[str, str]]
    care_instructions: List[str]


# ==========================================
# Tra cứu Vận đơn & Logistics (Phase 2)
# ==========================================
class TrackingTimelineStep(BaseModel):
    key: str
    title: str
    description: str
    location: str
    time: str
    status: Literal["completed", "current", "pending"]


class OrderTrackingResponse(BaseModel):
    order_id: str
    tracking_code: str
    carrier: str
    shipping_status: str
    shipping_status_label: str
    estimated_delivery: str
    customer_name: str
    customer_phone: str
    customer_address: str
    timeline: List[TrackingTimelineStep]
    items: List[Dict[str, Any]]
    total_amount: int
    payment_method: str
    payment_status: str
    created_at: str


# ==========================================
# Hóa đơn điện tử E-Invoice (Phase 2)
# ==========================================
class InvoiceResponse(BaseModel):
    title: str = "Phiếu thông tin đơn hàng"
    legal_note: str = "Lưu ý: Đây là phiếu thông tin đơn hàng nội bộ, không phải hóa đơn điện tử giá trị gia tăng hợp lệ."
    invoice_number: str
    order_id: str
    issued_at: str
    seller: Dict[str, str]
    buyer: Dict[str, str]
    items: List[Dict[str, Any]]
    subtotal: int
    shipping_fee: int
    discount_amount: int
    vat_rate: int = 8
    vat_amount: int
    total_amount: int
    payment_method: str
    payment_status: str
    carrier: str
    tracking_code: Optional[str] = "Chưa có mã vận đơn"


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


# ==========================================
# Khách hàng thân thiết & Điểm thưởng AURA Club (Phase 3)
# ==========================================
class LoyaltyStatusResponse(BaseModel):
    user_id: str
    user_name: str
    tier: str
    tier_name: str
    tier_badge: str
    points_balance: int
    points_value_vnd: int
    total_spent: int
    earn_rate_percent: int
    free_shipping_all_orders: bool
    next_tier: Optional[str] = None
    next_tier_name: Optional[str] = None
    next_tier_spent_needed: int = 0
    progress_percent: int = 0
    benefits: List[str] = []


class LoyaltyTransactionItem(BaseModel):
    id: int
    user_id: str
    order_id: Optional[str] = None
    points: int
    type: str
    description: str
    balance_after: int
    created_at: str


class LoyaltyHistoryResponse(BaseModel):
    points_balance: int
    total_spent: int
    tier: str
    transactions: List[LoyaltyTransactionItem]


# ==========================================
# Người dùng & Xác thực (Authentication & Users)
# ==========================================
class User(BaseModel):
    id: str
    name: str = ""
    full_name: Optional[str] = None
    username: str
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    role: Literal["admin", "user"] = "user"
    status: Literal["active", "disabled"] = "active"
    is_active: bool = True
    avatar: Optional[str] = None
    points_balance: int = 0
    total_spent: int = 0
    tier: str = "Silver"
    created_at: str = ""
    token_version: int = Field(default=1, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _sync_fields(cls, data):
        if isinstance(data, dict):
            if "full_name" in data and not data.get("name"):
                data["name"] = data["full_name"]
            elif "name" in data and not data.get("full_name"):
                data["full_name"] = data["name"]
            if "is_active" not in data and "status" in data:
                data["is_active"] = data["status"] == "active"
            elif "status" not in data and "is_active" in data:
                data["status"] = "active" if data["is_active"] else "disabled"
        return data


class UserRegisterRequest(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None
    email: str
    username: str
    password: str = Field(min_length=8)
    confirm_password: Optional[str] = None
    phone: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sync_fields(cls, data):
        if isinstance(data, dict):
            if "full_name" in data and not data.get("name"):
                data["name"] = data["full_name"]
            elif "name" in data and not data.get("full_name"):
                data["full_name"] = data["name"]
        return data

    @model_validator(mode="after")
    def _validate_passwords(self):
        if len(self.password.encode("utf-8")) > 72:
            raise ValueError("Mật khẩu không được vượt quá 72 byte")
        if self.confirm_password is not None and self.password != self.confirm_password:
            raise ValueError("Mật khẩu xác nhận không khớp với mật khẩu đã nhập")
        return self

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        v = v.strip()
        if not USERNAME_RE.match(v):
            raise ValueError("Tên đăng nhập chỉ chứa chữ cái, số, dấu chấm hoặc gạch dưới (3-30 ký tự)")
        return v

    @field_validator("email")
    @classmethod
    def _email_format(cls, v: str) -> str:
        v = v.strip()
        if "<" in v or ">" in v or " " in v or not EMAIL_RE.match(v):
            raise ValueError("Email không đúng định dạng hoặc chứa ký tự không hợp lệ")
        return v.lower()

    @field_validator("name", "full_name")
    @classmethod
    def _no_tags_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if "<" in v or ">" in v:
                raise ValueError("Họ và tên không được chứa ký tự < hoặc >")
            return v.strip()
        return None

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        cleaned = re.sub(r"[\s.\-]", "", v)
        if not PHONE_RE.match(cleaned):
            raise ValueError("Số điện thoại không hợp lệ (ví dụ: 0987654321)")
        return cleaned


class UserLoginRequest(BaseModel):
    username_or_email: Optional[str] = None
    username: Optional[str] = None
    password: str = Field(min_length=1, max_length=100)
    remember_me: bool = False

    @model_validator(mode="before")
    @classmethod
    def _compat(cls, data):
        if isinstance(data, dict):
            if "username" in data and not data.get("username_or_email"):
                data["username_or_email"] = data["username"]
            elif "username_or_email" in data and not data.get("username"):
                data["username"] = data["username_or_email"]
        return data


class UserProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = Field(default=None, max_length=250)
    avatar: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sync(cls, data):
        if isinstance(data, dict):
            if "full_name" in data and not data.get("name"):
                data["name"] = data["full_name"]
            elif "name" in data and not data.get("full_name"):
                data["full_name"] = data["name"]
        return data

    @field_validator("name", "full_name", "address")
    @classmethod
    def _no_tags(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if "<" in v or ">" in v:
                raise ValueError("Nội dung không được chứa ký tự < hoặc >")
            return v.strip()
        return None

    @field_validator("email")
    @classmethod
    def _email_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if "<" in v or ">" in v or " " in v or not EMAIL_RE.match(v):
                raise ValueError("Email không đúng định dạng hoặc chứa ký tự không hợp lệ")
            return v.lower()
        return None

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        cleaned = re.sub(r"[\s.\-]", "", v)
        if not PHONE_RE.match(cleaned):
            raise ValueError("Số điện thoại không hợp lệ (ví dụ: 0987654321)")
        return cleaned


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    confirm_password: Optional[str] = None

    @model_validator(mode="after")
    def _validate_passwords(self):
        if len(self.new_password.encode("utf-8")) > 72:
            raise ValueError("Mật khẩu không được vượt quá 72 byte")
        if self.confirm_password is not None and self.new_password != self.confirm_password:
            raise ValueError("Mật khẩu xác nhận không khớp với mật khẩu mới")
        return self


class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _email_format(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Email không đúng định dạng")
        return v.lower().strip()


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    confirm_password: Optional[str] = None

    @model_validator(mode="after")
    def _validate_passwords(self):
        if len(self.new_password.encode("utf-8")) > 72:
            raise ValueError("Mật khẩu không được vượt quá 72 byte")
        if self.confirm_password is not None and self.new_password != self.confirm_password:
            raise ValueError("Mật khẩu xác nhận không khớp với mật khẩu mới")
        return self


class AuthResponse(BaseModel):
    success: bool = True
    message: str = "Thành công"
    user: Optional[User] = None
    token: Optional[str] = None


class AdminProductPayload(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    category_name: Optional[str] = None
    gender: str = "unisex"
    price: Optional[int] = Field(default=None, ge=0)
    original_price: Optional[int] = Field(default=None, ge=0)
    flash_sale: bool = False
    is_flash_sale: bool = False
    flash_sale_price: Optional[int] = Field(default=None, ge=0)
    flash_sale_start: Optional[datetime.datetime] = None
    flash_sale_end: Optional[datetime.datetime] = None
    stock: int = Field(default=0, ge=0)
    stock_total: int = Field(default=0, ge=0)
    rating: float = Field(default=0.0, ge=0.0, le=5.0)
    reviews_count: int = Field(default=0, ge=0)
    location: str = "TP. Hồ Chí Minh"
    image: Optional[str] = None
    images: Optional[List[str]] = None
    sizes: Optional[List[str]] = None
    colors: Optional[List[Any]] = None
    description: Optional[str] = None
    material: Optional[str] = None
    style: Optional[str] = None
    occasion: Optional[str] = None
    occasions: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    variants: Optional[List[ProductVariant]] = None
    is_hot: bool = False
    is_new: bool = False

    @model_validator(mode="after")
    def _validate_prices(self):
        if self.price is not None:
            if self.original_price is not None and self.original_price < self.price:
                raise ValueError("original_price phải lớn hơn hoặc bằng price")
            if self.flash_sale_price is not None and self.flash_sale_price >= self.price:
                raise ValueError("flash_sale_price phải nhỏ hơn price")
        return self


class AdminOrderStatusUpdate(BaseModel):
    status: str = Field(min_length=1)


class AdminUserRoleUpdate(BaseModel):
    role: str = Field(min_length=1)


class AdminUserStatusUpdate(BaseModel):
    status: Optional[str] = None
    is_active: Optional[bool] = None


class CartItemRequest(BaseModel):
    product_id: str = Field(min_length=1)
    size: str = Field(min_length=1)
    color: Optional[str] = None
    quantity: int = Field(default=1, gt=0, le=99)
    combo_token: Optional[str] = None


class CartSyncRequest(BaseModel):
    items: List[CartItemRequest] = Field(default_factory=list, max_length=50)


# ==========================================
# Quản lý Lô Hàng & Báo Cáo Lãi/Lỗ
# ==========================================
class InventoryBatchCreate(BaseModel):
    quantity: int = Field(gt=0, description="Số lượng nhập kho, phải lớn hơn 0")
    cost_price: int = Field(ge=0, description="Giá vốn nhập đơn vị (VNĐ)")
    color: Optional[str] = Field(default=None, description="Màu sắc biến thể (nếu nhập theo biến thể)")
    size: Optional[str] = Field(default=None, description="Kích thước biến thể (nếu nhập theo biến thể)")
    note: Optional[str] = Field(default="", max_length=500)


class InventoryBatchItem(BaseModel):
    id: int
    product_id: str
    product_name: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    quantity: int
    cost_price: int
    received_at: str
    note: Optional[str] = ""
    created_by: Optional[str] = "admin"
    new_stock: Optional[int] = None
    new_stock_total: Optional[int] = None


class ProfitProductBreakdown(BaseModel):
    product_id: str
    product_name: str
    sold_quantity: int
    revenue: int
    cost_price_wac: Optional[float] = None
    cogs: Optional[int] = None
    profit: Optional[int] = None
    margin_percent: Optional[float] = None
    has_cost: bool = True
    note: Optional[str] = None


class ProfitReportResponse(BaseModel):
    total_revenue: int
    total_cogs: int
    gross_profit: int
    profit_margin_percent: float
    total_paid_orders: int
    total_items_sold: int
    products_breakdown: List[ProfitProductBreakdown] = []
    recent_batches: List[Dict[str, Any]] = []
    calculation_method: Optional[str] = None


class PaymentWebhookPayload(BaseModel):
    order_id: Optional[str] = None
    amount: Optional[int] = None
    transaction_code: Optional[str] = None
    channel: Optional[str] = "vietqr"
    content: Optional[str] = None
    transferAmount: Optional[int] = None
    referenceCode: Optional[str] = None


class VNPayCreatePaymentRequest(BaseModel):
    order_id: str
    bank_code: Optional[str] = None
    phone: Optional[str] = None

