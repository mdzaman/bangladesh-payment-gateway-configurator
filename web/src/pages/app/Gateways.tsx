// The hosted configurator: the same form as the original bdpay configurator
// (pick gateways, paste the credentials each one gave you), except that it
// saves to this workspace instead of printing a project. Secrets are write-only:
// once saved they are encrypted and the page only ever sees "set" plus a hint.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as api from "../../api/endpoints";
import type { ChecklistItem, FieldDef, GatewayDef, GatewayListing, GatewayState, TestResult, ViaDef } from "../../api/types";
import { CATALOG, STABILITY } from "../../lib/catalog";
import { useEnv } from "../../lib/env";
import { useSession } from "../../lib/session";
import {
  ConfirmButton, CopyRow, ErrorNote, FormError, GatewayStatusPill, Loading, PageHead, Pill, ReadOnlyNote, Time, useToast,
} from "../../ui";

const TEST_TONE: Record<TestResult["result"], string> = { verified: "", rejected: "bad", reachable: "warn", unreachable: "warn" };
const TEST_TITLE: Record<TestResult["result"], string> = {
  verified: "Credentials accepted", rejected: "Credentials rejected", reachable: "Gateway reachable", unreachable: "Gateway not reachable",
};

export default function Gateways() {
  const ctx = useEnv();
  const { platform } = useSession();
  const listing = useQuery({ queryKey: ctx.key("gateways"), queryFn: () => api.gateways.list(ctx.env) });
  // Gateways ticked on this visit that are not saved yet.
  const [adding, setAdding] = useState<string[]>([]);
  const [via, setVia] = useState<ViaDef | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  const states = useMemo(() => new Map((listing.data?.gateways ?? []).map((g) => [g.id, g])), [listing.data]);
  // The server only lists what this deployment and mode offer (no simulator in live, ...).
  const defs = useMemo(() => CATALOG.gateways.filter((g) => states.has(g.id)), [states]);
  const shown = defs.filter((g) => states.get(g.id)?.configured || adding.includes(g.id));

  if (listing.isPending) return <Loading label="Loading gateways" />;
  if (listing.error) return <ErrorNote error={listing.error} retry={() => listing.refetch()} />;

  const toggle = (g: GatewayDef) => {
    const state = states.get(g.id);
    if (!state?.configured && !adding.includes(g.id)) setAdding((list) => [...list, g.id]);
    setFocus(g.id);
  };
  const canAddSsl = via && !via.noApi && states.has("sslcommerz") && !states.get("sslcommerz")?.configured && !adding.includes("sslcommerz");

  return (
    <div className="stack">
      <PageHead title="Gateways">
        Connect your own merchant accounts. Money moves from your customer, through the provider, straight into your
        account; this service only starts and verifies the payment.
      </PageHead>
      <ReadOnlyNote show={!ctx.canWrite} live={ctx.mode === "live"} />

      <section className="card">
        <h2>1. Choose gateways</h2>
        <p className="lede">
          Ticked cards are connected. Dashed cards have no public merchant API: customers pay with them inside an
          aggregator's checkout.
        </p>
        <div className="gws">
          {defs.map((g) => {
            const state = states.get(g.id)!;
            const on = state.configured || adding.includes(g.id);
            return (
              <button key={g.id} type="button" className={`gw${on ? " on" : ""}`} aria-pressed={on} onClick={() => toggle(g)}>
                <span className="tick" aria-hidden="true">✓</span>
                <strong>{g.name}</strong>
                <small>{g.kind}</small>
                {state.configured
                  ? <GatewayStatusPill status={state.status} enabled={state.enabled} />
                  : <span className={`pill ${STABILITY[g.st][1]}`}>{STABILITY[g.st][0]}</span>}
              </button>
            );
          })}
          {CATALOG.via.map((v) => (
            <button key={v.id} type="button" className="gw via" onClick={() => setVia(v)}>
              <strong>{v.name}</strong>
              <small>No direct API</small>
              <span className="pill p-via">Via aggregator</span>
            </button>
          ))}
        </div>
        {via && (
          <div className="info" style={{ marginTop: 12 }}>
            <b>{via.name}:</b> {via.noApi ? via.via : <>customers choose it inside the checkout of {via.via}.</>}{" "}
            {canAddSsl && ctx.canWrite && (
              <button type="button" className="small" onClick={() => { setAdding((l) => [...l, "sslcommerz"]); setFocus("sslcommerz"); setVia(null); }}>
                Add SSLCOMMERZ
              </button>
            )}
          </div>
        )}
        {platform && !platform.allow_experimental && (
          <p className="help" style={{ marginTop: 10 }}>
            The experimental UddoktaPay driver is switched off on this deployment.
          </p>
        )}
      </section>

      <section className="stack">
        <h2>2. Enter credentials</h2>
        {shown.length === 0 && (
          <div className="card empty">
            <h3>No gateway connected yet</h3>
            <p>Tick a gateway above to enter the credentials its provider gave you.</p>
          </div>
        )}
        {shown.map((g) => (
          <GatewayPanel key={g.id} def={g} state={states.get(g.id)!} listing={listing.data!} open={focus === g.id || !states.get(g.id)!.configured}
            focused={focus === g.id} onDiscard={() => setAdding((l) => l.filter((x) => x !== g.id))}
            onSaved={() => setAdding((l) => l.filter((x) => x !== g.id))} />
        ))}
      </section>

      <CheckoutOrder gateways={listing.data!.gateways} />
      <Checklist />

      <section className="card">
        <h3>Prefer to run it yourself?</h3>
        <p className="lede">
          The same engine is open source. The generator builds a complete, tested Python service for the gateways you
          pick, entirely in your browser.
        </p>
        <Link className="btn small" to={`/generator?g=${shown.filter((g) => !g.cloudOnly).map((g) => g.id).join(",")}`}>
          Generate a self-hosted project
        </Link>
      </section>
    </div>
  );
}

// ------------------------------------------------------------------- panel
function initialValues(def: GatewayDef, state: GatewayState): Record<string, string> {
  const values: Record<string, string> = {};
  for (const f of def.f) {
    if (f.t === "secret" || f.t === "textarea") values[f.k] = "";
    else values[f.k] = state.values[f.k] ?? (state.configured ? "" : (f.cloudVal ?? f.val ?? ""));
  }
  return values;
}

function GatewayPanel({ def, state, listing, open, focused, onDiscard, onSaved }: {
  def: GatewayDef; state: GatewayState; listing: GatewayListing; open: boolean; focused: boolean;
  onDiscard: () => void; onSaved: () => void;
}) {
  const ctx = useEnv();
  const toast = useToast();
  const queryClient = useQueryClient();
  const ref = useRef<HTMLDetailsElement>(null);
  const [values, setValues] = useState(() => initialValues(def, state));
  const [dirty, setDirty] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const live = ctx.mode === "live";
  const hasDemo = !live && def.f.some((f) => f.demo);

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [focused]);
  // After a save the server's view is the truth again.
  useEffect(() => {
    if (!dirty) setValues(initialValues(def, state));
  }, [def, state, dirty]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ctx.key("gateways") });
    queryClient.invalidateQueries({ queryKey: ctx.key("checklist") });
    queryClient.invalidateQueries({ queryKey: ctx.key("overview") });
    queryClient.invalidateQueries({ queryKey: ctx.key("checkout-options") });
  };
  const save = useMutation({
    mutationFn: () => {
      const payload: Record<string, string | boolean> = {};
      for (const f of def.f) payload[f.k] = f.t === "check" ? values[f.k] === "1" : (values[f.k] ?? "");
      return api.gateways.save(ctx.env, def.id, payload);
    },
    onSuccess: () => {
      setDirty(false);
      setTest(null);
      refresh();
      onSaved();
      toast(`${def.name} saved`);
    },
  });
  const runTest = useMutation({
    mutationFn: () => api.gateways.test(ctx.env, def.id),
    onSuccess: (result) => { setTest(result); refresh(); },
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.gateways.setEnabled(ctx.env, def.id, enabled),
    onSuccess: (s) => { refresh(); toast(`${def.name} switched ${s.enabled ? "on" : "off"}`); },
  });
  const remove = useMutation({
    mutationFn: () => api.gateways.remove(ctx.env, def.id),
    onSuccess: () => { refresh(); toast(`${def.name} removed`); },
  });

  const set = (key: string, value: string) => {
    setValues((v) => ({ ...v, [key]: value }));
    setDirty(true);
  };
  const fieldError = (key: string) => (save.error instanceof ApiError ? save.error.field(key) : undefined);
  const shownTest = test ?? state.last_test;
  const tone = def.st === "exp" ? "bad" : def.st === "beta" ? "warn" : "";
  const disabled = !ctx.canWrite;

  return (
    <details ref={ref} className="card gwpanel" open={open}>
      <summary>
        <span>{def.name} {def.f.length ? "credentials" : ""}</span>
        <span className="row">
          {state.configured && <GatewayStatusPill status={state.status} enabled={state.enabled} />}
          <span className={`pill ${STABILITY[def.st][1]}`}>{STABILITY[def.st][0]}</span>
        </span>
      </summary>
      <form className="body" autoComplete="off" noValidate onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className={`info ${tone}`}>
          <b>{def.f.length ? "Where to get them:" : "About:"}</b> {def.get}{" "}
          {def.docs && <a href={def.docs} target="_blank" rel="noopener noreferrer">Docs</a>}
        </div>
        {def.need.length > 0 && <ul className="need">{def.need.map((n) => <li key={n}>{n}</li>)}</ul>}

        {def.f.map((f) => (
          <CredentialField key={f.k} field={f} value={values[f.k] ?? ""} onChange={(v) => set(f.k, v)} disabled={disabled}
            saved={state.secrets[f.k]} error={fieldError(f.k)} />
        ))}

        {hasDemo && ctx.canWrite && (
          <>
            <div className="row" style={{ marginTop: 12 }}>
              <button type="button" className="small" onClick={() => {
                setValues((v) => ({ ...v, ...Object.fromEntries(def.f.filter((f) => f.demo).map((f) => [f.k, f.demo!])) }));
                setDirty(true);
                toast("Sandbox demo values filled");
              }}>Fill sandbox demo values</button>
            </div>
            {def.demoNote && <p className="help" style={{ marginTop: 6 }}>{def.demoNote}</p>}
          </>
        )}
        {live && def.f.some((f) => f.demo) && (
          <p className="help" style={{ marginTop: 10 }}>Live mode needs your own live credentials; published sandbox values are refused.</p>
        )}

        {save.error && !(save.error instanceof ApiError && save.error.errors.length) ? <FormError error={save.error} /> : null}
        {save.error instanceof ApiError && save.error.errors.length > 0 && (
          <p className="field-error" role="alert">{save.error.message}</p>
        )}

        {ctx.canWrite && (
          <div className="row" style={{ marginTop: 16 }}>
            {def.f.length > 0 && (
              <button type="submit" className="primary" disabled={save.isPending || (state.configured && !dirty)}>
                {save.isPending ? "Saving…" : state.configured ? "Save changes" : `Connect ${def.name}`}
              </button>
            )}
            {state.configured && (
              <>
                <button type="button" disabled={runTest.isPending || dirty} title={dirty ? "Save your changes first" : undefined}
                  onClick={() => runTest.mutate()}>
                  {runTest.isPending ? "Testing…" : "Test connection"}
                </button>
                <button type="button" className="quiet" disabled={toggle.isPending} onClick={() => toggle.mutate(!state.enabled)}>
                  {state.enabled ? "Switch off" : "Switch on"}
                </button>
                {def.f.length > 0 && (
                  <ConfirmButton label="Remove" title={`Remove ${def.name}?`} confirmLabel="Remove credentials" danger className="quiet danger"
                    body={<p>The stored credentials are deleted. Payments already in progress with {def.name} could then no longer be verified, so this is refused while any are pending.</p>}
                    onConfirm={() => remove.mutateAsync()} />
                )}
              </>
            )}
            {!state.configured && <button type="button" className="quiet" onClick={onDiscard}>Cancel</button>}
          </div>
        )}
        <FormError error={runTest.error ?? toggle.error} />

        {shownTest && (
          <div className={`info ${TEST_TONE[shownTest.result]}`} style={{ marginTop: 14 }} role="status">
            <b>{TEST_TITLE[shownTest.result]}.</b> {shownTest.message}{" "}
            <span className="mute small">Checked <Time value={shownTest.checked_at} />.</span>
          </div>
        )}
        {state.last_error && state.status !== "verified" && (
          <div className="info bad" style={{ marginTop: 10 }}>
            <b>Last error from {def.name}</b> (<Time value={state.last_error_at} />): <code>{state.last_error}</code>
          </div>
        )}

        {state.configured && <ProviderUrls def={def} state={state} listing={listing} live={live} />}
      </form>
    </details>
  );
}

function CredentialField({ field: f, value, onChange, disabled, saved, error }: {
  field: FieldDef; value: string; onChange: (v: string) => void; disabled: boolean;
  saved?: { set: boolean; hint: string }; error?: string;
}) {
  const id = `f_${f.k}`;
  const help = f.cloudHelp ?? f.help;
  if (f.t === "check") {
    return (
      <>
        <label className="check">
          <input type="checkbox" checked={value === "1"} disabled={disabled} onChange={(e) => onChange(e.target.checked ? "1" : "")} />
          <span>{f.l}</span>
        </label>
        {error && <span className="field-error" role="alert">{error}</span>}
      </>
    );
  }
  const isSecret = f.t === "secret" || f.t === "textarea";
  const keep = isSecret && saved?.set ? `Saved (${saved.hint}). Leave empty to keep it.` : undefined;
  return (
    <div>
      <label className="f" htmlFor={id}>
        {f.l}{f.req ? <em> *</em> : null}
        {help && <span className="help">{help}</span>}
      </label>
      {f.t === "textarea" && (
        <textarea id={id} value={value} disabled={disabled} spellCheck={false} aria-invalid={error ? true : undefined}
          placeholder={keep ?? "-----BEGIN ... KEY----- or bare base64"} onChange={(e) => onChange(e.target.value)} />
      )}
      {f.t === "select" && (
        <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          {(f.opts ?? []).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      )}
      {(f.t === "text" || f.t === "secret") && (
        <input id={id} type={f.t === "secret" ? "password" : "text"} value={value} disabled={disabled} spellCheck={false}
          autoComplete={f.t === "secret" ? "new-password" : "off"} aria-invalid={error ? true : undefined}
          placeholder={keep} onChange={(e) => onChange(e.target.value)} />
      )}
      {error && <span className="field-error" role="alert">{error}</span>}
    </div>
  );
}

function ProviderUrls({ def, state, listing, live }: { def: GatewayDef; state: GatewayState; listing: GatewayListing; live: boolean }) {
  if (!state.urls.ipn && !def.whitelist) return null;
  return (
    <div style={{ marginTop: 18 }}>
      <h4>Give these to {def.name}</h4>
      {state.urls.ipn && (
        <>
          <p className="help" style={{ marginTop: 6 }}>
            IPN (notification) URL. Register it in the {def.name} merchant panel
            {def.id === "sslcommerz" ? " under My Store → IPN Settings" : ""}. It is only a hint: every notification is
            re-verified with {def.name}.
          </p>
          <CopyRow value={state.urls.ipn} label="IPN URL" />
        </>
      )}
      {def.whitelist && (
        <>
          <p className="help" style={{ marginTop: 10 }}>
            {def.name} only accepts calls from addresses it has whitelisted{live ? "" : " (in production)"}. Ask them to
            whitelist this server address and callback domain.
          </p>
          {listing.egress_ips.length
            ? listing.egress_ips.map((ip) => <CopyRow key={ip} value={ip} label="Server IPv4" />)
            : <p className="info warn small">The operator of this deployment has not published its outbound IP yet. Ask them for it.</p>}
          <CopyRow value={listing.callback_domain} label="Callback domain" />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------ checkout order
function CheckoutOrder({ gateways }: { gateways: GatewayState[] }) {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const enabled = gateways.filter((g) => g.configured && g.enabled).sort((a, b) => a.position - b.position);
  const reorder = useMutation({
    mutationFn: (order: string[]) => api.gateways.reorder(ctx.env, order),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ctx.key("gateways") });
      queryClient.invalidateQueries({ queryKey: ctx.key("checkout-options") });
    },
  });
  if (enabled.length < 2) return null;
  const move = (index: number, by: number) => {
    const order = enabled.map((g) => g.id);
    const [id] = order.splice(index, 1);
    order.splice(index + by, 0, id!);
    reorder.mutate(order);
  };
  return (
    <section className="card">
      <h3>Order on your checkout page</h3>
      <p className="lede">Customers see the enabled gateways in this order on hosted checkout and payment links.</p>
      <ol className="checklist">
        {enabled.map((g, i) => {
          const def = CATALOG.gateways.find((d) => d.id === g.id);
          return (
            <li key={g.id} style={{ gridTemplateColumns: "26px minmax(0,1fr) auto" }}>
              <span className="mute num">{i + 1}.</span>
              <span><b>{def?.checkout.label ?? g.id}</b><span className="detail">{def?.name} · {def?.checkout.hint}</span></span>
              <span className="row">
                <button type="button" className="small quiet" disabled={!ctx.canWrite || i === 0 || reorder.isPending} onClick={() => move(i, -1)} aria-label={`Move ${def?.name} up`}>↑</button>
                <button type="button" className="small quiet" disabled={!ctx.canWrite || i === enabled.length - 1 || reorder.isPending} onClick={() => move(i, 1)} aria-label={`Move ${def?.name} down`}>↓</button>
              </span>
            </li>
          );
        })}
      </ol>
      <FormError error={reorder.error} />
    </section>
  );
}

// ----------------------------------------------------------------- checklist
export function Checklist({ compact = false }: { compact?: boolean }) {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ctx.key("checklist"), queryFn: () => api.gateways.checklist(ctx.env) });
  const confirm = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) => api.gateways.confirm(ctx.env, id, done),
    onSuccess: (data) => queryClient.setQueryData(ctx.key("checklist"), data),
  });
  if (query.isPending) return <Loading label="Loading checklist" />;
  if (query.error) return <ErrorNote error={query.error} />;
  const items = query.data.items;
  const left = items.filter((i) => i.state !== "done").length;
  if (compact && left === 0) return null;
  return (
    <section className="card">
      <div className="card-head">
        <h3>{compact ? "Still to do" : ctx.mode === "live" ? "3. Go-live checklist" : "3. Before you go live"}</h3>
        <Pill tone={left ? "warn" : "ok"}>{left ? `${left} to do` : "All done"}</Pill>
      </div>
      <ul className="checklist">
        {items.filter((i) => !compact || i.state !== "done").map((item) => <ChecklistRow key={item.id} item={item}
          canWrite={ctx.canWrite} busy={confirm.isPending} onConfirm={(done) => confirm.mutate({ id: item.id, done })} />)}
      </ul>
      <FormError error={confirm.error} />
    </section>
  );
}

function ChecklistRow({ item, canWrite, busy, onConfirm }: { item: ChecklistItem; canWrite: boolean; busy: boolean; onConfirm: (done: boolean) => void }) {
  return (
    <li className={item.state}>
      <span className="mark" aria-hidden="true">✓</span>
      <span>
        {item.label}
        <span className="sr-only"> ({item.state === "done" ? "done" : item.state === "manual" ? "needs your confirmation" : "to do"})</span>
        {item.detail && <span className="detail">{item.detail}</span>}
      </span>
      {item.manual && canWrite ? (
        <button type="button" className="small quiet" disabled={busy} onClick={() => onConfirm(item.state !== "done")}>
          {item.state === "done" ? "Undo" : "Mark done"}
        </button>
      ) : <span />}
    </li>
  );
}
