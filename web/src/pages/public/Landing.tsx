import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Compliance, GatewayMatrix, PublicNav, SafetyRules, SiteFooter } from "../../layout/Public";
import { useSession } from "../../lib/session";

export default function Landing() {
  const { me, platform } = useSession();
  useEffect(() => {
    document.title = "bdpay cloud · Bangladesh payments, configured in one form";
  }, []);
  return (
    <>
      <header className="band hero">
        <div className="sun" aria-hidden="true" />
        <div className="wrap" style={{ position: "relative" }}>
          <PublicNav />
          <h1>Bangladesh payments, configured in one form</h1>
          <p>
            Pick the gateways, paste the credentials each one gave you, and take payments through one API: bKash, Nagad,
            SSLCOMMERZ, aamarPay, shurjoPay, EPS, PortPos, PayStation and Upay, plus every card, bank and wallet reached
            through those aggregators. Your money goes straight from the provider to your own merchant account.
          </p>
          <div className="steps">
            <span><b>1</b> Choose gateways</span><span><b>2</b> Enter credentials</span><span><b>3</b> Test in sandbox</span>
            <span><b>4</b> Go live</span>
          </div>
          <div className="row" style={{ marginTop: 26 }}>
            {me ? <Link className="btn big" to="/app">Open your dashboard</Link>
              : platform?.allow_signup !== false ? <Link className="btn big" to="/signup">Create a workspace</Link>
                : <Link className="btn big" to="/login">Sign in</Link>}
            <Link className="btn big ghost" to="/generator">Generate a self-hosted project</Link>
          </div>
        </div>
      </header>

      <main>
        <section className="band">
          <div className="wrap stack">
            <h2>Two ways to use it</h2>
            <div className="three">
              <div className="card">
                <h3>Hosted: this service</h3>
                <p className="lede">Connect your gateways in the dashboard and call one REST API from any language. Nothing to deploy.</p>
                <ul style={{ paddingLeft: 18, margin: 0 }}>
                  <li>Sandbox and live side by side, with a built-in test gateway</li>
                  <li>Hosted checkout and payment links, in English and Bangla</li>
                  <li>Signed webhooks with retries, and automatic reconciliation</li>
                  <li>Team roles, API keys and an audit log</li>
                </ul>
              </div>
              <div className="card">
                <h3>Self-hosted: your server</h3>
                <p className="lede">The generator prints a complete, tested Python service for the gateways you tick. It is built in your browser; nothing you type is sent anywhere.</p>
                <ul style={{ paddingLeft: 18, margin: 0 }}>
                  <li>Drivers, server, tests, Docker and a go-live checklist</li>
                  <li>The same engine and the same rules as the hosted service</li>
                  <li>One <code>.env</code> file; SQLite by default</li>
                </ul>
                <p style={{ margin: "14px 0 0" }}><Link className="btn small" to="/generator">Open the generator</Link></p>
              </div>
              <div className="card">
                <h3>Run the whole platform</h3>
                <p className="lede">Everything here is open source under the MIT licence: the engine, this dashboard, the workers and the deployment files.</p>
                <ul style={{ paddingLeft: 18, margin: 0 }}>
                  <li><code>docker compose up</code> for a single server</li>
                  <li>Stateless API and workers on PostgreSQL for more</li>
                  <li>Each tenant's credentials encrypted, each tenant isolated</li>
                </ul>
              </div>
            </div>
          </div>
        </section>

        <section className="band guide">
          <div className="wrap">
            <div className="gsec">
              <h2>Three calls from your server</h2>
              <pre className="code">{`POST /v1/orders                {"order_id":"INV1001","amount":"1250.00","gateway":"bkash","customer":{"phone":"017..."}}
POST /v1/orders/INV1001/pay    -> {"redirect_url":"https://..."}   send the customer there
GET  /v1/orders/INV1001        -> {"status":"paid", ...}           or receive the order.paid webhook
POST /v1/orders/INV1001/refund {"amount":"200.00","reason":"returned item"}`}</pre>
              <p style={{ marginTop: 12 }}>
                PHP, Node, WordPress, Laravel, Django, mobile back ends: anything that can make an HTTPS request. Mobile
                apps never hold gateway secrets; they call your server, which calls this API.
              </p>
            </div>
            <div className="gsec">
              <h2>What each gateway can do</h2>
              <GatewayMatrix />
            </div>
            <div className="gsec">
              <h2>Safety rules the engine enforces</h2>
              <SafetyRules />
            </div>
            <div className="gsec" style={{ marginBottom: 0 }}>
              <h2>Compliance in one paragraph</h2>
              <Compliance hosted />
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
