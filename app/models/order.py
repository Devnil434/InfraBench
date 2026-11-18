"""SQLAlchemy Order model."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime
from app.database import Base


class Order(Base):
    """Represents an order placed in the system."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_name = Column(String(100), nullable=False)
    item_count = Column(Integer, default=1, nullable=False)
    total_amount = Column(Float, nullable=False)
    status = Column(String(50), default="PENDING", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        """Convert order model instance to dictionary."""
        created_str = None
        if self.created_at is not None:
            if hasattr(self.created_at, "isoformat"):
                created_str = self.created_at.isoformat()
            else:
                created_str = str(self.created_at)

        return {
            "id": self.id,
            "customer_name": self.customer_name,
            "item_count": self.item_count,
            "total_amount": round(self.total_amount, 2),
            "status": self.status,
            "created_at": created_str,
        }
