// Two-factor sign-in on the account page: set it up with an authenticator app,
// keep recovery codes, switch it off. The QR code is drawn here in the browser.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/client";
import * as api from "../../api/endpoints";
import type { TwoFactorSetup } from "../../api/types";
import { encodeQr, qrPath } from "../../lib/qr";
import { useSession } from "../../lib/session";
import { download } from "../../lib/zip";
import { CopyButton, ErrorNote, FormError, Loading, Modal, Pill, TextField, Time, useToast } from "../../ui";

/** Always black on white, whatever the theme: that is what phone cameras read best. */
export function QrImage({ text, label }: { text: string; label: string }) {
  const code = useMemo(() => {
    try {
      return encodeQr(text);
    } catch {
      return null; // too long to draw: the key can still be typed by hand
    }
  }, [text]);
  if (!code) return null;
  const side = code.size + 8;
  return (
    <svg className="qr" viewBox={`0 0 ${side} ${side}`} role="img" aria-label={label} shapeRendering="crispEdges">
      <rect width={side} height={side} fill="#fff" />
      <path d={qrPath(code)} fill="#000" />
    </svg>
  );
}

function RecoveryCodes({ codes, email, onDone }: { codes: string[]; email: string; onDone: () => void }) {
  const file = [`bdpay cloud recovery codes for ${email}`, "Each code signs you in once. Keep them somewhere safe.", "", ...codes, ""].join("\n");
  return (
    <div className="secret-once" role="status">
      <b>Save these recovery codes now</b>
      <p className="small" style={{ margin: "4px 0 0" }}>
        Each one signs you in once if you cannot use your authenticator app. They are not stored in a readable form and cannot be shown again.
      </p>
      <ul className="codes">{codes.map((code) => <li key={code}><code>{code}</code></li>)}</ul>
      <div className="row between">
        <div className="row">
          <CopyButton text={codes.join("\n")} label="Copy all" className="small" />
          <button type="button" className="small" onClick={() => download("bdpay-cloud-recovery-codes.txt", file, "text/plain")}>Download</button>
        </div>
        <button type="button" className="small primary" onClick={onDone}>I have saved them</button>
      </div>
    </div>
  );
}

const SETUP_EXPIRED = "This set-up took too long and has expired. Start it again.";

export default function TwoFactorCard() {
  const { me, refresh } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["two-factor"], queryFn: api.twoFactor.status });
  const [asking, setAsking] = useState(false);
  const [password, setPassword] = useState("");
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [dialog, setDialog] = useState<"off" | "codes" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // The server stops accepting a code for a set-up after a while. Take the QR
  // code off the screen at that moment, so nobody scans one that is already dead.
  useEffect(() => {
    if (!setup) return;
    const timer = setTimeout(() => { setSetup(null); setCode(""); setNotice(SETUP_EXPIRED); }, setup.expires_in * 1000);
    return () => clearTimeout(timer);
  }, [setup]);

  // Switching it on or off signs every other device out, so the device list changes too.
  const changed = async () => {
    await Promise.all([refresh(), status.refetch(), queryClient.invalidateQueries({ queryKey: ["sessions"] })]);
  };
  const begin = useMutation({
    mutationFn: () => api.twoFactor.setup(password),
    onSuccess: (data) => { setSetup(data); setAsking(false); setPassword(""); setCode(""); },
  });
  const enable = useMutation({
    mutationFn: () => api.twoFactor.enable(code),
    onSuccess: async (data) => { setCodes(data.recovery_codes); setSetup(null); setCode(""); await changed(); },
    onError: (error) => {
      setCode("");
      if (error instanceof ApiError && error.code === "SETUP_EXPIRED") { setSetup(null); setNotice(error.message); }
    },
  });
  const confirm = useMutation({
    mutationFn: async (): Promise<string[] | null> => {
      if (dialog === "off") {
        await api.twoFactor.disable(password, code);
        return null;
      }
      return (await api.twoFactor.newRecoveryCodes(password, code)).recovery_codes;
    },
    onSuccess: async (fresh) => {
      setCodes(fresh);
      toast(fresh ? "New recovery codes created. The earlier ones no longer work." : "Two-factor sign-in is off. Other devices were signed out.");
      closeDialog();
      await changed();
    },
  });
  const openDialog = (which: "off" | "codes") => { confirm.reset(); setPassword(""); setCode(""); setDialog(which); };
  const closeDialog = () => { setDialog(null); setPassword(""); setCode(""); };
  const cancelSetup = () => { setSetup(null); setAsking(false); setPassword(""); setCode(""); begin.reset(); enable.reset(); };

  if (!me) return null;
  const on = status.data?.enabled ?? me.user.two_factor_enabled;
  const left = status.data?.recovery_codes_left ?? 0;
  const key = setup?.secret.replace(/(.{4})/g, "$1 ").trim() ?? "";

  return (
    <section className="card" id="two-factor">
      <div className="card-head">
        <h2>Two-factor sign-in</h2>
        <Pill tone={on ? "ok" : "grey"}>{on ? "On" : "Off"}</Pill>
      </div>
      {codes && <div style={{ marginBottom: 14 }}><RecoveryCodes codes={codes} email={me.user.email} onDone={() => setCodes(null)} /></div>}
      {status.isPending ? <Loading /> : status.error ? <ErrorNote error={status.error} retry={() => status.refetch()} /> : on ? (
        <>
          <p>
            Signing in takes your password and a code from your authenticator app. Turned on <Time value={status.data.enabled_at} />.
          </p>
          <p>
            Recovery codes left: <b>{left}</b>{" "}
            {left <= 3 && <Pill tone="warn">{left === 0 ? "None left" : "Running low"}</Pill>}
          </p>
          <div className="row">
            <button type="button" onClick={() => openDialog("codes")}>New recovery codes</button>
            <button type="button" className="danger" onClick={() => openDialog("off")}>Turn off</button>
          </div>
        </>
      ) : setup ? (
        <form onSubmit={(e) => { e.preventDefault(); enable.mutate(); }}>
          <div className="twofa">
            <QrImage text={setup.otpauth_uri} label="QR code to scan with your authenticator app" />
            <div>
              <p><b>1.</b> Scan this with your authenticator app. If you cannot scan it, add an account in the app by hand and type this key:</p>
              <div className="urlrow"><code aria-label="Set-up key">{key}</code><CopyButton text={setup.secret} /></div>
              <p style={{ marginTop: 14 }}>
                <b>2.</b> Enter the 6-digit code the app shows now. This set-up waits {Math.round(setup.expires_in / 60)} minutes for it.
              </p>
              <TextField label="Code" className="otp" value={code} onChange={setCode} required inputMode="numeric" autoComplete="one-time-code"
                maxLength={12} placeholder="123456" style={{ maxWidth: 220 }} />
              <FormError error={enable.error} />
              <div className="row" style={{ marginTop: 14 }}>
                <button type="submit" className="primary" disabled={enable.isPending}>{enable.isPending ? "Checking…" : "Turn on"}</button>
                <button type="button" className="quiet" onClick={cancelSetup}>Cancel</button>
              </div>
            </div>
          </div>
        </form>
      ) : asking ? (
        <form onSubmit={(e) => { e.preventDefault(); begin.mutate(); }}>
          <TextField label="Current password" help="To make sure it is you." type="password" value={password} onChange={setPassword} required
            autoComplete="current-password" autoFocus />
          <FormError error={begin.error} />
          <div className="row" style={{ marginTop: 14 }}>
            <button type="submit" className="primary" disabled={begin.isPending}>Continue</button>
            <button type="button" className="quiet" onClick={cancelSetup}>Cancel</button>
          </div>
        </form>
      ) : (
        <>
          {notice && <p className="info warn small" role="status">{notice}</p>}
          <p className="lede">
            Ask for a code from an authenticator app on your phone as well as your password. A stolen or guessed password is then not enough to get in.
            Any app that makes 6-digit codes works: Google Authenticator, Microsoft Authenticator, Authy, 1Password and others.
          </p>
          <button type="button" className="primary" onClick={() => { begin.reset(); enable.reset(); setNotice(null); setAsking(true); }}>
            Set up two-factor sign-in
          </button>
        </>
      )}

      <Modal open={dialog !== null} onClose={closeDialog} title={dialog === "off" ? "Turn off two-factor sign-in?" : "Create new recovery codes?"} footer={
        <>
          <button type="button" className="quiet" onClick={closeDialog}>Cancel</button>
          <button type="submit" form="two-factor-confirm" className={dialog === "off" ? "danger primary" : "primary"} disabled={confirm.isPending}>
            {confirm.isPending ? "Working…" : dialog === "off" ? "Turn off" : "Create codes"}
          </button>
        </>
      }>
        <form id="two-factor-confirm" onSubmit={(e) => { e.preventDefault(); confirm.mutate(); }}>
          <p>
            {dialog === "off"
              ? "Your account will be protected by its password alone, and every other device is signed out."
              : "The recovery codes you have now stop working. You get ten new ones to save."}
          </p>
          <TextField label="Current password" type="password" value={password} onChange={setPassword} required autoComplete="current-password" />
          <TextField label="Code" help="From your authenticator app, or a recovery code you have not used." className="otp" value={code}
            onChange={setCode} required autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} maxLength={40} />
          <FormError error={confirm.error} />
        </form>
      </Modal>
    </section>
  );
}
