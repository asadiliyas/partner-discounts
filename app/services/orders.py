"""Order business rules. No HTTP here; routes call into this module."""

from datetime import datetime, timezone

from app.models import Order
from app.repositories import discount_codes
from app.repositories import orders as orders_repo
from app.services import pricing

PENDING = "pending"
PAID = "paid"
# Money arrived but did not match the order (see services/payments.py).
PAYMENT_REVIEW = "payment_review"


class OrderNotFound(Exception):
    pass


class DiscountError(Exception):
    pass


class DiscountCodeNotFound(DiscountError):
    pass


class DiscountCodeExpired(DiscountError):
    pass


class DiscountAlreadyApplied(DiscountError):
    """The order already carries a different code. One code per order."""


class OrderNotDiscountable(DiscountError):
    """The order is no longer pending (paid, or under payment review)."""


async def get_order(order_id: str) -> Order:
    doc = await orders_repo.get(order_id)
    if doc is None:
        raise OrderNotFound(order_id)
    return Order(**doc)


async def list_orders(limit: int = 50) -> list[Order]:
    return [Order(**doc) for doc in await orders_repo.list_recent(limit)]


def _normalise_code(raw: str) -> str:
    return raw.strip().upper()


async def apply_discount(order_id: str, raw_code: str, now: datetime | None = None) -> Order:
    """Apply a discount code to a pending order and record what was applied.

    Rules:
    - one code per order; a second, different code is rejected
      (DiscountAlreadyApplied) rather than stacked or swapped;
    - re-sending the code that is already applied is a no-op that returns the
      order, so a client retry after a timeout is safe;
    - only pending orders can be discounted.
    """
    now = now or datetime.now(timezone.utc)
    if await orders_repo.get(order_id) is None:
        raise OrderNotFound(order_id)

    code = discount_codes.get_by_code(_normalise_code(raw_code))
    if code is None:
        raise DiscountCodeNotFound(raw_code)
    if code.expires_at <= now:
        raise DiscountCodeExpired(code.code)

    # Read, decide, then write with a condition that the order is still as we
    # read it. If the write loses a race, the next pass sees why.
    for _ in range(3):
        order = await orders_repo.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)

        existing = order.get("discount")
        if existing is not None:
            if existing["code"] == code.code:
                return Order(**order)
            raise DiscountAlreadyApplied(existing["code"])
        if order["status"] != PENDING:
            raise OrderNotDiscountable(order["status"])

        subtotal = order["subtotal_paise"]
        applied = pricing.build_applied_discount(subtotal, code, now)
        updated = await orders_repo.set_discount_if_none(
            order_id,
            expected_subtotal_paise=subtotal,
            discount=applied.model_dump(),
            total_paise=subtotal - applied.amount_paise,
        )
        if updated is not None:
            return Order(**updated)

    raise RuntimeError(f"order {order_id} kept changing while applying a discount")
