import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../api/endpoints";
import type { OrderStatus } from "../../api/types";
import { CATALOG, gatewayName } from "../../lib/catalog";
import { useEnv } from "../../lib/env";
import { STATUS_LABEL, taka } from "../../lib/format";
import { Empty, ErrorNote, Loading, PageHead, StatusPill, Time } from "../../ui";
import TestPaymentButton from "./TestPayment";

const STATUSES = Object.keys(STATUS_LABEL) as OrderStatus[];

export default function Payments() {
  const ctx = useEnv();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const gateway = params.get("gateway") ?? "";
  const q = params.get("q") ?? "";
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const query = useInfiniteQuery({
    queryKey: ctx.key("orders", status, gateway, q),
    queryFn: ({ pageParam }) => api.orders.list(ctx.env, { status, gateway, q, cursor: pageParam, limit: 50 }),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: 30_000,
  });
  const orders = query.data?.pages.flatMap((p) => p.data) ?? [];
  const filtered = Boolean(status || gateway || q);

  return (
    <div className="stack">
      <PageHead title="Payments" actions={<TestPaymentButton />}>
        Every order, newest first. An order is only ever marked paid after the provider confirmed the exact amount.
      </PageHead>

      <form className="filters" role="search" onSubmit={(e) => { e.preventDefault(); set("q", search.trim()); }}>
        <label>
          <span className="sr-only">Status</span>
          <select value={status} onChange={(e) => set("status", e.target.value)} style={{ width: "auto" }}>
            <option value="">Any status</option>
            {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </select>
        </label>
        <label>
          <span className="sr-only">Gateway</span>
          <select value={gateway} onChange={(e) => set("gateway", e.target.value)} style={{ width: "auto" }}>
            <option value="">Any gateway</option>
            {CATALOG.gateways.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        </label>
        <label className="grow" style={{ minWidth: 200 }}>
          <span className="sr-only">Search</span>
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Order id, transaction id, phone or email" />
        </label>
        <button type="submit" className="quiet">Search</button>
        {filtered && <button type="button" className="bare" onClick={() => setParams({}, { replace: true })}>Clear</button>}
      </form>

      {query.isPending ? <Loading label="Loading payments" /> : query.error ? <ErrorNote error={query.error} retry={() => query.refetch()} />
        : orders.length === 0 ? (
          <div className="card">
            <Empty title={filtered ? "Nothing matches" : "No payments yet"}>
              <p>{filtered ? "Try a different status, gateway or search." : ctx.mode === "sandbox"
                ? "Take a test payment to see the whole flow: order, provider page, signed return, verification."
                : "Orders appear here as soon as your server creates them with a live API key."}</p>
            </Empty>
          </div>
        ) : (
          <div className="card flush">
            <div className="scroll">
              <table className="cards">
                <thead><tr><th>Order</th><th className="right">Amount</th><th>Gateway</th><th>Status</th><th>Customer</th><th>Created</th></tr></thead>
                <tbody>
                  {orders.map((o) => (
                    <tr key={o.order_id} className="clickable" tabIndex={0} onClick={() => navigate(ctx.path(`payments/${encodeURIComponent(o.order_id)}`))}
                      onKeyDown={(e) => { if (e.key === "Enter") navigate(ctx.path(`payments/${encodeURIComponent(o.order_id)}`)); }}>
                      <td data-l="Order"><code>{o.order_id}</code></td>
                      <td data-l="Amount" className="right num"><b>{taka(o.amount)}</b>{o.refunded_poisha > 0 && <span className="mute small"> −{taka(o.refunded)}</span>}</td>
                      <td data-l="Gateway">{gatewayName(o.gateway)}</td>
                      <td data-l="Status"><StatusPill status={o.status} /></td>
                      <td data-l="Customer" className="small">{o.customer.name || o.customer.phone || o.customer.email || <span className="mute">—</span>}</td>
                      <td data-l="Created" className="small nowrap"><Time value={o.created_at} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      {query.hasNextPage && (
        <div className="row" style={{ justifyContent: "center" }}>
          <button type="button" onClick={() => query.fetchNextPage()} disabled={query.isFetchingNextPage}>
            {query.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}
