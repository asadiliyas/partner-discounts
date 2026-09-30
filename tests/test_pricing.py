from datetime import datetime, timezone

import pytest

from app.models import DiscountCode
from app.services import pricing


@pytest.mark.parametrize(
    ("subtotal", "bps", "cap", "expected"),
    [
        (14999, 1800, 80000, 2699),  # 2699.82 -> floored
        (38997, 1800, 80000, 7019),  # 7019.46 -> floored
        (38997, 500, 10000, 1949),  # 1949.85 -> floored, never rounded up
        (14999, 6000, 4000, 4000),  # 8999.4 -> capped
        (20000, 1500, 3000, 3000),  # exactly at the cap
        (100, 10000, 50000, 100),  # 100% never exceeds the subtotal
        (0, 1800, 80000, 0),
        (1, 9999, 80000, 0),  # less than a paisa off -> nothing off
    ],
)
def test_discount_amount(subtotal, bps, cap, expected):
    assert pricing.discount_amount(subtotal, bps, cap) == expected


@pytest.mark.parametrize("bad", [149.99, "14999", None, True])
def test_discount_amount_rejects_non_integer_paise(bad):
    with pytest.raises(TypeError):
        pricing.discount_amount(bad, 1800, 80000)


def test_discount_amount_rejects_negative_subtotal():
    with pytest.raises(ValueError):
        pricing.discount_amount(-1, 1800, 80000)


@pytest.mark.parametrize(
    ("amount", "partner_bps", "expected"),
    [
        (2699, 7000, (1889, 810)),  # 1889.3 to partner, remainder to RAYY
        (4000, 5500, (2200, 1800)),
        (1949, 8500, (1656, 293)),  # 1656.65 -> partner floored
        (1, 7000, (0, 1)),
        (1000, 10000, (1000, 0)),
        (1000, 0, (0, 1000)),
    ],
)
def test_split_funding(amount, partner_bps, expected):
    partner, rayy = pricing.split_funding(amount, partner_bps)
    assert (partner, rayy) == expected
    assert partner + rayy == amount


def test_split_always_adds_up():
    for amount in range(0, 5000, 7):
        for bps in (0, 1, 3333, 5000, 6667, 7000, 9999, 10000):
            partner, rayy = pricing.split_funding(amount, bps)
            assert partner + rayy == amount
            assert 0 <= partner <= amount


def test_build_applied_discount_snapshots_the_code():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    code = DiscountCode(
        code="KIDS18",
        percent_off_bps=1800,
        cap_paise=80000,
        expires_at=now,
        partner_share_bps=7000,
        rayy_share_bps=3000,
    )
    applied = pricing.build_applied_discount(14999, code, now)
    assert applied.model_dump() == {
        "code": "KIDS18",
        "percent_off_bps": 1800,
        "cap_paise": 80000,
        "amount_paise": 2699,
        "partner_share_bps": 7000,
        "rayy_share_bps": 3000,
        "partner_funded_paise": 1889,
        "rayy_funded_paise": 810,
        "applied_at": now,
    }
