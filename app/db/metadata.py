"""SQLAlchemy metadata used exclusively for Alembic autogeneration."""

import sqlalchemy as sa

metadata = sa.MetaData()

users = sa.Table(
    "users", metadata,
    sa.Column("user_id", sa.String(36), primary_key=True),
    sa.Column("email", sa.String(320), nullable=False),
    sa.Column("password_hash", sa.String(255), nullable=False),
    sa.Column("display_name", sa.String(120), nullable=False),
    sa.Column("created_at", sa.String(32), nullable=False),
)
sa.Index("ix_users_email", users.c.email, unique=True)

orders = sa.Table(
    "orders", metadata,
    sa.Column("order_id", sa.String(32), primary_key=True),
    sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id")),
    sa.Column("customer_name", sa.Text(), nullable=False),
    sa.Column("product_name", sa.Text(), nullable=False),
    sa.Column("order_status", sa.String(40), nullable=False),
    sa.Column("payment_status", sa.String(40), nullable=False),
    sa.Column("shipment_status", sa.String(40), nullable=False),
    sa.Column("tracking_number", sa.Text(), server_default=""),
    sa.Column("shipping_provider", sa.Text(), server_default=""),
    sa.Column("purchase_date", sa.String(32), nullable=False),
    sa.Column("estimated_delivery_date", sa.Text(), nullable=False),
    sa.Column("customer_email", sa.String(320)),
    sa.Column("customer_phone", sa.String(64)),
    sa.Column("shipping_address", sa.Text()),
    sa.Column("district", sa.Text()),
    sa.Column("province", sa.Text()),
    sa.Column("postal_code", sa.String(32)),
    sa.Column("total_amount", sa.Float()),
    sa.Column("payment_method", sa.String(64)),
    sa.Column("created_at", sa.String(32)),
    sa.Column("paid_at", sa.String(32)),
    sa.Column("cancellation_reason", sa.Text()),
    sa.Column("cancelled_at", sa.String(32)),
    sa.Column("refund_type", sa.String(32)),
    sa.Column("notification_sent", sa.Integer(), server_default="0"),
    sa.Column("notification_sent_at", sa.String(32)),
    sa.Column("shipped_at", sa.String(32)),
    sa.Column("delivered_at", sa.String(32)),
)

products = sa.Table(
    "products", metadata,
    sa.Column("product_id", sa.String(32), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("short_description", sa.Text(), nullable=False),
    sa.Column("full_description", sa.Text(), nullable=False),
    sa.Column("category", sa.String(120), nullable=False),
    sa.Column("price", sa.Float(), nullable=False),
    sa.Column("original_price", sa.Float()),
    sa.Column("rating", sa.Float(), nullable=False, server_default="0"),
    sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("stock_quantity", sa.Integer(), nullable=False),
    sa.Column("badge", sa.String(80)),
    sa.Column("image_url", sa.Text(), nullable=False),
    sa.Column("thumbnail_urls", sa.Text()),
    sa.Column("features", sa.Text()),
    sa.Column("active", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("created_at", sa.String(32), nullable=False),
)

order_items = sa.Table(
    "order_items", metadata,
    sa.Column("item_id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("order_id", sa.String(32), sa.ForeignKey("orders.order_id"), nullable=False),
    sa.Column("product_id", sa.String(32), sa.ForeignKey("products.product_id"), nullable=False),
    sa.Column("product_name", sa.Text(), nullable=False),
    sa.Column("quantity", sa.Integer(), nullable=False),
    sa.Column("unit_price", sa.Float(), nullable=False),
    sa.Column("line_total", sa.Float(), nullable=False),
)
sa.Index("idx_order_items_order_id", order_items.c.order_id)
