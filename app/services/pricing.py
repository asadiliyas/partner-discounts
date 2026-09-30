"""Money maths for discounts. Pure functions, integer paise only.

Rounding: a percentage rarely lands on a whole paisa, so we always round the
discount *down* (floor). The customer never gets more than the advertised
percentage, and the result is deterministic across runs and languages.

Funding split: the partner's share is also floored and RAYY takes the
remainder, so partner + RAYY always add up to exactly the discount and the
partner is never charged more than its agreed ratio.
"""

from datetime import datetime

from app.models import AppliedDiscount, DiscountCode

BPS_DENOMINATOR = 10_000


def _require_int(name: str, value: int) -> None:
    # bool is an int subclass; True paise is a bug, not a price.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int (paise), got {type(value).__name__}")


def discount_amount(subtotal_paise: int, percent_off_bps: int, cap_paise: int) -> int:
    """Discount in paise: percentage of the subtotal, floored, then capped."""
    _require_int("subtotal_paise", subtotal_paise)
    _require_int("percent_off_bps", percent_off_bps)
    _require_int("cap_paise", cap_paise)
    if subtotal_paise < 0:
        raise ValueError("subtotal_paise must not be negative")
    raw = subtotal_paise * percent_off_bps // BPS_DENOMINATOR
    return min(raw, cap_paise, subtotal_paise)


def split_funding(amount_paise: int, partner_share_bps: int) -> tuple[int, int]:
    """Split a discount into (partner_paise, rayy_paise)."""
    _require_int("amount_paise", amount_paise)
    _require_int("partner_share_bps", partner_share_bps)
    partner = amount_paise * partner_share_bps // BPS_DENOMINATOR
    return partner, amount_paise - partner


def build_applied_discount(subtotal_paise: int, code: DiscountCode, now: datetime) -> AppliedDiscount:
    amount = discount_amount(subtotal_paise, code.percent_off_bps, code.cap_paise)
    partner, rayy = split_funding(amount, code.partner_share_bps)
    return AppliedDiscount(
        code=code.code,
        percent_off_bps=code.percent_off_bps,
        cap_paise=code.cap_paise,
        amount_paise=amount,
        partner_share_bps=code.partner_share_bps,
        rayy_share_bps=code.rayy_share_bps,
        partner_funded_paise=partner,
        rayy_funded_paise=rayy,
        applied_at=now,
    )
