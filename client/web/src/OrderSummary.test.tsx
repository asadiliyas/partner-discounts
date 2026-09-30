import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OrderSummary } from "./OrderSummary";
import type { Order } from "./types";

const order: Order = {
  order_id: "ord_test_1",
  subtotal_paise: 19999,
  total_paise: 19999,
  status: "pending",
  discount: null,
};

const discounted: Order = {
  order_id: "ord_test_2",
  subtotal_paise: 199999,
  total_paise: 169999,
  status: "pending",
  discount: { code: "DEMO15", amount_paise: 30000 },
};

/** A promise the test settles by hand, to hold onPay "in progress". */
function deferred() {
  let resolve!: () => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const payButton = () => screen.getByRole("button");

describe("OrderSummary", () => {
  it("renders", () => {
    render(<OrderSummary order={order} onPay={async () => {}} />);
    expect(screen.getByRole("region", { name: "Order summary" })).toBeInTheDocument();
  });

  it("shows subtotal and total, and no discount row without a discount", () => {
    render(<OrderSummary order={order} onPay={async () => {}} />);
    expect(screen.getByText("Subtotal").nextElementSibling).toHaveTextContent("₹199.99");
    expect(screen.getByText("Total").nextElementSibling).toHaveTextContent("₹199.99");
    expect(screen.queryByText(/discount/i)).not.toBeInTheDocument();
  });

  it("shows the discount code and amount when there is one", () => {
    render(<OrderSummary order={discounted} onPay={async () => {}} />);
    expect(screen.getByText("Subtotal").nextElementSibling).toHaveTextContent("₹1,999.99");
    expect(screen.getByText("DEMO15")).toBeInTheDocument();
    expect(screen.getByText("₹300.00")).toBeInTheDocument();
    expect(screen.getByText("Total").nextElementSibling).toHaveTextContent("₹1,699.99");
  });

  it("disables Pay while the payment is in progress, then re-enables it", async () => {
    const user = userEvent.setup();
    const payment = deferred();
    const onPay = vi.fn(() => payment.promise);
    render(<OrderSummary order={order} onPay={onPay} />);

    expect(payButton()).toHaveTextContent("Pay");
    expect(payButton()).toBeEnabled();

    await user.click(payButton());
    expect(payButton()).toBeDisabled();
    expect(payButton()).toHaveTextContent(/^Pay$/);

    await user.click(payButton());
    await user.dblClick(payButton());
    expect(onPay).toHaveBeenCalledTimes(1);

    payment.resolve();
    await waitFor(() => expect(payButton()).toBeEnabled());
    expect(payButton()).toHaveTextContent(/^Pay$/);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reads Paid and stays disabled only when the order is paid", async () => {
    const user = userEvent.setup();
    const onPay = vi.fn(async () => {});
    render(<OrderSummary order={{ ...order, status: "paid" }} onPay={onPay} />);

    expect(payButton()).toHaveTextContent("Paid");
    expect(payButton()).toBeDisabled();
    await user.click(payButton());
    expect(onPay).not.toHaveBeenCalled();
  });

  it("switches to Paid when the parent passes the paid order back", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<OrderSummary order={order} onPay={async () => {}} />);
    await user.click(payButton());
    await waitFor(() => expect(payButton()).toBeEnabled());
    expect(payButton()).toHaveTextContent(/^Pay$/);

    rerender(<OrderSummary order={{ ...order, status: "paid" }} onPay={async () => {}} />);
    expect(payButton()).toHaveTextContent("Paid");
    expect(payButton()).toBeDisabled();
  });

  it("shows an alert when the payment fails and lets the customer retry", async () => {
    const user = userEvent.setup();
    const first = deferred();
    const second = deferred();
    const onPay = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    render(<OrderSummary order={order} onPay={onPay} />);

    await user.click(payButton());
    first.reject(new Error("card declined"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/payment failed/i);
    expect(payButton()).toBeEnabled();
    expect(payButton()).toHaveTextContent(/^Pay$/);

    await user.click(payButton());
    expect(onPay).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(payButton()).toBeDisabled();

    second.resolve();
    await waitFor(() => expect(payButton()).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
