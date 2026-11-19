import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import * as api from "../../api/endpoints";
import type { OrderDetail, OrderEvent } from "../../api/types";
import { gatewayName } from "../../lib/catalog";
import { useEnv } from "../../lib/env";
import { STATUS_HELP, STATUS_LABEL, taka, when } from "../../lib/format";
import { ErrorNote, FormError, Loading, Modal, PageHead, StatusPill, TextField, useToast } from "../../ui";

const EVENT_TITLE: Record<string, string> = {
  created: "Order created",
  transition: "Status changed",
  ignored_transition: "Ignored (orders only move forward)",
  still_pending: "Checked with the provider: still pending",
  refund: "Refund requested",
  refund_unconfirmed: "Refund outcome unknown",
};

function eventTone(e: OrderEvent): string {
  if (e.kind === "refund_unconfirmed" || e.to_status === "needs_review") return "warn";
  if (e.kind !== "transition") return "";
  if (e.to_status === "paid") return "good";
  if (e.to_status === "failed") return "bad";
  return "";
}

function eventText(e: OrderEvent): string {
  const d = e.detail as Record<string, unknown>;
  const bits: string[] = [];
  if (e.from_status && e.to_status && e.kind !== "created") {
    bits.push(`${STATUS_LABEL[e.from_status as keyof typeof STATUS_LABEL] ?? e.from_status} → ${STATUS_LABEL[e.to_status as keyof typeof STATUS_LABEL] ?? e.to_status}`);
  }
  if (typeof d.source === "string") bits.push(`from ${d.source.replace("callback:", "the customer's return (").replace(/^the customer's return \((.*)$/, "the customer's return ($1)")}`);
  if (Array.isArray(d.problems) && d.problems.length) bits.push(`mismatch: ${d.problems.join("; ")}`);
  if (typeof d.amount_poisha === "number" && e.kind === "refund") bits.push(`${taka(d.amount_poisha, true)}${d.ok ? "" : " (not accepted)"}`);
  if (typeof d.refund_ref === "string") bits.push(`reference ${d.refund_ref}`);
  if (typeof d.txn === "string") bits.push(`transaction ${d.txn}`);
  if (typeof d.reason === "string") bits.push(d.reason);
  if (typeof d.message === "string") bits.push(d.message);
  return bits.join(" · ");
}

export default function PaymentDetail() {
  const ctx = useEnv();
  const { orderId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const key = ctx.key("order", orderId);
  const query = useQuery({ queryKey: key, queryFn: () => api.orders.get(ctx.env, orderId) });
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ctx.key("orders") });
    queryClient.invalidateQueries({ queryKey: ctx.key("overview") });
  };
  const requery = useMutation({
    mutationFn: () => api.orders.requery(ctx.env, orderId),
    onSuccess: (o) => { invalidate(); toast(`Provider says: ${STATUS_LABEL[o.status].toLowerCase()}`); },
  });

  if (query.isPending) return <Loading label="Loading order" />;
  if (query.error) return <ErrorNote error={query.error} retry={() => query.refetch()} />;
  const order = query.data;
  const canRefund = ctx.canWrite && (order.status === "paid" || order.status === "partially_refunded");
  const final = order.status === "refunded" || order.status === "needs_review";

  return (
    <div className="stack">
      <p className="small"><Link to={ctx.path("payments")}>← All payments</Link></p>
      <PageHead title={`Order ${order.order_id}`} actions={
        <>
          {ctx.isDeveloper && !final && (
            <button type="button" className="quiet" disabled={requery.isPending} onClick={() => requery.mutate()}>
              {requery.isPending ? "Asking…" : "Ask the provider again"}
            </button>
          )}
          {canRefund && <RefundButton order={order} onDone={invalidate} />}
        </>
      }>
        <StatusPill status={order.status} /> <b>{taka(order.amount)}</b> through {gatewayName(order.gateway)}
      </PageHead>
      <FormError error={requery.error} />
      {STATUS_HELP[order.status] && (
        <p className={`info ${order.status === "needs_review" ? "warn" : ""}`}>{STATUS_HELP[order.status]}</p>
      )}

      <div className="three">
        <section className="card">
          <h3>Payment</h3>
          <dl className="kv" style={{ marginTop: 10 }}>
            <dt>Amount</dt><dd><b>{taka(order.amount)}</b> {order.currency}</dd>
            <dt>Refunded</dt><dd>{taka(order.refunded)}</dd>
            <dt>Gateway</dt><dd>{gatewayName(order.gateway)}</dd>
            <dt>Provider payment id</dt><dd>{order.provider_payment_id ? <code>{order.provider_payment_id}</code> : <span className="mute">—</span>}</dd>
            <dt>Provider transaction</dt><dd>{order.provider_txn_id ? <code>{order.provider_txn_id}</code> : <span className="mute">—</span>}</dd>
            <dt>Created</dt><dd>{when(order.created_at)}</dd>
            <dt>Paid</dt><dd>{order.paid_at ? when(order.paid_at) : <span className="mute">—</span>}</dd>
            <dt>Started from</dt><dd>{order.source}</dd>
            {order.checkout_session_id && <><dt>Checkout session</dt><dd><code>{order.checkout_session_id}</code></dd></>}
            {order.return_url && <><dt>Return URL</dt><dd><code>{order.return_url}</code></dd></>}
          </dl>
        </section>
        <section className="card">
          <h3>Customer and metadata</h3>
          {Object.keys(order.customer).length + Object.keys(order.metadata).length === 0
            ? <p className="mute" style={{ marginTop: 10 }}>None was sent with this order.</p>
            : (
              <dl className="kv" style={{ marginTop: 10 }}>
                {Object.entries(order.customer).map(([k, v]) => <Row key={`c-${k}`} k={k} v={v} />)}
                {Object.entries(order.metadata).map(([k, v]) => <Row key={`m-${k}`} k={k} v={v} mono />)}
              </dl>
            )}
        </section>
      </div>

      <section className="card">
        <h3>Audit trail</h3>
        <p className="lede">Every check and every change, as it happened. Secrets and phone numbers are masked.</p>
        <ol className="timeline">
          {order.events.map((e, i) => (
            <li key={i} className={eventTone(e)}>
              <b>{e.kind === "transition" && e.to_status ? STATUS_LABEL[e.to_status as keyof typeof STATUS_LABEL] ?? e.to_status : EVENT_TITLE[e.kind] ?? e.kind}</b>{" "}
              <time>{when(e.at)}</time>
              {eventText(e) && <div className="small mute">{eventText(e)}</div>}
            </li>
          ))}
        </ol>
      </section>

      {order.webhook_events.length > 0 && (
        <section className="card">
          <h3>Webhook events</h3>
          <table style={{ marginTop: 8 }}>
            <tbody>
              {order.webhook_events.map((e) => (
                <tr key={e.id}><td><code>{e.type}</code></td><td className="small mute"><code>{e.id}</code></td><td className="small right">{when(e.created_at)}</td></tr>
              ))}
            </tbody>
          </table>
          {ctx.isDeveloper && <p className="small" style={{ marginTop: 8 }}><Link to={ctx.path("developers/webhooks")}>See deliveries</Link></p>}
        </section>
      )}
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return <><dt>{k}</dt><dd>{mono ? <code>{v}</code> : v}</dd></>;
}

function RefundButton({ order, onDone }: { order: OrderDetail; onDone: () => void }) {
  const ctx = useEnv();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const left = (order.amount_poisha - order.refunded_poisha) / 100;
  const refund = useMutation({
    mutationFn: () => api.orders.refund(ctx.env, order.order_id, amount.trim() || null, reason.trim() || "customer request"),
    onSuccess: (r) => {
      onDone();
      if (r.ok) {
        toast("Refund accepted by the provider");
        setOpen(false);
      }
    },
  });
  if (order.refund_capability === "none") {
    return <button type="button" className="quiet" disabled title={`${gatewayName(order.gateway)} refunds are done in its merchant panel`}>Refund in provider panel</button>;
  }
  const fullOnly = order.refund_capability === "full";
  return (
    <>
      <button type="button" className="danger" onClick={() => setOpen(true)}>Refund</button>
      <Modal open={open} onClose={() => setOpen(false)} title={`Refund order ${order.order_id}`} footer={
        <>
          <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>
          <button type="submit" form="refund" className="danger primary" disabled={refund.isPending}>{refund.isPending ? "Refunding…" : "Refund"}</button>
        </>
      }>
        <form id="refund" onSubmit={(e) => { e.preventDefault(); refund.mutate(); }}>
          {ctx.mode === "live" && <p className="banner bad">Live mode: this sends real money back to the customer.</p>}
          <p>Up to <b>{taka(left)}</b> can be refunded through {gatewayName(order.gateway)}.</p>
          {fullOnly
            ? <p className="info">{gatewayName(order.gateway)} only supports full refunds by API.</p>
            : <TextField label="Amount (BDT)" help={`Leave empty to refund all ${taka(left)}.`} value={amount} onChange={setAmount} inputMode="decimal" />}
          <TextField label="Reason" help="Sent to the provider and kept in the audit trail." value={reason} onChange={setReason} maxLength={250} />
          {refund.data && !refund.data.ok && <p className="info bad" role="alert">The provider did not accept the refund ({refund.data.status}). Nothing changed.</p>}
          <FormError error={refund.error} />
        </form>
      </Modal>
    </>
  );
}
