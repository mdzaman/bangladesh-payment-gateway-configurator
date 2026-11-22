// Payment links (a reusable page anyone can pay on) and one-off checkout sessions.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../../api/endpoints";
import type { CheckoutOption } from "../../api/types";
import { useEnv } from "../../lib/env";
import { taka } from "../../lib/format";
import { CopyButton, Empty, ErrorNote, FormError, Loading, Modal, PageHead, Pill, ReadOnlyNote, TextField, Time, useToast } from "../../ui";

const SESSION_TONE = { open: "info", paid: "ok", expired: "grey", cancelled: "grey" } as const;

function GatewayChoice({ options, value, onChange }: { options: CheckoutOption[]; value: string[]; onChange: (v: string[]) => void }) {
  if (options.length < 2) return null;
  return (
    <fieldset style={{ border: 0, padding: 0, margin: "14px 0 0" }}>
      <legend className="f" style={{ padding: 0 }}>Payment methods<span className="help">Untick all to offer every enabled gateway.</span></legend>
      {options.map((o) => (
        <label key={o.id} className="check" style={{ margin: "6px 0" }}>
          <input type="checkbox" checked={value.includes(o.id)} onChange={(e) => onChange(e.target.checked ? [...value, o.id] : value.filter((x) => x !== o.id))} />
          <span>{o.label} <span className="mute">({o.id})</span></span>
        </label>
      ))}
    </fieldset>
  );
}

export default function Links() {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const toast = useToast();
  const links = useQuery({ queryKey: ctx.key("links"), queryFn: () => api.checkout.links(ctx.env) });
  const sessions = useQuery({ queryKey: ctx.key("sessions"), queryFn: () => api.checkout.sessions(ctx.env), refetchInterval: 30_000 });
  const options = useQuery({ queryKey: ctx.key("checkout-options"), queryFn: () => api.checkout.options(ctx.env) });
  const setActive = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => api.checkout.setLinkActive(ctx.env, id, active),
    onSuccess: (link) => { queryClient.invalidateQueries({ queryKey: ctx.key("links") }); toast(link.active ? "Link reopened" : "Link closed"); },
  });
  const methods = options.data?.data ?? [];
  const noMethods = options.data !== undefined && methods.length === 0;

  return (
    <div className="stack">
      <PageHead title="Checkout & links" actions={ctx.canWrite && !noMethods ? (
        <>
          <NewSession methods={methods} />
          <NewLink methods={methods} />
        </>
      ) : undefined}>
        Hosted pages where your customer chooses how to pay. No code needed: create a link and share it.
      </PageHead>
      <ReadOnlyNote show={!ctx.canWrite} live={ctx.mode === "live"} twoFactor={ctx.needsTwoFactor} />
      {noMethods && <p className="info warn">No gateway is switched on, so a checkout page would have nothing to offer. <Link to={ctx.path("gateways")}>Connect one first.</Link></p>}

      <section className="card flush">
        <div className="card-head" style={{ padding: "16px 20px 0" }}><h2>Payment links</h2></div>
        <p className="lede" style={{ padding: "0 20px" }}>A reusable page: put it on Facebook, in an SMS or on an invoice. Every visitor gets their own checkout.</p>
        {links.isPending ? <Loading /> : links.error ? <ErrorNote error={links.error} /> : links.data.data.length === 0 ? (
          <Empty title="No payment links yet"><p>Create one for a product, a fee, or an amount the customer types in.</p></Empty>
        ) : (
          <div className="scroll">
            <table className="cards">
              <thead><tr><th>Title</th><th className="right">Amount</th><th className="right">Paid</th><th>State</th><th>Created</th><th /></tr></thead>
              <tbody>
                {links.data.data.map((l) => (
                  <tr key={l.id}>
                    <td data-l="Title"><b>{l.title}</b>{l.description && <div className="small mute">{l.description}</div>}</td>
                    <td data-l="Amount" className="right num">{l.amount ? taka(l.amount) : <span className="mute">{taka(l.min_amount)} to {taka(l.max_amount)}</span>}</td>
                    <td data-l="Paid" className="right num">{l.paid_count}{l.max_payments ? ` of ${l.max_payments}` : ""}</td>
                    <td data-l="State"><Pill tone={l.active && (!l.max_payments || l.paid_count < l.max_payments) ? "ok" : "grey"}>
                      {!l.active ? "Closed" : l.max_payments && l.paid_count >= l.max_payments ? "Limit reached" : "Open"}</Pill></td>
                    <td data-l="Created" className="small nowrap"><Time value={l.created_at} /></td>
                    <td className="actions">
                      <CopyButton text={l.url} label="Copy link" />{" "}
                      <a className="btn small quiet" href={l.url} target="_blank" rel="noopener noreferrer">Open</a>{" "}
                      {ctx.canWrite && (
                        <button type="button" className="small quiet" disabled={setActive.isPending} onClick={() => setActive.mutate({ id: l.id, active: !l.active })}>
                          {l.active ? "Close" : "Reopen"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <FormError error={setActive.error} />

      <section className="card flush">
        <div className="card-head" style={{ padding: "16px 20px 0" }}><h2>Recent checkouts</h2></div>
        <p className="lede" style={{ padding: "0 20px" }}>One per customer visit, whether it came from a link, the dashboard or your server's API.</p>
        {sessions.isPending ? <Loading /> : sessions.error ? <ErrorNote error={sessions.error} /> : sessions.data.data.length === 0 ? (
          <Empty title="No checkouts yet" />
        ) : (
          <div className="scroll">
            <table className="cards">
              <thead><tr><th>Reference</th><th className="right">Amount</th><th>State</th><th>Paid by order</th><th>Created</th><th /></tr></thead>
              <tbody>
                {sessions.data.data.map((s) => (
                  <tr key={s.id}>
                    <td data-l="Reference">{s.reference ?? s.description ?? <span className="mute">—</span>}{s.reference && s.description && <div className="small mute">{s.description}</div>}</td>
                    <td data-l="Amount" className="right num"><b>{taka(s.amount)}</b></td>
                    <td data-l="State"><Pill tone={SESSION_TONE[s.status]}>{s.status}</Pill></td>
                    <td data-l="Paid by order">{s.paid_order_id ? <Link to={ctx.path(`payments/${s.paid_order_id}`)}><code>{s.paid_order_id}</code></Link> : <span className="mute">—</span>}</td>
                    <td data-l="Created" className="small nowrap"><Time value={s.created_at} /></td>
                    <td className="actions">{s.status === "open" && <CopyButton text={s.url} label="Copy link" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function NewLink({ methods }: { methods: CheckoutOption[] }) {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [fixed, setFixed] = useState(true);
  const [amount, setAmount] = useState("");
  const [min, setMin] = useState("10");
  const [max, setMax] = useState("50000");
  const [gateways, setGateways] = useState<string[]>([]);
  const [collect, setCollect] = useState<string[]>(["name", "phone"]);
  const [limit, setLimit] = useState("");
  const [successUrl, setSuccessUrl] = useState("");
  const create = useMutation({
    mutationFn: () => api.checkout.createLink(ctx.env, {
      title, description, amount: fixed ? amount : null, min_amount: fixed ? null : min, max_amount: fixed ? null : max,
      gateways: gateways.length ? gateways : null, collect, max_payments: limit ? Number(limit) : null,
      success_url: successUrl.trim() || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ctx.key("links") });
      setOpen(false);
      setTitle(""); setDescription(""); setAmount("");
    },
  });
  const toggleCollect = (k: string) => setCollect((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]));
  return (
    <>
      <button type="button" className="primary" onClick={() => setOpen(true)}>New payment link</button>
      <Modal open={open} onClose={() => setOpen(false)} title="New payment link" wide footer={
        <>
          <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>
          <button type="submit" form="new-link" className="primary" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create link"}</button>
        </>
      }>
        <form id="new-link" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <TextField label="Title" help="Customers see this at the top of the page." value={title} onChange={setTitle} required maxLength={100} placeholder="Monthly tuition, Class 8" />
          <TextField label="Description" value={description} onChange={setDescription} maxLength={500} />
          <label className="f">Amount</label>
          <div className="seg">
            <button type="button" aria-pressed={fixed} onClick={() => setFixed(true)}>Fixed amount</button>
            <button type="button" aria-pressed={!fixed} onClick={() => setFixed(false)}>Customer enters it</button>
          </div>
          {fixed ? <TextField label="Amount (BDT)" value={amount} onChange={setAmount} inputMode="decimal" required placeholder="1250.00" />
            : (
              <div className="row2">
                <TextField label="Smallest amount (BDT)" value={min} onChange={setMin} inputMode="decimal" />
                <TextField label="Largest amount (BDT)" value={max} onChange={setMax} inputMode="decimal" />
              </div>
            )}
          <fieldset style={{ border: 0, padding: 0, margin: "14px 0 0" }}>
            <legend className="f" style={{ padding: 0 }}>Ask the customer for</legend>
            <div className="row">
              {[["name", "Name"], ["phone", "Mobile number"], ["email", "Email"]].map(([k, label]) => (
                <label key={k} className="check" style={{ margin: 0 }}>
                  <input type="checkbox" checked={collect.includes(k!)} onChange={() => toggleCollect(k!)} /><span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <GatewayChoice options={methods} value={gateways} onChange={setGateways} />
          <div className="row2">
            <TextField label="Stop after this many payments" help="Empty: no limit. Use 1 for a single invoice." value={limit} onChange={setLimit} inputMode="numeric" />
            <TextField label="Send customers here after paying" help="Optional. Empty: our confirmation page." value={successUrl} onChange={setSuccessUrl} type="url" placeholder="https://shop.example/thanks" />
          </div>
          <FormError error={create.error} />
        </form>
      </Modal>
    </>
  );
}

function NewSession({ methods }: { methods: CheckoutOption[] }) {
  const ctx = useEnv();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [description, setDescription] = useState("");
  const [gateways, setGateways] = useState<string[]>([]);
  const create = useMutation({
    mutationFn: () => api.checkout.createSession(ctx.env, {
      amount, reference: reference.trim() || null, description, gateways: gateways.length ? gateways : null,
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ctx.key("sessions") }),
  });
  const close = () => { setOpen(false); create.reset(); setAmount(""); setReference(""); setDescription(""); };
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>One-off checkout</button>
      <Modal open={open} onClose={close} title="One-off checkout" footer={create.data ? (
        <>
          <CopyButton text={create.data.url} label="Copy link" className="quiet" />
          <a className="btn primary" href={create.data.url} target="_blank" rel="noopener noreferrer">Open the page</a>
        </>
      ) : (
        <>
          <button type="button" className="quiet" onClick={close}>Cancel</button>
          <button type="submit" form="new-session" className="primary" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create checkout"}</button>
        </>
      )}>
        {create.data ? (
          <div className="stack tight">
            <p>Checkout for <b>{taka(create.data.amount)}</b> is ready. It can be paid once and expires <Time value={create.data.expires_at} />.</p>
            <code style={{ display: "block" }}>{create.data.url}</code>
          </div>
        ) : (
          <form id="new-session" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <p className="lede">For one customer and one amount, for example an invoice you send by message.</p>
            <TextField label="Amount (BDT)" value={amount} onChange={setAmount} inputMode="decimal" required placeholder="1250.00" />
            <TextField label="Your reference" help="Invoice or order number. Shown to the customer." value={reference} onChange={setReference} maxLength={100} />
            <TextField label="Description" value={description} onChange={setDescription} maxLength={200} />
            <GatewayChoice options={methods} value={gateways} onChange={setGateways} />
            <FormError error={create.error} />
          </form>
        )}
      </Modal>
    </>
  );
}
