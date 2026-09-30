"""Wire and storage models. All money is integer paise."""

from datetime import datetime

from pydantic import BaseModel, Field, StrictInt


class OrderItem(BaseModel):
    sku: str
    name: str
    unit_price_paise: int = Field(ge=0)
    quantity: int = Field(ge=1)


class AppliedDiscount(BaseModel):
    """What was applied to an order, frozen at the time it was applied.

    The code's terms (percentage, cap, funding split) are copied onto the
    order so that later changes to the code, or to a partner's split, never
    change what this order owes. ``partner_funded_paise`` +
    ``rayy_funded_paise`` = ``amount_paise``.
    """

    code: str
    percent_off_bps: int
    cap_paise: int
    amount_paise: int = Field(ge=0)
    partner_share_bps: int
    rayy_share_bps: int
    partner_funded_paise: int = Field(ge=0)
    rayy_funded_paise: int = Field(ge=0)
    applied_at: datetime


class Order(BaseModel):
    order_id: str
    partner_id: str
    items: list[OrderItem]
    subtotal_paise: int = Field(ge=0)
    total_paise: int = Field(ge=0)
    currency: str = "INR"
    status: str
    created_at: datetime
    discount: AppliedDiscount | None = None
    payment_id: str | None = None
    paid_at: datetime | None = None


class DiscountCode(BaseModel):
    """A partner discount code.

    ``percent_off_bps`` is the percentage in basis points (1500 = 15%).
    ``cap_paise`` is the most the code can take off one order.
    ``partner_share_bps`` + ``rayy_share_bps`` = 10000 and describe who funds
    the discount (7000 / 3000 is a 70/30 split).
    """

    code: str
    percent_off_bps: int = Field(gt=0, le=10000)
    cap_paise: int = Field(gt=0)
    expires_at: datetime
    partner_share_bps: int = Field(ge=0, le=10000)
    rayy_share_bps: int = Field(ge=0, le=10000)


class ApplyDiscountRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class PaymentWebhook(BaseModel):
    """Body of a gateway webhook; see ``app/gateway.py``."""

    event: str
    payment_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    # Strict: a float like 123.45 (or even 123.0) is a malformed amount, not paise.
    amount_paise: StrictInt = Field(ge=0)


class PaymentWebhookResult(BaseModel):
    payment_id: str
    order_id: str
    outcome: str
    duplicate: bool = False
