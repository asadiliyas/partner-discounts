import asyncio
import json
from pathlib import Path

from app.gateway import SIGNATURE_HEADER, StubGateway
from app.repositories import orders as orders_repo
from tests.race import hold_at_barrier

FIXTURES = json.loads((Path(__file__).resolve().parents[1] / "seed" / "fixtures.json").read_text())
APPLY = {s["name"]: s for s in FIXTURES["apply_discount"]}
WEBHOOKS = {s["name"]: s for s in FIXTURES["webhooks"]}

gateway = StubGateway()  # signs with the same secret the app verifies with


def encode(body: dict) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode()


def signed(body: dict | bytes) -> tuple[dict, bytes]:
    raw = body if isinstance(body, bytes) else encode(body)
    return {SIGNATURE_HEADER: gateway.sign(raw), "Content-Type": "application/json"}, raw


async def deliver(client, headers: dict, raw: bytes):
    return await client.post("/webhooks/payment", content=raw, headers=headers)


def success_event(order_id: str, amount: int, payment_id: str = "pay_test_1") -> dict:
    return {
        "event": "payment.succeeded",
        "payment_id": payment_id,
        "order_id": order_id,
        "amount_paise": amount,
    }


async def payments_for(db, order_id: str) -> list[dict]:
    return await db["payments"].find({"order_id": order_id}).to_list(None)


async def test_webhook_1_duplicate_delivery_pays_once(client, db):
    s = WEBHOOKS["webhook_1"]
    headers, raw = signed(s["body"])

    responses = [await deliver(client, headers, raw) for _ in range(s["times_delivered"])]

    assert [r.status_code for r in responses] == [200, 200]
    assert [r.json()["outcome"] for r in responses] == ["paid", "paid"]
    assert [r.json()["duplicate"] for r in responses] == [False, True]

    order = await db["orders"].find_one({"_id": "ord_c_3003"})
    assert order["status"] == "paid"
    assert order["payment_id"] == "pay_c_9001"
    assert order["paid_at"] is not None
    payments = await payments_for(db, "ord_c_3003")
    assert len(payments) == 1
    assert payments[0]["_id"] == "pay_c_9001"
    assert payments[0]["amount_paise"] == 45900
    assert payments[0]["outcome"] == "paid"


async def test_concurrent_deliveries_from_the_gateway_pay_once(client, db, monkeypatch):
    payment = gateway.create_payment("ord_c_3003", 45900)
    deliveries = gateway.deliveries(payment)
    # Both deliveries see the order as pending before either updates it.
    hold_at_barrier(monkeypatch, orders_repo, "update_if_pending")

    responses = await asyncio.gather(*(deliver(client, h, b) for h, b in deliveries))

    assert all(r.status_code == 200 for r in responses)
    assert all(r.json()["outcome"] == "paid" for r in responses)
    assert sorted(r.json()["duplicate"] for r in responses) == [False, True]
    order = await db["orders"].find_one({"_id": "ord_c_3003"})
    assert order["status"] == "paid"
    assert order["payment_id"] == payment["payment_id"]
    assert len(await payments_for(db, "ord_c_3003")) == 1


async def test_two_different_payments_racing_for_one_order(client, db, monkeypatch):
    hold_at_barrier(monkeypatch, orders_repo, "update_if_pending")
    responses = await asyncio.gather(
        deliver(client, *signed(success_event("ord_c_3003", 45900, "pay_a"))),
        deliver(client, *signed(success_event("ord_c_3003", 45900, "pay_b"))),
    )

    outcomes = {r.json()["payment_id"]: r.json()["outcome"] for r in responses}
    assert sorted(outcomes.values()) == ["order_not_payable", "paid"]
    winner = next(pid for pid, outcome in outcomes.items() if outcome == "paid")
    order = await db["orders"].find_one({"_id": "ord_c_3003"})
    assert order["status"] == "paid"
    assert order["payment_id"] == winner
    assert len(await payments_for(db, "ord_c_3003")) == 2


async def test_webhook_2_amount_differs_from_discounted_total(client, db):
    # The customer was charged the pre-discount amount after KIDS18 was applied.
    apply_2 = APPLY["apply_2"]
    response = await client.post(
        f"/orders/{apply_2['order_id']}/apply-discount", json={"code": apply_2["codes"][0]}
    )
    assert response.status_code == 200
    assert response.json()["total_paise"] == 12300

    s = WEBHOOKS["webhook_2"]
    response = await deliver(client, *signed(s["body"]))

    # Acknowledged (retrying cannot fix it) but the order is NOT marked paid.
    assert response.status_code == 200
    assert response.json()["outcome"] == "amount_mismatch"
    order = await db["orders"].find_one({"_id": "ord_c_3001"})
    assert order["status"] == "payment_review"
    assert order["payment_id"] == "pay_c_9002"
    assert order.get("paid_at") is None
    [payment] = await payments_for(db, "ord_c_3001")
    assert payment["amount_paise"] == 14999
    assert payment["outcome"] == "amount_mismatch"

    # A redelivery reports the same thing and changes nothing.
    again = await deliver(client, *signed(s["body"]))
    assert again.json() == {**response.json(), "duplicate": True}


async def test_payment_matching_the_discounted_total_marks_paid(client, db):
    await client.post("/orders/ord_c_3001/apply-discount", json={"code": "KIDS18"})
    response = await deliver(client, *signed(success_event("ord_c_3001", 12300)))
    assert response.json()["outcome"] == "paid"
    order = (await client.get("/orders/ord_c_3001")).json()
    assert order["status"] == "paid"
    assert order["discount"]["code"] == "KIDS18"


async def test_discount_cannot_be_applied_after_payment(client):
    await deliver(client, *signed(success_event("ord_c_3001", 14999)))
    response = await client.post("/orders/ord_c_3001/apply-discount", json={"code": "KIDS18"})
    assert response.status_code == 409
    assert (await client.get("/orders/ord_c_3001")).json()["total_paise"] == 14999


async def test_second_payment_for_a_paid_order_is_recorded_not_applied(client, db):
    first = await deliver(client, *signed(success_event("ord_c_3003", 45900, "pay_first")))
    second = await deliver(client, *signed(success_event("ord_c_3003", 45900, "pay_second")))

    assert first.json()["outcome"] == "paid"
    assert second.status_code == 200
    assert second.json()["outcome"] == "order_not_payable"
    order = await db["orders"].find_one({"_id": "ord_c_3003"})
    assert order["payment_id"] == "pay_first"
    assert {p["_id"] for p in await payments_for(db, "ord_c_3003")} == {"pay_first", "pay_second"}


async def test_payment_for_unknown_order_is_recorded(client, db):
    response = await deliver(client, *signed(success_event("ord_missing", 100, "pay_orphan")))
    assert response.status_code == 200
    assert response.json()["outcome"] == "order_not_found"
    assert (await db["payments"].find_one({"_id": "pay_orphan"}))["outcome"] == "order_not_found"


async def test_redelivery_finishes_a_payment_that_was_recorded_but_not_settled(client, db):
    # Simulates a crash after the payment insert but before the order update.
    await db["payments"].insert_one(
        {"_id": "pay_crash", "order_id": "ord_c_3003", "amount_paise": 45900, "outcome": None}
    )
    response = await deliver(client, *signed(success_event("ord_c_3003", 45900, "pay_crash")))
    assert response.status_code == 200
    assert response.json() == {
        "payment_id": "pay_crash",
        "order_id": "ord_c_3003",
        "outcome": "paid",
        "duplicate": True,
    }
    assert (await db["orders"].find_one({"_id": "ord_c_3003"}))["status"] == "paid"
    assert (await db["payments"].find_one({"_id": "pay_crash"}))["outcome"] == "paid"


async def test_reused_payment_id_with_different_amount_is_rejected(client, db):
    await deliver(client, *signed(success_event("ord_c_3003", 45900, "pay_same")))
    response = await deliver(client, *signed(success_event("ord_c_3003", 1, "pay_same")))
    assert response.status_code == 409
    assert (await db["payments"].find_one({"_id": "pay_same"}))["amount_paise"] == 45900


async def test_bad_signature_is_rejected_and_nothing_is_recorded(client, db):
    raw = encode(success_event("ord_c_3003", 45900))
    wrong = StubGateway(webhook_secret="whsec_not_ours").sign(raw)
    response = await deliver(client, {SIGNATURE_HEADER: wrong}, raw)
    assert response.status_code == 401
    assert await db["payments"].count_documents({}) == 0
    assert (await db["orders"].find_one({"_id": "ord_c_3003"}))["status"] == "pending"


async def test_missing_signature_is_rejected(client, db):
    response = await deliver(client, {}, encode(success_event("ord_c_3003", 45900)))
    assert response.status_code == 401
    assert await db["payments"].count_documents({}) == 0


async def test_body_changed_after_signing_is_rejected(client, db):
    headers, _ = signed(success_event("ord_c_3003", 45900))
    tampered = encode(success_event("ord_c_3003", 100))
    response = await deliver(client, headers, tampered)
    assert response.status_code == 401
    assert (await db["orders"].find_one({"_id": "ord_c_3003"}))["status"] == "pending"


async def test_non_integer_amount_is_rejected(client, db):
    raw = b'{"event":"payment.succeeded","payment_id":"pay_x","order_id":"ord_c_3003","amount_paise":459.0}'
    response = await deliver(client, *signed(raw))
    assert response.status_code == 400
    assert await db["payments"].count_documents({}) == 0


async def test_malformed_json_is_rejected(client):
    response = await deliver(client, *signed(b"{not json"))
    assert response.status_code == 400


async def test_other_events_are_acknowledged_and_ignored(client, db):
    body = {**success_event("ord_c_3003", 45900), "event": "payment.failed"}
    response = await deliver(client, *signed(body))
    assert response.status_code == 200
    assert response.json()["outcome"] == "ignored"
    assert await db["payments"].count_documents({}) == 0
    assert (await db["orders"].find_one({"_id": "ord_c_3003"}))["status"] == "pending"
