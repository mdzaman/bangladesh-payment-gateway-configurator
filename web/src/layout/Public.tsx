// Pieces shared by the landing page and the self-host generator.
import { Link } from "react-router-dom";
import { CATALOG, STABILITY } from "../lib/catalog";
import { useSession } from "../lib/session";
import { Brand } from "./Shell";

export function PublicNav() {
  const { me, platform } = useSession();
  return (
    <div className="pubnav">
      <Brand />
      <nav aria-label="Site">
        <Link to="/generator">Self-host generator</Link>
        {platform?.docs_enabled && <a href="/docs">API</a>}
        {me ? <Link className="cta" to="/app">Open dashboard</Link> : (
          <>
            <Link to="/login">Sign in</Link>
            {platform?.allow_signup !== false && <Link className="cta" to="/signup">Create workspace</Link>}
          </>
        )}
      </nav>
    </div>
  );
}

export function GatewayMatrix({ selfHostOnly = false }: { selfHostOnly?: boolean }) {
  return (
    <div className="scroll">
      <table className="cards">
        <thead><tr><th>Gateway</th><th>Type</th><th>Driver</th><th>Refund by API</th><th>How it verifies</th><th>Docs</th></tr></thead>
        <tbody>
          {CATALOG.gateways.filter((g) => !selfHostOnly || !g.cloudOnly).map((g) => (
            <tr key={g.id}>
              <td data-l="Gateway"><b>{g.name}</b></td>
              <td data-l="Type">{g.kind}</td>
              <td data-l="Driver"><span className={`pill ${STABILITY[g.st][1]}`}>{STABILITY[g.st][0]}</span></td>
              <td data-l="Refund">{g.refund}</td>
              <td data-l="Verifies">{g.verify}</td>
              <td data-l="Docs">{g.docs ? <a href={g.docs} target="_blank" rel="noopener noreferrer">Docs</a> : null}</td>
            </tr>
          ))}
          {CATALOG.via.map((v) => (
            <tr key={v.id}>
              <td data-l="Gateway"><b>{v.name}</b></td>
              <td data-l="Reached" colSpan={5} className="mute">{v.via}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SafetyRules() {
  return (
    <ol>
      <li>The amount is taken from the order saved on the server, never from the browser. A provider answer with a different amount, currency or order number moves the order to <code>needs_review</code>, not <code>paid</code>.</li>
      <li>Return URLs and IPNs are only hints. Every one triggers a server-to-server check (bKash execute then query, SSLCOMMERZ verify_sign then Validation API, Nagad signature and verify, aamarPay search, shurjoPay verification).</li>
      <li>Callback links are HMAC-signed and expire, so one order's link cannot be replayed for another.</li>
      <li>Orders only move forward. Duplicate callbacks and IPNs are harmless.</li>
      <li>Access tokens (bKash, shurjoPay, EPS, Upay) are cached and refreshed 5 minutes early; bKash can lock merchants that grant a token on every request.</li>
      <li>TLS verification cannot be switched off, and live mode refuses plain-HTTP URLs.</li>
      <li>Secrets and phone numbers are masked in logs; every state change is kept in an audit table.</li>
    </ol>
  );
}

export function Compliance({ hosted = false }: { hosted?: boolean }) {
  return (
    <p>
      Under the Payment and Settlement Systems Act 2024, operating a payment system or providing payment services in
      Bangladesh needs a Bangladesh Bank licence. This is merchant-side integration software: money always moves
      through a licensed provider into the merchant's own account.{" "}
      {hosted
        ? "Each workspace connects its own merchant accounts, and the service never receives, holds or moves funds. "
        : ""}
      Do not use it to collect other people's money into your account (that makes you an aggregator), and do not
      collect business payments into personal wallets. Card data never touches your server because every flow is a
      hosted redirect. Bangla QR is issued by your bank or MFS and has no open e-commerce API. This is not legal
      advice; check with Bangladeshi counsel before running a hosted service for other merchants.
    </p>
  );
}

export function SiteFooter() {
  return (
    <footer className="site">
      bdpay cloud · open source (MIT) · not affiliated with bKash, Nagad, SSLCOMMERZ, aamarPay, shurjoPay, EPS, PortPos,
      PayStation, Upay or UddoktaPay · beta drivers follow public and community references and must be checked against
      the provider's onboarding pack before going live.
    </footer>
  );
}
