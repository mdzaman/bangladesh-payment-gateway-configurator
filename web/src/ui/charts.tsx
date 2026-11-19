// The three figures on the overview page, drawn as plain SVG and HTML.
//
// Form follows the data's job: one series over time is a column chart in a
// single hue (no legend: the title names it); one whole split into parts is a
// thin segmented bar above the list that carries the numbers; a ratio per
// gateway is a meter list. Every value is also reachable without hovering:
// each chart has a table view. Colours are the validated --viz-* tokens.
import { useId, useMemo, useState, type KeyboardEvent } from "react";
import type { DailyStat, Stats } from "../api/types";
import { gatewayName } from "../lib/catalog";
import { percent, plural, taka, takaShort } from "../lib/format";

const W = 720;
const H = 250;
const M = { top: 14, right: 10, bottom: 28, left: 56 };

/** Clean axis steps (1, 2, 2.5 or 5 times a power of ten) covering 0..max. */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / count;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? 10 * pow;
  const ticks: number[] = [];
  for (let v = 0; v < max + step - 1e-9; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

const shortDay = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
const longDay = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const asDate = (day: string) => new Date(`${day}T00:00:00Z`);

export function PaidPerDay({ data, stale }: { data: DailyStat[]; stale?: boolean }) {
  const [active, setActive] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const titleId = useId();
  const geometry = useMemo(() => {
    const max = Math.max(0, ...data.map((d) => d.paid_poisha));
    const ticks = niceTicks(max / 100).map((t) => t * 100);
    const top = ticks[ticks.length - 1] || 1;
    const band = (W - M.left - M.right) / Math.max(1, data.length);
    // Thin marks: capped at 24px, and never touching (2px of surface between neighbours).
    const width = Math.min(24, Math.max(2, band - 2));
    const base = H - M.bottom;
    const y = (v: number) => M.top + (base - M.top) * (1 - v / top);
    const every = Math.max(1, Math.ceil(data.length / 7));
    return { max, ticks, band, width, base, y, every };
  }, [data]);
  const { max, ticks, band, width, base, y, every } = geometry;

  const onKey = (e: KeyboardEvent) => {
    if (!data.length) return;
    const last = data.length - 1;
    const at = active ?? last;
    const next = e.key === "ArrowLeft" ? Math.max(0, at - 1) : e.key === "ArrowRight" ? Math.min(last, at + 1)
      : e.key === "Home" ? 0 : e.key === "End" ? last : e.key === "Escape" ? null : undefined;
    if (next === undefined) return;
    e.preventDefault();
    setActive(next);
  };
  const tip = active !== null ? data[active] : undefined;

  return (
    <figure className="viz">
      <figcaption>
        <h3 id={titleId}>Paid per day</h3>
        <button type="button" className="small quiet" onClick={() => setTable((v) => !v)} aria-pressed={table}>
          {table ? "Show chart" : "Show table"}
        </button>
      </figcaption>
      {table ? (
        <div className="scroll" style={{ maxHeight: 320 }}>
          <table aria-labelledby={titleId}>
            <thead><tr><th>Day (Dhaka)</th><th className="right">Paid</th><th className="right">Payments</th><th className="right">Not paid</th></tr></thead>
            <tbody>
              {[...data].reverse().map((d) => (
                <tr key={d.day}>
                  <td>{longDay.format(asDate(d.day))}</td><td className="right num">{taka(d.paid_amount)}</td>
                  <td className="right num">{d.paid}</td><td className="right num">{d.unpaid}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : max === 0 ? (
        <p className="empty">No paid payments in this period yet.</p>
      ) : (
        <div className={`viz-wrap${stale ? " stale" : ""}`} tabIndex={0} role="group" aria-labelledby={titleId}
          aria-description="Use the left and right arrow keys to read each day. A table view is available."
          onKeyDown={onKey} onBlur={() => setActive(null)} onPointerLeave={() => setActive(null)}>
          <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={titleId}>
            {ticks.map((t) => (
              <g key={t}>
                <line className={t === 0 ? "axis" : "grid"} x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
                <text x={M.left - 8} y={y(t)} textAnchor="end" dominantBaseline="middle">{takaShort(t)}</text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = M.left + band * (i + 0.5);
              const x = cx - width / 2;
              const top = y(d.paid_poisha);
              const height = base - top;
              const r = Math.min(4, width / 2, height);
              return (
                <g key={d.day}>
                  {height > 0 && (
                    // Rounded at the data end, square on the baseline.
                    <path className={`col${active === i ? " on" : ""}`}
                      d={`M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + width - r}Q${x + width},${top} ${x + width},${top + r}V${base}Z`} />
                  )}
                  {(i % every === 0 || i === data.length - 1) && (data.length - 1 - i >= every / 2 || i === data.length - 1) && (
                    <text x={cx} y={H - 8} textAnchor="middle">{shortDay.format(asDate(d.day))}</text>
                  )}
                  {/* The whole column of the day is the hover target, not just the painted bar. */}
                  <rect className="hit" x={M.left + band * i} y={M.top} width={band} height={base - M.top}
                    onPointerEnter={() => setActive(i)} onPointerMove={() => setActive(i)} />
                </g>
              );
            })}
          </svg>
          {tip && active !== null && (
            <div className="tip" role="status" style={{
              left: `${((M.left + band * (active + 0.5)) / W) * 100}%`,
              top: `calc(${(Math.min(y(tip.paid_poisha), base - 30) / H) * 100}% - 8px)`,
            }}>
              <b>{taka(tip.paid_amount)}</b>
              {longDay.format(asDate(tip.day))} · {plural(tip.paid, "payment")}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

/** Finished payments split three ways. Grey sits between green and red on purpose. */
export function OutcomeSplit({ stats }: { stats: Stats }) {
  const s = stats.by_status;
  const parts = [
    { name: "Paid", n: s.paid + s.partially_refunded + s.refunded, color: "var(--viz-good)" },
    { name: "Cancelled or expired", n: s.cancelled + s.expired, color: "var(--viz-neutral)" },
    { name: "Failed", n: s.failed, color: "var(--viz-critical)" },
  ];
  const total = parts.reduce((n, p) => n + p.n, 0);
  return (
    <figure className="viz">
      <figcaption><h3>How payments ended</h3><span className="mute small">{plural(total, "finished payment")}</span></figcaption>
      {total === 0 ? <p className="empty">Nothing has finished in this period yet.</p> : (
        <>
          <div className="split" role="img"
            aria-label={parts.map((p) => `${p.name}: ${p.n} (${percent(p.n / total)})`).join(", ")}>
            {parts.filter((p) => p.n > 0).map((p) => <span key={p.name} style={{ flex: p.n, background: p.color }} title={`${p.name}: ${p.n}`} />)}
          </div>
          <table style={{ marginTop: 10 }}>
            <tbody>
              {parts.map((p) => (
                <tr key={p.name}>
                  <td><span className="swatch" style={{ background: p.color }} aria-hidden="true" />{p.name}</td>
                  <td className="right num">{p.n.toLocaleString()}</td>
                  <td className="right num mute">{percent(p.n / total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </figure>
  );
}

/** Success rate per gateway: a name, a bar and a figure per row, bars aligned. */
export function GatewayMeters({ stats }: { stats: Stats }) {
  const rows = stats.by_gateway;
  return (
    <figure className="viz">
      <figcaption><h3>By gateway</h3><span className="mute small">share of finished payments that were paid</span></figcaption>
      {rows.length === 0 ? <p className="empty">No payments in this period yet.</p> : (
        <div className="meters">
          {rows.map((g) => (
            <div key={g.gateway} style={{ display: "contents" }}>
              <span><b>{gatewayName(g.gateway)}</b></span>
              <span className="track" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((g.success_rate ?? 0) * 100)}
                aria-label={`${gatewayName(g.gateway)} success rate`}>
                <span style={{ width: `${(g.success_rate ?? 0) * 100}%` }} />
              </span>
              <span className="fig"><b>{percent(g.success_rate)}</b> <span className="mute">· {taka(g.paid_amount)} from {g.paid} paid</span></span>
            </div>
          ))}
        </div>
      )}
    </figure>
  );
}
