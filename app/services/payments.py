"""Payment webhook handling. No HTTP here; the route passes the raw body in.

The gateway delivers at least once, sometimes twice in quick succession, so
every step here is safe to repeat:

1. Verify the HMAC signature over the raw body before trusting anything in it.
2. Record the payment under its gateway ``payment_id`` (the ``_id``). A
   second delivery hits the unique key and is recognised as a duplicate.
3. Move the order out of ``pending`` with a conditional update. Only one
   delivery can win; the loser re-reads and reports the same outcome.
4. Store the outcome on the payment, so later duplicates return it directly.

If the process dies between 2 and 4, the payment is stored without an
outcome; the gateway's redelivery finishes the job. That is why no
multi-document transaction is needed.

A payment that does not match its order (wrong amount, unknown order, order
already paid) is still recorded and acknowledged with 200: the money has
moved, retrying will not change that, and it needs a person to look at it.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from app.config import settings
from app.models import PaymentWebhook, PaymentWebhookResult
from app.repositories import orders as orders_repo
from app.repositories import payments as payments_repo
from app.services.orders import PAID, PAYMENT_REVIEW, PENDING

logger = logging.getLogger(__name__)

PAYMENT_SUCCEEDED = "payment.succeeded"

OUTCOME_PAID = "paid"
OUTCOME_AMOUNT_MISMATCH = "amount_mismatch"
OUTCOME_ORDER_NOT_FOUND = "order_not_found"
OUTCOME_ORDER_NOT_PAYABLE = "order_not_payable"
OUTCOME_IGNORED = "ignored"


class InvalidSignature(Exception):
    pass


class InvalidPayload(Exception):
    pass


class PaymentIdConflict(Exception):
    """Same payment id seen before with a different order or amount."""


def verify_signature(body: bytes, signature: str | None, secret: str | None = None) -> bool:
    if not signature:
        return False
    key = (secret or settings.gateway_webhook_secret).encode()
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.strip().lower().encode())


async def handle_payment_webhook(
    body: bytes, signature: str | None, now: datetime | None = None
) -> PaymentWebhookResult:
    if not verify_signature(body, signature):
        raise InvalidSignature()
    try:
        event = PaymentWebhook.model_validate_json(body)
    except ValidationError as exc:
        raise InvalidPayload(str(exc)) from exc

    if event.event != PAYMENT_SUCCEEDED:
        # Acknowledge so the gateway stops retrying; nothing to do for now.
        return _result(event, OUTCOME_IGNORED)

    now = now or datetime.now(timezone.utc)
    stored, created = await payments_repo.insert_if_new(
        {
            "_id": event.payment_id,
            "order_id": event.order_id,
            "amount_paise": event.amount_paise,
            "event": event.event,
            "received_at": now,
            "outcome": None,
            "settled_at": None,
        }
    )
    if not created:
        if stored["order_id"] != event.order_id or stored["amount_paise"] != event.amount_paise:
            logger.error("payment %s redelivered with different details", event.payment_id)
            raise PaymentIdConflict(event.payment_id)
        if stored.get("outcome"):
            return _result(event, stored["outcome"], duplicate=True)
        # Recorded by an earlier delivery that has not finished (crashed, or
        # still running). Settling is idempotent, so finish it here.

    outcome = await _settle(event, now)
    await payments_repo.set_outcome(event.payment_id, outcome, now)
    if outcome != OUTCOME_PAID:
        logger.warning(
            "payment %s for order %s needs review: %s", event.payment_id, event.order_id, outcome
        )
    return _result(event, outcome, duplicate=not created)


async def _settle(event: PaymentWebhook, now: datetime) -> str:
    for _ in range(3):
        order = await orders_repo.get(event.order_id)
        if order is None:
            return OUTCOME_ORDER_NOT_FOUND
        if order.get("payment_id") == event.payment_id:
            # This payment already moved the order (an earlier or concurrent delivery).
            return OUTCOME_PAID if order["status"] == PAID else OUTCOME_AMOUNT_MISMATCH
        if order["status"] != PENDING:
            # Already paid by a different payment, or under review: a double charge.
            return OUTCOME_ORDER_NOT_PAYABLE

        if event.amount_paise == order["total_paise"]:
            fields = {"status": PAID, "payment_id": event.payment_id, "paid_at": now}
            outcome = OUTCOME_PAID
        else:
            # Charged a different amount from the order total (e.g. a discount
            # was applied after the payment was created). Never mark paid.
            fields = {"status": PAYMENT_REVIEW, "payment_id": event.payment_id}
            outcome = OUTCOME_AMOUNT_MISMATCH

        if await orders_repo.update_if_pending(event.order_id, order["total_paise"], fields):
            return outcome

    raise RuntimeError(f"order {event.order_id} kept changing while settling {event.payment_id}")


def _result(event: PaymentWebhook, outcome: str, duplicate: bool = False) -> PaymentWebhookResult:
    return PaymentWebhookResult(
        payment_id=event.payment_id, order_id=event.order_id, outcome=outcome, duplicate=duplicate
    )
