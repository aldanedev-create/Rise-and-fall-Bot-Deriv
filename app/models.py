from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


class Signal(db.Model):
    __tablename__ = "signals"

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(32), nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=False)
    direction = db.Column(db.String(8), nullable=False)
    confidence = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    indicators = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def to_dict(self) -> dict:
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return {
            "id": self.id,
            "symbol": self.symbol,
            "display_name": self.display_name,
            "direction": self.direction,
            "confidence": round(self.confidence, 1),
            "price": self.price,
            "reason": self.reason,
            "indicators": self.indicators or {},
            "created_at": created_at.isoformat(),
        }
