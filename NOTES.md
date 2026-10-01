# NOTES

## Client

- **React** (`client/web/`).

## How it was checked

- Backend: `make test` → 59 passed (in-memory Mongo). `make test-mongo` → the same 59 pass against the compose MongoDB 7 replica set.
- Client: `npm ci && npm test` → 28 passed. `npm run typecheck` and `npm run build` are clean.
- Mutation checks: each guard was removed by hand and the suite rerun, and each removal fails at least one test:
  - Backend: the `discount: None` condition on apply, the `status: pending` condition on the webhook update, the signature check, and flooring (swapped for `round`).
  - Client: disabled-while-paying, the error on rejection, and "Paid" only when paid.
  - One removal fails nothing: skipping the stored outcome on a duplicate webhook. Settling is idempotent, so it gives the same result either way.
- Smoke test against `docker compose up` over HTTP:
  - KIDS18 applies to `ord_c_3005` (₹70.19 off), then FIRST5 is refused with 409.
  - The gateway's double delivery marks the order paid once. The second call returns `duplicate: true`.
  - A bad signature gets 401.
- No MongoDB transactions are used (see below), so the in-memory Mongo is enough for `make test`.

## Decisions

- **Rounding.**
  - The discount is `subtotal × bps // 10000` (floored), then capped at `cap_paise`. The customer never gets more than the advertised percentage.
  - The partner's share is floored too, and RAYY takes the remainder. Partner + RAYY always equals the discount exactly.
  - All maths is integer-only, in `app/services/pricing.py`.
- **Second code on an order.**
  - One code per order. A different second code gets `409` and the first stays.
  - Re-sending the same code returns `200` with the order unchanged, so client retries are safe.
  - Two codes sent at the same moment: exactly one wins, because the write is one conditional update (`status: pending, discount: null`).
  - Codes don't stack, and the better one doesn't replace the first. Swapping codes would need an explicit "remove discount" action.
- **apply-discount responses:**
  - unknown order: `404`
  - unknown code or expired code: `422`
  - order already has a different code, or isn't `pending`: `409`
  - Codes are matched case-insensitively, and a code expires at the instant `expires_at` is reached.
- **What gets recorded on apply.** A frozen snapshot goes on the order: the code, `percent_off_bps`, `cap_paise`, `amount_paise`, the split ratios, `partner_funded_paise` / `rayy_funded_paise`, and `applied_at`. `total_paise` = subtotal − discount.
- **Payment webhook.**
  - The HMAC is checked over the raw bytes with `compare_digest` before anything is parsed. A missing or bad signature gets `401`.
  - A non-integer amount (even `459.0`) or malformed JSON gets `400`.
  - The payment is stored in `payments` with `_id = payment_id`. The unique key is what makes duplicate deliveries harmless, and duplicates return the stored outcome with `duplicate: true`.
  - The order moves `pending → paid` with a conditional update, so only one concurrent delivery can make that change.
  - Non-happy cases are recorded and answered with `200`, since retrying won't fix them and a person needs to look:
    - amount ≠ order total: order goes to `payment_review` and is **not** marked paid (this is fixture `webhook_2`: 14999 charged after KIDS18 made the total 12300)
    - order already paid by another payment: `order_not_payable` (a double charge)
    - unknown order: `order_not_found`
  - The same `payment_id` arriving with a different amount or order gets `409`.
  - Events other than `payment.succeeded` are answered with `200` and ignored.
- **No transactions.** Every state change is a single-document conditional update.
  - If the process dies after the payment is inserted but before the order is updated, the payment is left without an outcome. The gateway's redelivery then finishes the job (there's a test for this).
- **Structure.** Routes only map exceptions to HTTP codes. Money maths is in `services/pricing.py`, the rules are in `services/orders.py` and `services/payments.py`, and queries are in `repositories/`.

## Monthly partner settlement

1. Store per order, when the discount is applied: `partner_id`, the code, the split ratio used (`partner_share_bps`), and the split already worked out in paise (`partner_funded_paise`, `rayy_funded_paise`). This is the frozen snapshot above.
2. Store per order, on payment: `status: paid`, `paid_at` (UTC) and `payment_id`. A partner owes its share only for orders that were actually paid.
3. The settlement query for month M reads orders with `status = "paid"`, `discount != null` and `paid_at` in `[start of M, start of M+1)` in IST. It groups by `partner_id` and sums `discount.partner_funded_paise`.
4. It never reads the live code config or the partner's current ratio. A new ratio next month only affects discounts applied after the change, and there is no re-rounding at settlement time.
5. Needs an index on `{status: 1, paid_at: 1}`. Refunds and chargebacks should become separate adjustment entries, not edits to past orders (not built here).

## What the AI got wrong (and how it was caught)

- The first "codes sent at the same time" and "concurrent webhook deliveries" tests used `asyncio.gather` and passed.
- A mutation check showed they still passed with the concurrency guard **deleted**: with the in-memory Mongo, the first request finished before the second read the order, so there was never a race.
- Fixed with `tests/race.py`. It holds both requests at a barrier just before the write, so both read the same state first. Removing either guard now fails a test.
- A smaller one: a hand-worked expected value for `formatPaise(Number.MAX_SAFE_INTEGER)` grouped the paise digits instead of the rupee digits. The test run caught it; the function was right.

## Not yet trusted in production

- **Price not locked during payment.** There is no price lock when a payment is started. A discount applied mid-payment is caught after the fact (amount mismatch → `payment_review`), not prevented. A real flow would move the order to `awaiting_payment` when the gateway payment is created, and refuse discounts after that.
- **`payment_review` goes nowhere yet.** Nothing picks up `payment_review` / `order_not_payable` except a log warning: no alert, no ops queue, no refund.
- **Replayed webhooks.** The signature has no timestamp, so a replayed webhook can't be told apart from a gateway redelivery. Idempotency makes a replay harmless, but there's no replay window and no secret rotation.
- **No customer rules.** Nothing on apply-discount checks who is calling. There are no per-customer or usage limits (`FIRST5` presumably means first order only), and codes aren't tied to a partner.
- **Code expiry comes from the template.** Codes load from JSON with expiry relative to process start, and are cached, so expiry moves on every restart. They need to live in a store with absolute dates.
- **Race tests are stubbed.** Real concurrency is only covered by the barrier-forced tests and one full-suite run against a single-node replica set, not by load against a real cluster.

