from sqlalchemy import (
    Column, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.db.session import Base


class UserDB(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)
    province = Column(String, nullable=True)
    district = Column(String, nullable=True)
    ward = Column(String, nullable=True)
    role = Column(String, default="user", nullable=False)
    status = Column(String, default="active", nullable=False)
    avatar = Column(String, nullable=True)
    points_balance = Column(Integer, default=0, nullable=False)
    total_spent = Column(Integer, default=0, nullable=False)
    tier = Column(String, default="Silver", nullable=False)
    created_at = Column(String, nullable=False)


class OrderDB(Base):
    __tablename__ = "orders"

    order_id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=True)
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=False)
    customer_address = Column(String, nullable=False)
    province = Column(String, nullable=True)
    district = Column(String, nullable=True)
    ward = Column(String, nullable=True)
    specific_address = Column(String, nullable=True)
    customer_note = Column(String, nullable=True)
    payment_method = Column(String, nullable=False)
    payment_status = Column(String, default="unpaid", nullable=False)
    order_status = Column(String, default="pending_payment", index=True, nullable=False)
    carrier = Column(String, default="Giao Hàng Nhanh (GHN Express)", nullable=True)
    tracking_code = Column(String, nullable=True, index=True)
    shipping_status = Column(String, default="ready_to_pick", nullable=True)
    estimated_delivery = Column(String, nullable=True)
    total_amount = Column(Integer, nullable=False)
    subtotal = Column(Integer, nullable=False)
    shipping_fee = Column(Integer, default=0, nullable=False)
    discount_amount = Column(Integer, default=0, nullable=False)
    voucher_code = Column(String, nullable=True)
    quote_json = Column(Text, nullable=True)
    paid_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False, index=True)

    items = relationship("OrderItemDB", back_populates="order", cascade="all, delete-orphan")


class OrderItemDB(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String, ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(String, nullable=False)
    product_name = Column(String, nullable=True)
    color = Column(String, nullable=True)
    size = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Integer, nullable=False)
    line_total = Column(Integer, nullable=False)

    order = relationship("OrderDB", back_populates="items")


class ProductDB(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    category_name = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    price = Column(Integer, nullable=False)
    original_price = Column(Integer, nullable=False)
    flash_sale = Column(Integer, default=0)
    flash_sale_price = Column(Integer, nullable=True)
    sold_count = Column(Integer, default=0)
    stock = Column(Integer, default=50)
    stock_total = Column(Integer, default=100)
    rating = Column(Float, default=5.0)
    reviews_count = Column(Integer, default=0)
    location = Column(String, default="TP. Hồ Chí Minh")
    images = Column(Text, nullable=True)
    sizes = Column(Text, nullable=True)
    colors = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    material = Column(String, nullable=True)
    style = Column(String, nullable=True)
    occasions = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    is_hot = Column(Integer, default=0)
    is_new = Column(Integer, default=0)
    created_at = Column(String, nullable=True)


class ProductVariantDB(Base):
    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    color = Column(String, nullable=False)
    color_hex = Column(String, nullable=True)
    size = Column(String, nullable=False)
    stock = Column(Integer, default=10, nullable=False)
    sku = Column(String, nullable=True)
    created_at = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("product_id", "color", "size", name="uq_variant_pid_color_size"),
    )


class ReviewDB(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, nullable=True)
    user_name = Column(String, nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=False)
    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    purchased_size = Column(String, nullable=True)
    purchased_color = Column(String, nullable=True)
    fit_feedback = Column(String, default="Vừa vặn")
    is_verified_buyer = Column(Integer, default=1)
    likes_count = Column(Integer, default=0)
    created_at = Column(String, nullable=False)


class PaymentTransactionDB(Base):
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String, ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False)
    transaction_code = Column(String, unique=True, nullable=False)
    amount = Column(Integer, nullable=False)
    payment_channel = Column(String, default="vietqr")
    status = Column(String, default="success")
    payload_json = Column(Text, nullable=True)
    created_at = Column(String, nullable=False)


class LoyaltyTransactionDB(Base):
    __tablename__ = "loyalty_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id = Column(String, nullable=True)
    points = Column(Integer, nullable=False)
    type = Column(String, nullable=False)
    description = Column(String, nullable=False)
    balance_after = Column(Integer, nullable=False)
    created_at = Column(String, nullable=False)


class InventoryBatchDB(Base):
    __tablename__ = "inventory_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    cost_price = Column(Integer, nullable=False)
    received_at = Column(String, nullable=False)
    note = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
