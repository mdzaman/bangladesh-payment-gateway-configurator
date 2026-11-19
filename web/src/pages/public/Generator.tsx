// The self-host generator: the original bdpay configurator, as a page of this
// app. Pick gateways, paste credentials, get a complete tested Python project.
// Everything happens in this browser tab: the reference source is bundled with
// the page, and nothing typed here is sent to any server (this page makes no
// requests at all once it has loaded).
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { FieldDef, GatewayDef, ViaDef } from "../../api/types";
import files from "../../generated/coreFiles.json";
import { Compliance, GatewayMatrix, PublicNav, SafetyRules, SiteFooter } from "../../layout/Public";
import { CATALOG, SELF_HOST_GATEWAYS, STABILITY } from "../../lib/catalog";
import {
  build, bundleMarkdown, countFiles, golive, isSecretPath, lineCount, mask, randomHex, treeGroups, validate, warnings,
  type GeneratorInput, type Project,
} from "../../lib/generator";
import { download, zipBytes } from "../../lib/zip";
import { Rich, useToast } from "../../ui";

const FILES = files as Record<string, string>;
const VALID = new Set(SELF_HOST_GATEWAYS.map((g) => g.id));

function initialSelection(param: string | null): string[] {
  const fromUrl = (param ?? "").split(",").filter((id) => VALID.has(id));
  if (fromUrl.length) return fromUrl;
  try {
    const saved: unknown = JSON.parse(localStorage.getItem("bdpay.sel") || "null");
    if (Array.isArray(saved)) return saved.filter((id): id is string => typeof id === "string" && VALID.has(id));
  } catch { /* nothing saved */ }
  return ["bkash", "sslcommerz"];
}

export default function Generator() {
  const toast = useToast();
  const [params] = useSearchParams();
  const [selected, setSelected] = useState<string[]>(() => initialSelection(params.get("g")));
  const [vals, setVals] = useState<Record<string, string>>({});
  const [env, setEnv] = useState<"sandbox" | "live">("sandbox");
  const [publicUrl, setPublicUrl] = useState("http://localhost:8000");
  const [pkg, setPkg] = useState("bdpay-service");
  const [owner, setOwner] = useState("");
  const [resultUrl, setResultUrl] = useState("");
  const [secret, setSecret] = useState(() => randomHex(32));
  const [admin, setAdmin] = useState(() => randomHex(20));
  const [peek, setPeek] = useState(false);
  const [via, setVia] = useState<ViaDef | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const outRef = useRef<HTMLElement>(null);

  useEffect(() => {
    document.title = "Self-host generator · bdpay";
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("bdpay.sel", JSON.stringify(selected));
    } catch { /* private mode */ }
  }, [selected]);

  const list = useMemo(() => SELF_HOST_GATEWAYS.filter((g) => selected.includes(g.id)), [selected]);
  const toggle = (id: string, on: boolean) => setSelected((s) => (on ? [...new Set([...s, id])] : s.filter((x) => x !== id)));
  const setVal = (key: string, value: string) => setVals((v) => ({ ...v, [key]: value }));
  const input: GeneratorInput = { gateways: list, env, publicUrl, pkg, owner, resultUrl, secret, admin, vals };

  const generate = (e: FormEvent) => {
    e.preventDefault();
    const errs = validate(input);
    setErrors(errs);
    if (errs.length) {
      toast(`Fix ${errs.length} item${errs.length > 1 ? "s" : ""} first`, "bad");
      return;
    }
    const built = build(input, FILES);
    setProject(built);
    setNotes(warnings(input));
    setCurrent("README.md");
    setReveal(false);
    if (window.innerWidth < 980) {
      outRef.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
    toast(`Generated ${Object.keys(built).length} files`);
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied");
    } catch {
      toast("Copy blocked: select the code and copy by hand", "bad");
    }
  };
  const name = pkg.trim() || "bdpay-service";
  const guideList = list.length ? list : SELF_HOST_GATEWAYS;

  return (
    <>
      <header className="band hero">
        <div className="sun" aria-hidden="true" />
        <div className="wrap" style={{ position: "relative" }}>
          <PublicNav />
          <h1>Generate your own payment service</h1>
          <p>
            Pick the gateways, paste the credentials each one gave you, press Generate. You get a complete, tested Python
            payment service for your configuration: drivers, server, tests, Docker and a go-live checklist. Everything
            is built in this page; nothing you type is sent anywhere.
          </p>
          <div className="steps">
            <span><b>1</b> Choose gateways</span><span><b>2</b> Enter credentials</span><span><b>3</b> Generate code</span>
            <span><b>4</b> Test in sandbox</span><span><b>5</b> Deploy</span>
          </div>
        </div>
      </header>

      <main className="band">
        <div className="wrap two">
          <form autoComplete="off" noValidate onSubmit={generate} className="stack">
            <section className="card">
              <h2>1. Choose gateways</h2>
              <p className="lede">Ticked cards get a driver. Dashed cards have no public merchant API: customers pay with them inside an aggregator's checkout.</p>
              <div className="gws">
                {SELF_HOST_GATEWAYS.map((g) => (
                  <label key={g.id} className="gw">
                    <input type="checkbox" value={g.id} checked={selected.includes(g.id)} onChange={(e) => toggle(g.id, e.target.checked)} />
                    <span className="tick" aria-hidden="true">✓</span>
                    <strong>{g.name}</strong>
                    <small>{g.kind}</small>
                    <span className={`pill ${STABILITY[g.st][1]}`}>{STABILITY[g.st][0]}</span>
                  </label>
                ))}
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
                  {!via.noApi && !selected.includes("sslcommerz") && (
                    <button type="button" className="small" onClick={() => { toggle("sslcommerz", true); setVia(null); }}>Add SSLCOMMERZ</button>
                  )}
                </div>
              )}
            </section>

            <section className="card">
              <h2>2. Project settings</h2>
              <label className="f">Environment</label>
              <div className="seg wide" role="radiogroup" aria-label="Environment">
                <label><input type="radio" name="env" value="sandbox" checked={env === "sandbox"} onChange={() => setEnv("sandbox")} /> Sandbox (test money)</label>
                <label className="live"><input type="radio" name="env" value="live" checked={env === "live"} onChange={() => setEnv("live")} /> Live (real money)</label>
              </div>
              <div className="row2">
                <div>
                  <label className="f" htmlFor="publicUrl">Public URL of this service <em>*</em><span className="help">Providers send customers and IPNs here. Use https; ngrok works for testing.</span></label>
                  <input type="url" id="publicUrl" value={publicUrl} onChange={(e) => setPublicUrl(e.target.value)} />
                </div>
                <div>
                  <label className="f" htmlFor="pkg">Project name<span className="help">Folder and package name in pyproject.</span></label>
                  <input type="text" id="pkg" value={pkg} onChange={(e) => setPkg(e.target.value)} />
                </div>
              </div>
              <div className="row2">
                <div>
                  <label className="f" htmlFor="owner">Copyright owner (MIT licence)</label>
                  <input type="text" id="owner" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Your name or company" />
                </div>
                <div>
                  <label className="f" htmlFor="resultUrl">After-payment page (optional)<span className="help">e.g. https://shop.example/thanks?order={"{order_id}"}&amp;status={"{status}"}</span></label>
                  <input type="url" id="resultUrl" value={resultUrl} onChange={(e) => setResultUrl(e.target.value)} placeholder="leave empty to use the built-in page" />
                </div>
              </div>
              <div className="row2">
                <div>
                  <label className="f" htmlFor="secret">Callback signing secret<span className="help">Auto-generated, 64 hex characters.</span></label>
                  <input type={peek ? "text" : "password"} id="secret" value={secret} onChange={(e) => setSecret(e.target.value)} spellCheck={false} />
                </div>
                <div>
                  <label className="f" htmlFor="admin">Admin API token<span className="help">Protects create/refund/requery endpoints.</span></label>
                  <input type={peek ? "text" : "password"} id="admin" value={admin} onChange={(e) => setAdmin(e.target.value)} spellCheck={false} />
                </div>
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <button type="button" className="small" onClick={() => { setSecret(randomHex(32)); setAdmin(randomHex(20)); toast("New secrets made"); }}>New secrets</button>
                <button type="button" className="small" onClick={() => setPeek((v) => !v)}>{peek ? "Hide secrets" : "Show secrets"}</button>
              </div>
            </section>

            {list.map((g) => <CredentialsPanel key={g.id} def={g} vals={vals} setVal={setVal} live={env === "live"} />)}

            <section className="card">
              <h2>3. Generate</h2>
              <p className="lede">
                {list.length
                  ? `${list.length} gateway${list.length > 1 ? "s" : ""}: ${list.map((g) => g.name).join(", ")}. Generating builds ${countFiles(list)} files.`
                  : "Choose at least one gateway."}
              </p>
              <button type="submit" className="primary big wide" disabled={!list.length}>Generate full Python code</button>
              {errors.length > 0 && <ul className="errors" role="alert">{errors.map((x) => <li key={x}>{x}</li>)}</ul>}
            </section>
          </form>

          <aside className="out" aria-live="polite" ref={outRef}>
            <div className="row between" style={{ marginBottom: 10 }}>
              <div className="stats">
                <div><b>{project ? Object.keys(project).length : 0}</b>files</div>
                <div><b>{project ? list.length : 0}</b>gateway{list.length === 1 ? "" : "s"}</div>
                <div><b>{project ? lineCount(project).toLocaleString() : 0}</b>lines</div>
              </div>
              <div className="row">
                <button type="button" disabled={!project} onClick={() => project && download(`${name}.zip`, zipBytes(project, name, { isPrivate: isSecretPath }), "application/zip")}>
                  Download project (.zip)
                </button>
                <button type="button" disabled={!project} onClick={() => project && download(`${name}-bundle.md`, bundleMarkdown(project, name), "text/markdown")}>
                  Download as one Markdown
                </button>
              </div>
            </div>
            <div className="ide">
              <nav className="tree" aria-label="Project files">
                {project && treeGroups(project).map(({ dir, paths }) => (
                  <div key={dir || "."}>
                    {dir && <div className="dir">{dir}/</div>}
                    {paths.map((p) => (
                      <button key={p} type="button" className={p === current ? "on" : ""} style={{ paddingLeft: dir ? 22 : 12 }}
                        onClick={() => setCurrent(p)}>
                        {p.split("/").pop()}{isSecretPath(p) ? " 🔒" : ""}
                      </button>
                    ))}
                  </div>
                ))}
              </nav>
              <div className="pane">
                <div className="panebar">
                  <span>
                    {current ?? "no file"}
                    {current && isSecretPath(current) && <> 🔒 <button type="button" className="small" onClick={() => setReveal((v) => !v)}>{reveal ? "Mask" : "Reveal"}</button></>}
                  </span>
                  <span className="row">
                    <button type="button" className="small" disabled={!project || !current} onClick={() => project && current && copy(project[current]!)}>Copy file</button>
                    <button type="button" className="small" disabled={!project} onClick={() => project && copy(bundleMarkdown(project, name))}>Copy all</button>
                  </span>
                </div>
                {project && current ? <pre className="codeview" tabIndex={0}>{mask(current, project[current]!, reveal)}</pre> : (
                  <div className="ide-empty">
                    <div>
                      <svg viewBox="0 0 120 120" aria-hidden="true">
                        <rect x="18" y="14" width="70" height="90" rx="8" fill="#20352d" /><rect x="30" y="30" width="46" height="6" rx="3" fill="#3fbf8f" />
                        <rect x="30" y="44" width="34" height="6" rx="3" fill="#6f8f82" /><rect x="30" y="58" width="40" height="6" rx="3" fill="#6f8f82" />
                        <rect x="30" y="72" width="26" height="6" rx="3" fill="#6f8f82" /><circle cx="88" cy="86" r="20" fill="#e8263b" />
                        <path d="M80 86l6 6 11-12" stroke="#fff" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      <p>Your generated project appears here: every file, ready to copy or download.</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
            <p className="lede" style={{ marginTop: 10 }}>
              Files marked 🔒 hold your secrets (.env, keys). They are masked on screen; the download has the real values.
              Never commit them.
            </p>
            {notes.map((n) => <p key={n} className="info warn small">{n}</p>)}
            <p className="info small">
              Rather not run a server? <Link to="/signup">The hosted service</Link> uses this same engine, with a dashboard,
              webhooks and hosted checkout.
            </p>
          </aside>
        </div>
      </main>

      <section className="band guide" id="guide">
        <div className="wrap">
          <nav className="toc" aria-label="Guide sections">
            <a href="#run">Run locally</a><a href="#testing">Test in sandbox</a><a href="#integrate">Plug into your app</a>
            <a href="#deploy">Deploy</a><a href="#golive">Go-live checklist</a><a href="#matrix">Gateway matrix</a>
            <a href="#rules">Safety rules</a><a href="#law">Compliance</a>
          </nav>

          <div className="gsec" id="run">
            <h2>Run it locally in five commands</h2>
            <p>Unzip the download, then:</p>
            <pre className="code">{`cd ${name}
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                 # offline tests with mock gateways, no credentials needed
python -m bdpay doctor              # checks settings, keys, gateway reachability, Dhaka clock
uvicorn app:app --reload            # open http://localhost:8000 (test console)`}</pre>
            <p style={{ marginTop: 12 }}>
              Providers must be able to reach your callback and IPN URLs. On a laptop, run <code>ngrok http 8000</code> and
              put the https address in <b>Public URL</b>, then generate again (or edit <code>BDPAY_PUBLIC_URL</code> in <code>.env</code>).
            </p>
          </div>

          <div className="gsec" id="testing">
            <h2>Test in sandbox</h2>
            <div className="scroll">
              <table className="cards">
                <thead><tr><th>Gateway</th><th>How to get sandbox access</th><th>Published test data</th></tr></thead>
                <tbody>
                  {guideList.map((g) => (
                    <tr key={g.id}><td data-l="Gateway"><b>{g.name}</b></td><td data-l="Access">{g.sandbox}</td><td data-l="Test data">{g.test}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p style={{ marginTop: 12 }}>
              Test each gateway at least for: a successful payment, a customer cancel, a failed payment, the same callback
              arriving twice (must stay one payment), and a refund where supported. The generated test suite already
              covers the mock versions of these; the sandbox run proves your credentials and URLs.
            </p>
          </div>

          <div className="gsec" id="integrate">
            <h2>Plug it into any application</h2>
            <p>
              <b>Option A, separate service (any language).</b> Run the generated service next to your app. Your app calls
              three endpoints with the <code>X-BDPAY-ADMIN</code> header, and gets told when an order is paid through
              the <code>on_paid</code> hook in <code>app.py</code> (point it at your own webhook or database).
            </p>
            <pre className="code">{`POST /bdpay/orders            {"order_id":"INV1001","amount":"1250.00","gateway":"bkash","customer":{"phone":"017..."}}
POST /bdpay/orders/INV1001/pay   -> {"redirect_url":"https://..."}   send the customer there
GET  /bdpay/orders/INV1001       -> {"status":"paid", ...}
POST /bdpay/orders/INV1001/refund {"amount":"200.00","reason":"returned item"}`}</pre>
            <p style={{ marginTop: 12 }}><b>Option B, inside a Python app.</b> Copy the <code>bdpay</code> folder in.</p>
            <pre className="code">{`# FastAPI
from bdpay import BdPay
from bdpay.web import build_router
pay = BdPay.from_env()
app.include_router(build_router(pay, on_paid=mark_invoice_paid))

# Flask / Django / scripts: use the service object directly
pay.create_order("INV1001", "1250.00", "sslcommerz", {"name": "Rahim", "phone": "01700000000"})
redirect(pay.start_payment("INV1001"))
# in your callback view for /bdpay/callback/<gateway>/<order_id>/<token>/<outcome>:
order = pay.handle_callback(gateway, order_id, token, outcome, {**request.args, **request.form})`}</pre>
            <p style={{ marginTop: 12 }}>
              <b>PHP, Node, WordPress, mobile apps:</b> use Option A. Mobile apps must never hold gateway secrets; they call
              your server, which calls bdpay.
            </p>
          </div>

          <div className="gsec" id="deploy">
            <h2>Deploy</h2>
            <pre className="code">docker compose up -d --build       # service on :8000 + reconcile worker every 10 min</pre>
            <ul>
              <li><b>Any VPS or cloud VM:</b> the compose file above, behind Nginx or Caddy with HTTPS. Keep <code>.env</code> and <code>keys/</code> on the server only.</li>
              <li><b>AWS:</b> run the image on ECS Fargate or App Runner, store secrets in Secrets Manager, and send outbound traffic through a NAT gateway with an Elastic IP so Nagad and Upay can whitelist one fixed IPv4. For several replicas, move the store from SQLite to Postgres/Redis (same method names in <code>store.py</code>).</li>
              <li><b>Reconcile:</b> always run <code>python -m bdpay reconcile</code> on a schedule. It re-checks payments left pending when a customer closes the tab or a callback is lost.</li>
            </ul>
          </div>

          <div className="gsec" id="golive">
            <h2>Go-live checklist</h2>
            <ol>{golive(list).map((x) => <li key={x}><Rich text={x} /></li>)}</ol>
          </div>

          <div className="gsec" id="matrix">
            <h2>What each gateway can do</h2>
            <GatewayMatrix selfHostOnly />
          </div>

          <div className="gsec" id="rules">
            <h2>Safety rules the generated code enforces</h2>
            <SafetyRules />
          </div>

          <div className="gsec" id="law" style={{ marginBottom: 0 }}>
            <h2>Compliance in one paragraph</h2>
            <Compliance />
          </div>
        </div>
      </section>
      <SiteFooter />
    </>
  );
}

function CredentialsPanel({ def, vals, setVal, live }: { def: GatewayDef; vals: Record<string, string>; setVal: (k: string, v: string) => void; live: boolean }) {
  const toast = useToast();
  const tone = def.st === "exp" ? "bad" : def.st === "beta" ? "warn" : "";
  return (
    <details className="card gwpanel" open>
      <summary>
        <span>{def.name} credentials</span>
        <span className={`pill ${STABILITY[def.st][1]}`}>{STABILITY[def.st][0]}</span>
      </summary>
      <div className="body">
        <div className={`info ${tone}`}><b>Where to get them:</b> {def.get} <a href={def.docs} target="_blank" rel="noopener noreferrer">Docs</a></div>
        <ul className="need">{def.need.map((n) => <li key={n}>{n}</li>)}</ul>
        {def.f.map((f) => <GeneratorField key={f.k} field={f} value={vals[f.k] ?? f.val ?? ""} onChange={(v) => setVal(f.k, v)} />)}
        {def.f.some((f) => f.demo) && (
          <>
            <div className="row" style={{ marginTop: 12 }}>
              <button type="button" className="small" onClick={() => {
                def.f.forEach((f) => { if (f.demo) setVal(f.k, f.demo); });
                toast("Sandbox demo values filled");
              }}>Fill sandbox demo values</button>
            </div>
            <p className="help" style={{ marginTop: 6 }}>{def.demoNote}{live ? " Live mode refuses these values." : ""}</p>
          </>
        )}
      </div>
    </details>
  );
}

function GeneratorField({ field: f, value, onChange }: { field: FieldDef; value: string; onChange: (v: string) => void }) {
  const id = `g_${f.k}`;
  if (f.t === "check") {
    return (
      <label className="check">
        <input type="checkbox" id={id} checked={value === "1"} onChange={(e) => onChange(e.target.checked ? "1" : "")} />
        <span>{f.l}</span>
      </label>
    );
  }
  return (
    <div>
      <label className="f" htmlFor={id}>{f.l}{f.req ? <em> *</em> : null}{f.help && <span className="help">{f.help}</span>}</label>
      {f.t === "textarea" && <textarea id={id} value={value} spellCheck={false} placeholder="-----BEGIN ... KEY----- or bare base64" onChange={(e) => onChange(e.target.value)} />}
      {f.t === "select" && (
        <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
          {(f.opts ?? []).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      )}
      {(f.t === "text" || f.t === "secret") && <input type={f.t === "secret" ? "password" : "text"} id={id} value={value} spellCheck={false} onChange={(e) => onChange(e.target.value)} />}
    </div>
  );
}
