// "Take a test payment" from the dashboard: the hosted version of the reference
// test console. Creates an order and hands back the provider's payment page.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as api from "../../api/endpoints";
import type { Order } from "../../api/types";
import { useEnv } from "../../lib/env";
import { taka } from "../../lib/format";
import { FormError, Modal, TextField } from "../../ui";

export default function TestPaymentButton({ className = "primary" }: { className?: string }) {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [gateway, setGateway] = useState("");
  const [amount, setAmount] = useState("10.00");
  const [phone, setPhone] = useState("");
  const [started, setStarted] = useState<{ order: Order; redirect_url: string } | null>(null);
  const options = useQuery({ queryKey: ctx.key("checkout-options"), queryFn: () => api.checkout.options(ctx.env), enabled: open });
  const start = useMutation({
    mutationFn: () => api.orders.start(ctx.env, {
      gateway: gateway || options.data?.data[0]?.id || "", amount, customer: phone ? { phone } : {},
    }),
    onSuccess: (result) => {
      setStarted(result);
      queryClient.invalidateQueries({ queryKey: ctx.key("orders") });
    },
  });
  const live = ctx.mode === "live";
  if (!ctx.canWrite) return null;
  const close = () => { setOpen(false); setStarted(null); start.reset(); };
  const none = options.data && options.data.data.length === 0;

  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>{live ? "New payment" : "Take a test payment"}</button>
      <Modal open={open} onClose={close} title={live ? "New live payment" : "Take a test payment"} footer={started ? (
        <>
          <Link className="btn quiet" to={ctx.path(`payments/${started.order.order_id}`)} onClick={close}>View the order</Link>
          <a className="btn primary" href={started.redirect_url} target="_blank" rel="noopener noreferrer">Open the payment page</a>
        </>
      ) : (
        <>
          <button type="button" className="quiet" onClick={close}>Cancel</button>
          <button type="submit" form="test-payment" className={live ? "danger primary" : "primary"} disabled={start.isPending || none}>
            {start.isPending ? "Starting…" : "Create order and pay"}
          </button>
        </>
      )}>
        {started ? (
          <div className="stack tight">
            <p>
              Order <code>{started.order.order_id}</code> for <b>{taka(started.order.amount)}</b> is waiting at the provider.
            </p>
            <p className="info">
              Open the payment page and finish (or abandon) the payment there. You come back to this service through a
              signed link, and the result is checked with the provider before the order changes.
            </p>
          </div>
        ) : (
          <form id="test-payment" onSubmit={(e) => { e.preventDefault(); start.mutate(); }}>
            {live && <p className="banner bad">This is live mode: the payment will move real money.</p>}
            {none ? (
              <p className="info warn">No gateway is switched on. <Link to={ctx.path("gateways")} onClick={close}>Connect one first.</Link></p>
            ) : (
              <>
                <label className="f" htmlFor="tp-gateway">Gateway</label>
                <select id="tp-gateway" value={gateway || options.data?.data[0]?.id || ""} onChange={(e) => setGateway(e.target.value)}>
                  {(options.data?.data ?? []).map((o) => <option key={o.id} value={o.id}>{o.label} ({o.id})</option>)}
                </select>
                <TextField label="Amount (BDT)" value={amount} onChange={setAmount} inputMode="decimal" required
                  error={start.error instanceof ApiError && /amount/i.test(start.error.message) ? start.error.message : undefined} />
                <TextField label="Customer phone" help="Optional. Some gateways show it on their page." value={phone} onChange={setPhone}
                  inputMode="tel" placeholder="01XXXXXXXXX" />
              </>
            )}
            <FormError error={start.error} />
          </form>
        )}
      </Modal>
    </>
  );
}
