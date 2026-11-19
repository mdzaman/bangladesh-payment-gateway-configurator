// API keys, webhook endpoints and deliveries, the event log, and copy-paste
// integration snippets for this environment.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, useParams } from "react-router-dom";
import * as api from "../../api/endpoints";
import type { ApiKey, WebhookEndpoint } from "../../api/types";
import { useEnv } from "../../lib/env";
import { when } from "../../lib/format";
import { useSession } from "../../lib/session";
import {
  CodeBlock, ConfirmButton, Empty, ErrorNote, FormError, Loading, Modal, PageHead, Pill, ReadOnlyNote, SecretOnce,
  TextField, Time, useToast,
} from "../../ui";

const TABS = [["keys", "API keys"], ["webhooks", "Webhooks"], ["events", "Events"], ["quickstart", "Quick start"]] as const;

export default function Developers() {
  const ctx = useEnv();
  const { tab = "keys" } = useParams();
  return (
    <div className="stack">
      <PageHead title="Developers">
        Everything your server needs to take payments in {ctx.mode} mode.
      </PageHead>
      <nav className="tabs" aria-label="Developer sections">
        {TABS.map(([id, label]) => <NavLink key={id} to={ctx.path(`developers/${id}`)} end
          aria-current={tab === id ? "page" : undefined}>{label}</NavLink>)}
      </nav>
      <ReadOnlyNote show={!ctx.canWrite && tab !== "quickstart" && tab !== "events"} live={ctx.mode === "live"} />
      {tab === "keys" && <Keys />}
      {tab === "webhooks" && <Webhooks />}
      {tab === "events" && <Events />}
      {tab === "quickstart" && <QuickStart />}
    </div>
  );
}

// ---------------------------------------------------------------- API keys
function Keys() {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ctx.key("api-keys"), queryFn: () => api.apiKeys.list(ctx.env) });
  const [created, setCreated] = useState<(ApiKey & { key: string }) | null>(null);
  const revoke = useMutation({
    mutationFn: (id: string) => api.apiKeys.revoke(ctx.env, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ctx.key("api-keys") }),
  });
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorNote error={query.error} />;
  const keys = query.data.data;
  return (
    <>
      {created && (
        <SecretOnce title={`API key "${created.name}"`} value={created.key}>
          Copy it now and keep it on your server only. It is stored as a hash and cannot be shown again.
        </SecretOnce>
      )}
      <section className="card flush">
        <div className="card-head" style={{ padding: "16px 20px 0" }}>
          <h2>API keys</h2>
          {ctx.canWrite && <NewKey scopes={query.data.scopes} onCreated={setCreated} />}
        </div>
        <p className="lede" style={{ padding: "0 20px" }}>
          Send one as <code>Authorization: Bearer …</code> from your server. Never put a key in a browser or a mobile app.
          {ctx.mode === "sandbox" ? " Sandbox keys start with bdp_test_ and can never move real money." : " Live keys start with bdp_live_."}
        </p>
        {keys.length === 0 ? <Empty title="No API keys yet"><p>Create one to call the API from your server.</p></Empty> : (
          <div className="scroll">
            <table className="cards">
              <thead><tr><th>Name</th><th>Key</th><th>Can do</th><th>Last used</th><th>Created</th><th /></tr></thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id}>
                    <td data-l="Name"><b>{k.name}</b></td>
                    <td data-l="Key"><code>{k.display}</code></td>
                    <td data-l="Can do" className="small">{k.scopes.includes("*") ? "Everything" : k.scopes.join(", ")}</td>
                    <td data-l="Last used" className="small"><Time value={k.last_used_at} /></td>
                    <td data-l="Created" className="small"><Time value={k.created_at} /></td>
                    <td className="actions">
                      {k.revoked_at ? <Pill tone="grey">Revoked</Pill> : k.expires_at && new Date(k.expires_at) < new Date() ? <Pill tone="grey">Expired</Pill>
                        : ctx.canWrite && (
                          <ConfirmButton label="Revoke" title={`Revoke "${k.name}"?`} confirmLabel="Revoke key" danger className="small quiet danger"
                            body={<p>Requests using this key stop working immediately. This cannot be undone.</p>}
                            onConfirm={() => revoke.mutateAsync(k.id)} />
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function NewKey({ scopes, onCreated }: { scopes: string[]; onCreated: (key: ApiKey & { key: string }) => void }) {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [restricted, setRestricted] = useState(false);
  const [picked, setPicked] = useState<string[]>(["orders:read"]);
  const create = useMutation({
    mutationFn: () => api.apiKeys.create(ctx.env, { name, scopes: restricted ? picked : null }),
    onSuccess: (key) => {
      onCreated(key);
      queryClient.invalidateQueries({ queryKey: ctx.key("api-keys") });
      queryClient.invalidateQueries({ queryKey: ctx.key("checklist") });
      setOpen(false);
      setName("");
    },
  });
  return (
    <>
      <button type="button" className="primary small" onClick={() => setOpen(true)}>New API key</button>
      <Modal open={open} onClose={() => setOpen(false)} title={`New ${ctx.mode} API key`} footer={
        <>
          <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>
          <button type="submit" form="new-key" className="primary" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create key"}</button>
        </>
      }>
        <form id="new-key" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <TextField label="Name" help="Where it will be used, so you know what to revoke later." value={name} onChange={setName} required maxLength={60} placeholder="Shop backend" />
          <label className="f">Permissions</label>
          <div className="seg">
            <button type="button" aria-pressed={!restricted} onClick={() => setRestricted(false)}>Full access</button>
            <button type="button" aria-pressed={restricted} onClick={() => setRestricted(true)}>Restricted</button>
          </div>
          {restricted && scopes.map((s) => (
            <label key={s} className="check" style={{ margin: "8px 0 0" }}>
              <input type="checkbox" checked={picked.includes(s)} onChange={(e) => setPicked((p) => (e.target.checked ? [...p, s] : p.filter((x) => x !== s)))} />
              <span><code>{s}</code></span>
            </label>
          ))}
          <FormError error={create.error} />
        </form>
      </Modal>
    </>
  );
}

// ----------------------------------------------------------------- webhooks
function Webhooks() {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const toast = useToast();
  const endpoints = useQuery({ queryKey: ctx.key("webhooks"), queryFn: () => api.webhooks.list(ctx.env) });
  const deliveries = useQuery({ queryKey: ctx.key("deliveries"), queryFn: () => api.webhooks.deliveries(ctx.env), refetchInterval: 15_000 });
  const worker = useQuery({ queryKey: ctx.key("worker"), queryFn: () => api.worker(ctx.env), refetchInterval: 30_000 });
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ctx.key("webhooks") });
    queryClient.invalidateQueries({ queryKey: ctx.key("deliveries") });
    queryClient.invalidateQueries({ queryKey: ctx.key("checklist") });
  };
  const act = useMutation({
    mutationFn: async (a: { kind: "test" | "toggle" | "rotate" | "delete" | "resend"; endpoint?: WebhookEndpoint; id?: string }) => {
      if (a.kind === "test") { await api.webhooks.test(ctx.env, a.endpoint!.id); return "Test event queued"; }
      if (a.kind === "toggle") { await api.webhooks.update(ctx.env, a.endpoint!.id, { enabled: !a.endpoint!.enabled }); return a.endpoint!.enabled ? "Endpoint paused" : "Endpoint resumed"; }
      if (a.kind === "rotate") {
        const r = await api.webhooks.rotateSecret(ctx.env, a.endpoint!.id);
        setSecret({ title: `New signing secret for ${r.url}`, value: r.secret });
        return "Signing secret replaced";
      }
      if (a.kind === "delete") { await api.webhooks.remove(ctx.env, a.endpoint!.id); return "Endpoint deleted"; }
      await api.webhooks.redeliver(ctx.env, a.id!);
      return "Queued for delivery again";
    },
    onSuccess: (message) => { refresh(); toast(message); },
  });
  if (endpoints.isPending) return <Loading />;
  if (endpoints.error) return <ErrorNote error={endpoints.error} />;
  const list = endpoints.data.data;
  const names = new Map(list.map((e) => [e.id, e.url]));

  return (
    <>
      {worker.data && !worker.data.running && (
        <p className="info warn" role="status">
          No background worker has reported in the last few minutes, so webhooks are not being delivered right now.
          Whoever runs this deployment needs to start <code>bdpay-cloud worker</code>.
        </p>
      )}
      {secret && <SecretOnce title={secret.title} value={secret.value}>Copy it now: your server needs it to verify the <code>BDPay-Signature</code> header. It cannot be shown again.</SecretOnce>}
      <section className="card flush">
        <div className="card-head" style={{ padding: "16px 20px 0" }}>
          <h2>Endpoints</h2>
          {ctx.canWrite && <NewEndpoint types={endpoints.data.event_types} onCreated={(e) => { setSecret({ title: `Signing secret for ${e.url}`, value: e.secret }); refresh(); }} />}
        </div>
        <p className="lede" style={{ padding: "0 20px" }}>
          Your server is told the moment a payment is confirmed, fails, or is refunded. Each event is signed and retried
          for about two days until your server answers 2xx.
        </p>
        {list.length === 0 ? <Empty title="No endpoints yet"><p>Without one, your server has to ask for each order's status.</p></Empty> : (
          <div className="scroll">
            <table className="cards">
              <thead><tr><th>URL</th><th>Events</th><th>Health</th><th>Last delivered</th><th /></tr></thead>
              <tbody>
                {list.map((e) => (
                  <tr key={e.id}>
                    <td data-l="URL"><code>{e.url}</code>{e.description && <div className="small mute">{e.description}</div>}</td>
                    <td data-l="Events" className="small">{e.event_types.includes("*") ? "All events" : e.event_types.join(", ")}</td>
                    <td data-l="Health">{!e.enabled ? <Pill tone="grey">Paused</Pill> : e.consecutive_failures > 0
                      ? <Pill tone="bad">{e.consecutive_failures} failed in a row</Pill> : <Pill tone="ok">Healthy</Pill>}</td>
                    <td data-l="Last delivered" className="small"><Time value={e.last_success_at} /></td>
                    <td className="actions">
                      {ctx.canWrite && (
                        <>
                          <button type="button" className="small quiet" disabled={act.isPending} onClick={() => act.mutate({ kind: "test", endpoint: e })}>Send test</button>{" "}
                          <button type="button" className="small quiet" disabled={act.isPending} onClick={() => act.mutate({ kind: "toggle", endpoint: e })}>{e.enabled ? "Pause" : "Resume"}</button>{" "}
                          <ConfirmButton label="New secret" title="Replace the signing secret?" confirmLabel="Replace secret"
                            body={<p>Deliveries are signed with the new secret at once. Update your server straight after, or its signature check will reject them.</p>}
                            onConfirm={() => act.mutateAsync({ kind: "rotate", endpoint: e })} />{" "}
                          <ConfirmButton label="Delete" title="Delete this endpoint?" confirmLabel="Delete endpoint" danger className="small quiet danger"
                            body={<p>Events will no longer be sent to <code>{e.url}</code>. Its delivery history is removed too.</p>}
                            onConfirm={() => act.mutateAsync({ kind: "delete", endpoint: e })} />
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <FormError error={act.error} />

      <section className="card flush">
        <div className="card-head" style={{ padding: "16px 20px 0" }}><h2>Recent deliveries</h2></div>
        {deliveries.isPending ? <Loading /> : deliveries.error ? <ErrorNote error={deliveries.error} /> : deliveries.data.data.length === 0
          ? <Empty title="Nothing sent yet" /> : (
            <div className="scroll">
              <table className="cards">
                <thead><tr><th>Event</th><th>To</th><th>Result</th><th className="right">Attempts</th><th>When</th><th /></tr></thead>
                <tbody>
                  {deliveries.data.data.map((d) => (
                    <tr key={d.id}>
                      <td data-l="Event"><code>{d.event_type}</code></td>
                      <td data-l="To" className="small">{names.get(d.endpoint_id) ?? d.endpoint_id}</td>
                      <td data-l="Result">
                        <Pill tone={d.status === "succeeded" ? "ok" : d.status === "failed" ? "bad" : d.attempts ? "warn" : "info"}>
                          {d.status === "succeeded" ? `Delivered (${d.last_status_code})` : d.status === "failed" ? "Gave up" : d.attempts ? "Retrying" : "Queued"}
                        </Pill>
                        {d.last_error && d.status !== "succeeded" && <div className="small mute">{d.last_error}{d.next_attempt_at ? <> · next try <Time value={d.next_attempt_at} /></> : null}</div>}
                      </td>
                      <td data-l="Attempts" className="right num">{d.attempts}</td>
                      <td data-l="When" className="small nowrap"><Time value={d.last_attempt_at ?? d.created_at} /></td>
                      <td className="actions">
                        {ctx.canWrite && d.status !== "pending" && (
                          <button type="button" className="small quiet" disabled={act.isPending} onClick={() => act.mutate({ kind: "resend", id: d.id })}>Send again</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>

      <section className="card">
        <h3>Verify every webhook</h3>
        <p className="lede">Check the signature against the raw request body, refuse timestamps older than five minutes, and treat the event id as an idempotency key (an event can arrive more than once).</p>
        <CodeBlock code={VERIFY_SNIPPET} />
      </section>
    </>
  );
}

const VERIFY_SNIPPET = `# Python. header = request.headers["BDPay-Signature"], body = raw request bytes
import hashlib, hmac, time

def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    timestamp, given = int(parts["t"]), parts["v1"]
    if abs(time.time() - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)`;

function NewEndpoint({ types, onCreated }: { types: string[]; onCreated: (e: WebhookEndpoint & { secret: string }) => void }) {
  const ctx = useEnv();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [all, setAll] = useState(true);
  const [picked, setPicked] = useState<string[]>(["order.paid"]);
  const create = useMutation({
    mutationFn: () => api.webhooks.create(ctx.env, { url, description, event_types: all ? null : picked }),
    onSuccess: (e) => { onCreated(e); setOpen(false); setUrl(""); setDescription(""); },
  });
  return (
    <>
      <button type="button" className="primary small" onClick={() => setOpen(true)}>Add endpoint</button>
      <Modal open={open} onClose={() => setOpen(false)} title="Add a webhook endpoint" footer={
        <>
          <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>
          <button type="submit" form="new-endpoint" className="primary" disabled={create.isPending}>{create.isPending ? "Adding…" : "Add endpoint"}</button>
        </>
      }>
        <form id="new-endpoint" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <TextField label="Endpoint URL" type="url" value={url} onChange={setUrl} required placeholder="https://shop.example/webhooks/bdpay"
            help={ctx.mode === "live" ? "Must be https and reachable from the internet." : "Must be reachable from the internet (a tunnel such as ngrok works for testing)."} />
          <TextField label="Description" value={description} onChange={setDescription} maxLength={200} />
          <label className="f">Events</label>
          <div className="seg">
            <button type="button" aria-pressed={all} onClick={() => setAll(true)}>All events</button>
            <button type="button" aria-pressed={!all} onClick={() => setAll(false)}>Choose</button>
          </div>
          {!all && types.map((t) => (
            <label key={t} className="check" style={{ margin: "8px 0 0" }}>
              <input type="checkbox" checked={picked.includes(t)} onChange={(e) => setPicked((p) => (e.target.checked ? [...p, t] : p.filter((x) => x !== t)))} />
              <span><code>{t}</code></span>
            </label>
          ))}
          <FormError error={create.error} />
        </form>
      </Modal>
    </>
  );
}

// ------------------------------------------------------------------- events
function Events() {
  const ctx = useEnv();
  const query = useQuery({ queryKey: ctx.key("events"), queryFn: () => api.webhooks.events(ctx.env), refetchInterval: 15_000 });
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorNote error={query.error} />;
  const events = query.data.data;
  return (
    <section className="card">
      <h2>Events</h2>
      <p className="lede">What happened in this environment, exactly as it is sent to your webhook endpoints. Events exist whether or not an endpoint is configured.</p>
      {events.length === 0 ? <Empty title="No events yet" /> : events.map((e) => {
        const order = (e.data as { order?: { order_id?: string } }).order;
        return (
          <details key={e.id} style={{ borderTop: "1px solid var(--line)", padding: "10px 0" }}>
            <summary style={{ cursor: "pointer" }}>
              <code>{e.type}</code> {order?.order_id && <span className="mute small">order {order.order_id}</span>}{" "}
              <span className="mute small">· {when(e.created_at)}</span>
            </summary>
            <div style={{ marginTop: 10 }}><CodeBlock code={JSON.stringify(e, null, 2)} /></div>
          </details>
        );
      })}
    </section>
  );
}

// -------------------------------------------------------------- quick start
function QuickStart() {
  const ctx = useEnv();
  const { platform } = useSession();
  const base = platform?.public_url ?? window.location.origin;
  const key = ctx.mode === "live" ? "bdp_live_…" : "bdp_test_…";
  const gateway = ctx.mode === "live" ? "bkash" : "simulator";
  const [lang, setLang] = useState<"curl" | "python" | "node" | "php">("curl");
  const snippets = {
    curl: `# 1. Record the order. The amount saved here is the only amount ever accepted.
curl -X POST ${base}/v1/orders \\
  -H "Authorization: Bearer ${key}" -H "Content-Type: application/json" \\
  -d '{"order_id":"INV1001","amount":"1250.00","gateway":"${gateway}","customer":{"name":"Rahim","phone":"01700000000"},
       "return_url":"https://shop.example/thanks?order={order_id}&status={status}"}'

# 2. Get the provider's payment page and send the customer there.
curl -X POST ${base}/v1/orders/INV1001/pay -H "Authorization: Bearer ${key}"
# -> {"redirect_url":"https://..."}

# 3. Ship only after your webhook receives order.paid (or this says "paid").
curl ${base}/v1/orders/INV1001 -H "Authorization: Bearer ${key}"

# Refund, fully or partly. Always send an Idempotency-Key with refunds.
curl -X POST ${base}/v1/orders/INV1001/refund \\
  -H "Authorization: Bearer ${key}" -H "Idempotency-Key: refund-INV1001-1" -H "Content-Type: application/json" \\
  -d '{"amount":"200.00","reason":"returned item"}'`,
    python: `import httpx   # pip install httpx

bdpay = httpx.Client(base_url="${base}/v1", headers={"Authorization": "Bearer ${key}"}, timeout=40)

order = bdpay.post("/orders", json={
    "order_id": "INV1001", "amount": "1250.00", "gateway": "${gateway}",
    "customer": {"name": "Rahim", "phone": "01700000000"},
    "return_url": "https://shop.example/thanks?order={order_id}&status={status}",
}).raise_for_status().json()

url = bdpay.post(f"/orders/{order['order_id']}/pay").raise_for_status().json()["redirect_url"]
# redirect the customer to url; fulfil the order when your webhook receives order.paid`,
    node: `const bdpay = (path, init = {}) => fetch("${base}/v1" + path, {
  ...init, headers: { Authorization: "Bearer ${key}", "Content-Type": "application/json", ...init.headers },
}).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail.message); return r.json(); });

const order = await bdpay("/orders", { method: "POST", body: JSON.stringify({
  order_id: "INV1001", amount: "1250.00", gateway: "${gateway}",
  customer: { name: "Rahim", phone: "01700000000" },
  return_url: "https://shop.example/thanks?order={order_id}&status={status}",
}) });

const { redirect_url } = await bdpay(\`/orders/\${order.order_id}/pay\`, { method: "POST" });
// res.redirect(redirect_url); fulfil the order when your webhook receives order.paid`,
    php: `<?php
function bdpay(string $method, string $path, ?array $body = null): array {
    $ch = curl_init("${base}/v1" . $path);
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST => $method, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 40,
        CURLOPT_HTTPHEADER => ["Authorization: Bearer ${key}", "Content-Type: application/json"],
        CURLOPT_POSTFIELDS => $body === null ? null : json_encode($body),
    ]);
    $response = json_decode(curl_exec($ch), true);
    if (curl_getinfo($ch, CURLINFO_HTTP_CODE) >= 400) throw new Exception($response["detail"]["message"]);
    return $response;
}

$order = bdpay("POST", "/orders", ["order_id" => "INV1001", "amount" => "1250.00", "gateway" => "${gateway}",
    "customer" => ["name" => "Rahim", "phone" => "01700000000"],
    "return_url" => "https://shop.example/thanks?order={order_id}&status={status}"]);
$pay = bdpay("POST", "/orders/INV1001/pay");
header("Location: " . $pay["redirect_url"]);   // fulfil the order when your webhook receives order.paid`,
  };
  return (
    <>
      <section className="card">
        <h2>Three calls from your server</h2>
        <p className="lede">Create the order, send the customer to the provider, and act on the verified result. Replace the key with one from the API keys tab.</p>
        <div className="seg" style={{ marginBottom: 12 }}>
          {(["curl", "python", "node", "php"] as const).map((l) => (
            <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)}>{l === "curl" ? "cURL" : l === "node" ? "Node" : l === "php" ? "PHP" : "Python"}</button>
          ))}
        </div>
        <CodeBlock code={snippets[lang]} />
      </section>
      <section className="card">
        <h3>Good to know</h3>
        <ul style={{ paddingLeft: 18, margin: "8px 0 0" }}>
          <li>Order ids: letters, digits, <code>-</code> or <code>_</code>, up to 40 characters (Nagad: letters and digits only, up to 20). Use a new order id for each new attempt.</li>
          <li>Asking to pay an order twice returns the same payment page; it never opens a second payment.</li>
          <li>Statuses only move forward: <code>created → pending → paid | failed | cancelled | expired | needs_review</code>, then <code>partially_refunded → refunded</code>.</li>
          <li><code>needs_review</code> means the provider reported a payment whose amount, currency or order number did not match. Do not ship; check it by hand.</li>
          <li>Coming from a self-hosted bdpay? The same calls work under <code>{base}/bdpay/…</code> with the key in <code>X-BDPAY-ADMIN</code>.</li>
          <li>Prefer no code at all? Use <NavLink to={ctx.path("links")}>checkout sessions and payment links</NavLink>.</li>
        </ul>
        {platform?.docs_enabled && <p style={{ marginTop: 12 }}><a className="btn small" href="/docs" target="_blank" rel="noopener noreferrer">Full API reference</a></p>}
      </section>
    </>
  );
}
