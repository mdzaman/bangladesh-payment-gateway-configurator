// Two-factor sign-in, on the Account page: set it up, switch it off, replace the
// recovery codes. The secret and the recovery codes each appear once, come
// straight from the server's answer, and are kept only in this component's
// state: never in storage, never in a URL.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import * as api from "../../api/endpoints";
import type { TwoFactorSetup } from "../../api/types";
import { when } from "../../lib/format";
import { encodeQr, qrPath } from "../../lib/qr";
import { useSession } from "../../lib/session";
import { download } from "../../lib/zip";
import {
  CopyButton, ErrorNote, FormError, Loading, NO_FACTOR, Pill, SecondFactorField, TextField, asSecondFactor, useToast,
  type FactorInput,
} from "../../ui";

/** The otpauth:// link as a QR code, drawn here in the page: the link holds the
 *  secret, so it is not sent to a QR service. Black on white whatever the theme,
 *  with the four-module margin scanners expect. */
function QrImage({ text, label }: { text: string; label: string }) {
  const code = useMemo(() => encodeQr(text), [text]);
  const side = code.size + 8;
  return (
    <svg className="qr" viewBox={`0 0 ${side} ${side}`} role="img" aria-label={label} shapeRendering="crispEdges">
      <rect width={side} height={side} fill="#fff" />
      <path d={qrPath(code)} fill="#000" />
    </svg>
  );
}

function RecoveryCodes({ codes, account, brand, onDone }: { codes: string[]; account: string; brand: string; onDone: () => void }) {
  const file = [`${brand}: recovery codes for ${account}`, `Made on ${new Date().toISOString().slice(0, 10)}. Each code works once.`, "", ...codes, ""].join("\n");
  return (
    <div className="secret-once" role="status" style={{ marginBottom: 14 }}>
      <b>Save your recovery codes now</b>
      <p className="small" style={{ margin: "4px 0 0" }}>
        If you cannot use your authenticator app, each of these signs you in once. Keep them apart from your password:
        in a password manager, or printed. They are stored only as hashes and cannot be shown again.
      </p>
      <ul className="codes" aria-label="Recovery codes">{codes.map((code) => <li key={code}><code>{code}</code></li>)}</ul>
      <div className="row">
        <CopyButton text={codes.join("\n")} label="Copy all" className="small" />
        <button type="button" className="small" onClick={() => download("recovery-codes.txt", file, "text/plain")}>Download as a file</button>
        <span className="grow" />
        <button type="button" className="small primary" onClick={onDone}>I have saved them</button>
      </div>
    </div>
  );
}

type View = "idle" | "password" | "scan" | "new-codes" | "disable";

export default function TwoFactor({ onSessionsChanged }: { onSessionsChanged: () => void }) {
  const { me, platform, refresh } = useSession();
  const toast = useToast();
  const status = useQuery({ queryKey: ["two-factor"], queryFn: api.twoFactor.status });
  const [view, setView] = useState<View>("idle");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [factor, setFactor] = useState<FactorInput>(NO_FACTOR);
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);

  const show = (next: View) => {
    setView(next);
    setPassword("");
    setCode("");
    setFactor(NO_FACTOR);
    if (next !== "scan") setSetup(null);
    for (const mutation of [begin, confirm, disable, renew]) mutation.reset();
  };
  const begin = useMutation({
    mutationFn: () => api.twoFactor.setup(password),
    onSuccess: (data) => { show("scan"); setSetup(data); },
  });
  const confirm = useMutation({
    mutationFn: () => api.twoFactor.confirm(code),
    onSuccess: async (data) => {
      show("idle");
      setRecoveryCodes(data.recovery_codes);
      await Promise.all([refresh(), status.refetch()]);
      onSessionsChanged();
      toast("Two-factor authentication is on. Other devices were signed out.");
    },
  });
  const disable = useMutation({
    mutationFn: () => api.twoFactor.disable(password, asSecondFactor(factor)),
    onSuccess: async () => {
      show("idle");
      setRecoveryCodes(null);
      await Promise.all([refresh(), status.refetch()]);
      onSessionsChanged();
      toast("Two-factor authentication is off. Other devices were signed out.");
    },
  });
  const renew = useMutation({
    mutationFn: () => api.twoFactor.newRecoveryCodes(password, asSecondFactor(factor)),
    onSuccess: async (data) => {
      show("idle");
      setRecoveryCodes(data.recovery_codes);
      await status.refetch();
    },
  });
  if (!me) return null;
  const submit = (mutation: { mutate: () => void }) => (e: FormEvent) => { e.preventDefault(); mutation.mutate(); };
  const state = status.data;

  return (
    <section className="card" id="two-factor">
      <div className="card-head">
        <h2>Two-factor authentication</h2>
        {state && <Pill tone={state.enabled ? "ok" : "grey"}>{state.enabled ? "On" : "Off"}</Pill>}
      </div>
      {recoveryCodes && (
        <RecoveryCodes codes={recoveryCodes} account={me.user.email} brand={platform?.brand_name ?? "bdpay cloud"} onDone={() => setRecoveryCodes(null)} />
      )}
      {status.isPending ? <Loading /> : status.error ? <ErrorNote error={status.error} retry={() => status.refetch()} /> : !state ? null : state.enabled ? (
        <>
          <p style={{ marginBottom: 6 }}>
            On since {when(state.enabled_at)}. Signing in takes your password and then a code from your authenticator app.
          </p>
          <p className={state.recovery_codes_left <= 3 ? "info warn" : "small mute"}>
            {state.recovery_codes_left === 0 ? "No recovery codes left" : `${state.recovery_codes_left} recovery ${state.recovery_codes_left === 1 ? "code" : "codes"} left`}
            {state.recovery_codes_left <= 3 ? ". Make new ones, so a lost phone does not lock you out." : "."}
          </p>
          {view === "idle" && (
            <div className="row" style={{ marginTop: 12 }}>
              <button type="button" className="small" onClick={() => show("new-codes")}>New recovery codes</button>
              <button type="button" className="small danger" onClick={() => show("disable")}>Switch off</button>
            </div>
          )}
          {(view === "new-codes" || view === "disable") && (
            <form onSubmit={submit(view === "disable" ? disable : renew)} style={{ marginTop: 6, maxWidth: 420 }}>
              <h3 style={{ marginTop: 14 }}>{view === "disable" ? "Switch two-factor off" : "Make new recovery codes"}</h3>
              <p className="small mute" style={{ margin: "4px 0 0" }}>
                {view === "disable"
                  ? "Signing in will need only your password again. Your other devices are signed out."
                  : "The codes you have now stop working, used or not."}
              </p>
              <TextField label="Current password" type="password" value={password} onChange={setPassword} required autoComplete="current-password" autoFocus />
              <SecondFactorField factor={factor} onChange={setFactor} />
              <FormError error={view === "disable" ? disable.error : renew.error} />
              <div className="row" style={{ marginTop: 14 }}>
                {view === "disable"
                  ? <button type="submit" className="danger primary" disabled={disable.isPending}>{disable.isPending ? "Switching off…" : "Switch off"}</button>
                  : <button type="submit" className="primary" disabled={renew.isPending}>{renew.isPending ? "Making codes…" : "Make new codes"}</button>}
                <button type="button" className="quiet" onClick={() => show("idle")}>Cancel</button>
              </div>
            </form>
          )}
        </>
      ) : (
        <>
          <p className="lede">
            Ask for a code from an authenticator app on your phone, as well as your password, every time you sign in. A
            password that was guessed, reused or stolen is then not enough to get into your account.
          </p>
          {view === "idle" && <button type="button" className="primary" onClick={() => show("password")}>Set up two-factor authentication</button>}
          {view === "password" && (
            <form onSubmit={submit(begin)} style={{ maxWidth: 420 }}>
              <TextField label="Current password" help="To confirm it is you before anything changes." type="password" value={password}
                onChange={setPassword} required autoComplete="current-password" autoFocus />
              <FormError error={begin.error} />
              <div className="row" style={{ marginTop: 14 }}>
                <button type="submit" className="primary" disabled={begin.isPending}>{begin.isPending ? "Starting…" : "Continue"}</button>
                <button type="button" className="quiet" onClick={() => show("idle")}>Cancel</button>
              </div>
            </form>
          )}
          {view === "scan" && setup && (
            <ol className="setup-steps">
              <li>
                <b>Scan this with an authenticator app.</b>
                <span className="help">Google Authenticator, Microsoft Authenticator, 1Password, Authy and others all work.</span>
                <div className="setup-scan">
                  <QrImage text={setup.otpauth_uri} label="QR code to add this account to an authenticator app" />
                  <div>
                    <p className="small" style={{ marginBottom: 6 }}>Cannot scan it? Add an account in the app by hand and type this key:</p>
                    <code className="setup-key">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
                    <div style={{ margin: "8px 0 10px" }}><CopyButton text={setup.secret} label="Copy the key" /></div>
                    <dl className="kv small">
                      <dt>Account</dt><dd>{setup.account}</dd>
                      <dt>Type</dt><dd>Time-based, {setup.digits} digits, new every {setup.period} seconds</dd>
                    </dl>
                  </div>
                </div>
              </li>
              <li>
                <b>Enter the code the app shows.</b>
                <form onSubmit={submit(confirm)} style={{ maxWidth: 420 }}>
                  <TextField label="6-digit code" value={code} onChange={setCode} required inputMode="numeric" autoComplete="one-time-code"
                    maxLength={12} placeholder="123456" className="otp" />
                  <FormError error={confirm.error} />
                  <div className="row" style={{ marginTop: 14 }}>
                    <button type="submit" className="primary" disabled={confirm.isPending || !code.trim()}>{confirm.isPending ? "Checking…" : "Switch on"}</button>
                    <button type="button" className="quiet" onClick={() => show("idle")}>Cancel</button>
                  </div>
                </form>
              </li>
            </ol>
          )}
          {view === "scan" && (
            <p className="small mute" style={{ marginTop: 12, marginBottom: 0 }}>
              The key is shown only now. Nothing changes until you enter a code, and an unfinished set-up expires
              after {Math.round((setup?.expires_in ?? 900) / 60)} minutes.
            </p>
          )}
        </>
      )}
    </section>
  );
}
