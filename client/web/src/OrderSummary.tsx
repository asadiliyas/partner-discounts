import { useRef, useState } from "react";
import { formatPaise } from "./formatPaise";
import type { Order } from "./types";

/**
 * Shows the subtotal, the discount (code and amount) when there is one, and
 * the total, all through formatPaise, plus a "Pay" button that calls onPay.
 *
 * - The button is disabled while onPay is pending (no double submit); it
 *   still reads "Pay".
 * - Only when order.status is "paid" does the button read "Paid"; it is then
 *   disabled.
 * - If onPay rejects, show an error in an element with role="alert" and
 *   re-enable the button.
 */
export function OrderSummary(props: { order: Order; onPay: () => Promise<void> }): JSX.Element {
  const { order, onPay } = props;
  const [paying, setPaying] = useState(false);
  const [failed, setFailed] = useState(false);
  // State updates are async; the ref blocks a second click in the same tick.
  const inFlight = useRef(false);
  const isPaid = order.status === "paid";

  async function handlePay() {
    if (inFlight.current || isPaid) return;
    inFlight.current = true;
    setPaying(true);
    setFailed(false);
    try {
      await onPay();
    } catch {
      setFailed(true);
    } finally {
      inFlight.current = false;
      setPaying(false);
    }
  }

  return (
    <section aria-label="Order summary">
      <h2>Order {order.order_id}</h2>
      <dl>
        <dt>Subtotal</dt>
        <dd>{formatPaise(order.subtotal_paise)}</dd>
        {order.discount && (
          <>
            <dt>
              Discount (<span>{order.discount.code}</span>)
            </dt>
            <dd>
              −<span>{formatPaise(order.discount.amount_paise)}</span>
            </dd>
          </>
        )}
        <dt>Total</dt>
        <dd>{formatPaise(order.total_paise)}</dd>
      </dl>

      {failed && !isPaid && <p role="alert">Payment failed. Please try again.</p>}

      <button type="button" onClick={handlePay} disabled={paying || isPaid}>
        {isPaid ? "Paid" : "Pay"}
      </button>
    </section>
  );
}
