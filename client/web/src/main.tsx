import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { OrderSummary } from "./OrderSummary";
import type { Order } from "./types";

const sample: Order = {
  order_id: "ord_demo_1",
  subtotal_paise: 199999,
  total_paise: 169999,
  status: "pending",
  discount: { code: "DEMO15", amount_paise: 30000 },
};

/** Local demo: the first attempt fails so the retry path is visible, the second succeeds. */
function Demo() {
  const [order, setOrder] = useState(sample);
  const attempts = useRef(0);

  async function pay() {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    attempts.current += 1;
    if (attempts.current === 1) throw new Error("card declined");
    setOrder((current) => ({ ...current, status: "paid" }));
  }

  return <OrderSummary order={order} onPay={pay} />;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Demo />
  </React.StrictMode>,
);
