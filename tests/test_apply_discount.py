import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.repositories import discount_codes
from app.repositories import orders as orders_repo
from app.services import orders as orders_service
from tests.race import hold_at_barrier

FIXTURES = json.loads((Path(__file__).resolve().parents[1] / "seed" / "fixtures.json").read_text())
SCENARIOS = {s["name"]: s for s in FIXTURES["apply_discount"]}

# Worked by hand from seed/orders.json and seed/discount_codes.json.
DISCOUNT_ON = {
    ("ord_c_3002", "SUPER60"): 4000,  # 60% of 14999 = 8999.4, capped at 4000
    ("ord_c_3001", "KIDS18"): 2699,  # 18% of 14999 = 2699.82, floored
    ("ord_c_3004", "KIDS18"): 2699,
    ("ord_c_3005", "KIDS18"): 7019,  # 18% of 38997 = 7019.46
    ("ord_c_3005", "FIRST5"): 1949,  # 5% of 38997 = 1949.85
}


async def apply(client, order_id, code):
    return await client.post(f"/orders/{order_id}/apply-discount", json={"code": code})


async def get_order(client, order_id):
    response = await client.get(f"/orders/{order_id}")
    assert response.status_code == 200
    return response.json()


async def test_apply_1_code_is_capped(client):
    s = SCENARIOS["apply_1"]
    response = await apply(client, s["order_id"], s["codes"][0])
    assert response.status_code == 200
    body = response.json()
    assert body["discount"]["code"] == "SUPER60"
    assert body["discount"]["amount_paise"] == 4000
    assert body["total_paise"] == 14999 - 4000
    assert body["discount"]["partner_funded_paise"] == 2200
    assert body["discount"]["rayy_funded_paise"] == 1800
    assert await get_order(client, s["order_id"]) == body


async def test_apply_2_records_what_was_applied(client):
    s = SCENARIOS["apply_2"]
    response = await apply(client, s["order_id"], s["codes"][0])
    assert response.status_code == 200
    stored = await get_order(client, s["order_id"])
    discount = stored["discount"]
    assert stored["subtotal_paise"] == 14999
    assert stored["total_paise"] == 12300
    assert stored["status"] == "pending"
    assert {k: v for k, v in discount.items() if k != "applied_at"} == {
        "code": "KIDS18",
        "percent_off_bps": 1800,
        "cap_paise": 80000,
        "amount_paise": 2699,
        "partner_share_bps": 7000,
        "rayy_share_bps": 3000,
        "partner_funded_paise": 1889,
        "rayy_funded_paise": 810,
    }
    assert discount["applied_at"]


async def test_apply_3_expired_code_is_rejected(client):
    s = SCENARIOS["apply_3"]
    response = await apply(client, s["order_id"], s["codes"][0])
    assert response.status_code == 422
    assert response.json()["detail"] == "discount code has expired"
    stored = await get_order(client, s["order_id"])
    assert stored["discount"] is None
    assert stored["total_paise"] == stored["subtotal_paise"]


async def test_apply_4_two_codes_at_the_same_time_only_one_wins(client, monkeypatch):
    s = SCENARIOS["apply_4"]
    assert s["sent"] == "at_the_same_time"
    order_id = s["order_id"]
    # Both requests read the undiscounted order before either writes.
    hold_at_barrier(monkeypatch, orders_repo, "set_discount_if_none")
    responses = await asyncio.gather(*(apply(client, order_id, code) for code in s["codes"]))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409]
    winner = next(r.json() for r in responses if r.status_code == 200)
    winning_code = winner["discount"]["code"]

    stored = await get_order(client, order_id)
    assert stored["discount"]["code"] == winning_code
    assert stored["total_paise"] == 38997 - DISCOUNT_ON[(order_id, winning_code)]


async def test_apply_5_second_code_after_the_first_is_rejected(client):
    s = SCENARIOS["apply_5"]
    assert s["sent"] == "one_after_another"
    first, second = s["codes"]

    response = await apply(client, s["order_id"], first)
    assert response.status_code == 200
    response = await apply(client, s["order_id"], second)
    assert response.status_code == 409
    assert "KIDS18" in response.json()["detail"]

    stored = await get_order(client, s["order_id"])
    assert stored["discount"]["code"] == "KIDS18"
    assert stored["total_paise"] == 14999 - DISCOUNT_ON[("ord_c_3004", "KIDS18")]


async def test_reapplying_the_same_code_is_a_no_op(client):
    first = await apply(client, "ord_c_3001", "KIDS18")
    again = await apply(client, "ord_c_3001", "KIDS18")
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()


async def test_code_lookup_ignores_case_and_whitespace(client):
    response = await apply(client, "ord_c_3001", "  kids18 ")
    assert response.status_code == 200
    assert response.json()["discount"]["code"] == "KIDS18"


async def test_unknown_code_is_rejected(client):
    response = await apply(client, "ord_c_3001", "NOT_A_CODE")
    assert response.status_code == 422
    assert (await get_order(client, "ord_c_3001"))["discount"] is None


async def test_unknown_order_is_404(client):
    response = await apply(client, "ord_does_not_exist", "KIDS18")
    assert response.status_code == 404


@pytest.mark.parametrize("body", [{}, {"code": ""}, {"code": 18}])
async def test_malformed_body_is_422(client, body):
    response = await client.post("/orders/ord_c_3001/apply-discount", json=body)
    assert response.status_code == 422


async def test_paid_order_cannot_be_discounted(client, db):
    await db["orders"].update_one({"_id": "ord_c_3003"}, {"$set": {"status": "paid"}})
    response = await apply(client, "ord_c_3003", "KIDS18")
    assert response.status_code == 409
    stored = await get_order(client, "ord_c_3003")
    assert stored["total_paise"] == 45900
    assert stored["discount"] is None


async def test_code_expires_at_its_expiry_instant(db):
    code = discount_codes.get_by_code("KIDS18")
    with pytest.raises(orders_service.DiscountCodeExpired):
        await orders_service.apply_discount("ord_c_3001", "KIDS18", now=code.expires_at)
    order = await orders_service.apply_discount(
        "ord_c_3001", "KIDS18", now=code.expires_at - timedelta(microseconds=1)
    )
    assert order.discount is not None
