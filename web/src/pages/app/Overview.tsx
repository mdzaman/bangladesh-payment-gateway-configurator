import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../../api/endpoints";
import { gatewayName } from "../../lib/catalog";
import { useEnv } from "../../lib/env";
import { percent, plural, taka } from "../../lib/format";
import { ErrorNote, GatewayStatusPill, Loading, PageHead, Time } from "../../ui";
import { GatewayMeters, OutcomeSplit, PaidPerDay } from "../../ui/charts";
import { Checklist } from "./Gateways";
import TestPaymentButton from "./TestPayment";

const RANGES = [7, 30, 90];

export default function Overview() {
  const ctx = useEnv();
  const [days, setDays] = useState(30);
  const query = useQuery({
    queryKey: ctx.key("overview", days),
    queryFn: () => api.overview(ctx.env, days),
    placeholderData: keepPreviousData, // a new range keeps the old frame until it arrives
    refetchInterval: 60_000,
  });
  if (query.isPending) return <Loading label="Loading overview" />;
  if (query.error) return <ErrorNote error={query.error} retry={() => query.refetch()} />;
  const { stats, gateways } = query.data;
  const stale = query.isPlaceholderData;
  const finished = stats.paid_count + stats.by_status.failed + stats.by_status.cancelled + stats.by_status.expired;
  const connected = gateways.filter((g) => g.enabled);

  return (
    <div className="stack">
      <PageHead title="Overview" actions={<TestPaymentButton />}>
        {ctx.workspace.name} in {ctx.mode} mode.
      </PageHead>

      {/* One row of filters above everything it scopes. */}
      <div className="filters">
        <div className="seg" role="group" aria-label="Period">
          {RANGES.map((d) => (
            <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>Last {d} days</button>
          ))}
        </div>
      </div>

      <div className={`tiles${stale ? " stale" : ""}`}>
        <div className="tile lead">
          <div className="label">Paid in the last {stats.days} days</div>
          <div className="value">{taka(stats.paid_amount)}</div>
          <div className="sub">
            {plural(stats.paid_count, "payment")}
            {Number(stats.refunded_amount) > 0 ? ` · ${taka(stats.refunded_amount)} refunded` : ""}
          </div>
        </div>
        <div className="tile">
          <div className="label">Success rate</div>
          <div className="value">{percent(stats.success_rate)}</div>
          <div className="sub">{finished ? `of ${plural(finished, "finished payment")}` : "no finished payments yet"}</div>
        </div>
        <div className="tile">
          <div className="label">Awaiting a result now</div>
          <div className="value">{stats.pending_now.toLocaleString()}</div>
          <div className="sub">re-checked with the provider automatically</div>
        </div>
        <div className={`tile${stats.needs_review_now ? " attention" : ""}`}>
          <div className="label">Need review now</div>
          <div className="value">{stats.needs_review_now.toLocaleString()}</div>
          <div className="sub">
            {stats.needs_review_now
              ? <Link to={ctx.path("payments?status=needs_review")}>Check these before shipping</Link>
              : "amount and order always matched"}
          </div>
        </div>
      </div>

      <section className="card"><PaidPerDay data={stats.daily} stale={stale} /></section>

      <div className="three">
        <section className={`card${stale ? " stale" : ""}`}><OutcomeSplit stats={stats} /></section>
        <section className={`card${stale ? " stale" : ""}`}><GatewayMeters stats={stats} /></section>
      </div>

      <section className="card">
        <div className="card-head">
          <h3>Connected gateways</h3>
          <Link className="btn small" to={ctx.path("gateways")}>Configure</Link>
        </div>
        {connected.length === 0 ? (
          <p className="info warn">No gateway is switched on, so no payment can start. <Link to={ctx.path("gateways")}>Connect one.</Link></p>
        ) : (
          <div className="scroll">
            <table>
              <thead><tr><th>Gateway</th><th>State</th><th>Last successful call</th><th>Last error</th></tr></thead>
              <tbody>
                {gateways.map((g) => (
                  <tr key={g.id}>
                    <td><b>{gatewayName(g.id)}</b></td>
                    <td><GatewayStatusPill status={g.status} enabled={g.enabled} /></td>
                    <td><Time value={g.last_success_at} /></td>
                    <td className="small">{g.last_error ? <code>{g.last_error}</code> : <span className="mute">none</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Checklist compact />
    </div>
  );
}
