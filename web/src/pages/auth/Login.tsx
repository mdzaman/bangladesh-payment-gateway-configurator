import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as api from "../../api/endpoints";
import type { Me } from "../../api/types";
import AuthLayout from "../../layout/AuthLayout";
import { useSession } from "../../lib/session";
import { FormError, TextField } from "../../ui";

/** Only ever follow a path on this site, never a full URL someone put in ?next=. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/app";
}

export default function Login() {
  const { me, signedIn, platform } = useSession();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [password, setPassword] = useState("");
  // Two-factor sign-in: set once the password is accepted. There is no session until the code is too.
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [notice, setNotice] = useState("");
  const next = safeNext(params.get("next"));
  const enter = (data: Me) => { signedIn(data); navigate(next, { replace: true }); };
  const login = useMutation({
    mutationFn: () => api.auth.login(email.trim(), password),
    onSuccess: (data) => {
      if (!("two_factor_required" in data)) return enter(data);
      verify.reset();
      setChallenge(data.challenge); setCode(""); setRecovery(false); setNotice("");
    },
  });
  const startOver = (message = "") => { setChallenge(""); setPassword(""); setCode(""); setNotice(message); };
  const verify = useMutation({
    mutationFn: () => api.auth.loginTwoFactor(challenge, code),
    onSuccess: enter,
    onError: (error) => {
      setCode("");
      // Ten minutes passed, or this account was signed out everywhere meanwhile: back to the password.
      if (error instanceof ApiError && error.code === "CHALLENGE_EXPIRED") startOver(error.message);
    },
  });
  if (me) return <Navigate to={next} replace />;
  if (challenge) {
    return (
      <AuthLayout title="Two-factor sign-in"
        lede={recovery ? "Enter one of the recovery codes you saved when you set this up. Each one works once." : "Enter the 6-digit code from your authenticator app."}
        footer={<button type="button" className="bare" onClick={() => startOver()}>Sign in with a different account</button>}>
        <form onSubmit={(e) => { e.preventDefault(); verify.mutate(); }}>
          {recovery ? (
            <TextField key="recovery" label="Recovery code" className="otp" value={code} onChange={setCode} required autoFocus
              autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={40} placeholder="xxxx-xxxx-xxxx-xxxx" />
          ) : (
            <TextField key="app" label="Code" className="otp" value={code} onChange={setCode} required autoFocus
              inputMode="numeric" autoComplete="one-time-code" maxLength={12} placeholder="123456" />
          )}
          <FormError error={verify.error} />
          <div style={{ marginTop: 16 }}><button type="submit" className="primary wide" disabled={verify.isPending}>{verify.isPending ? "Checking…" : "Sign in"}</button></div>
          <p className="small" style={{ marginTop: 12, marginBottom: 0 }}>
            <button type="button" className="bare" onClick={() => { setRecovery(!recovery); setCode(""); verify.reset(); }}>
              {recovery ? "Use a code from my authenticator app" : "I cannot use my app: use a recovery code"}
            </button>
          </p>
        </form>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout title="Sign in" footer={platform?.allow_signup !== false ? <>New here? <Link to="/signup">Create a workspace</Link></> : undefined}>
      <form onSubmit={(e) => { e.preventDefault(); setNotice(""); login.mutate(); }}>
        {notice && <p className="info warn" role="status">{notice}</p>}
        <TextField label="Email" type="email" value={email} onChange={setEmail} required autoComplete="username" autoFocus />
        <TextField label="Password" type="password" value={password} onChange={setPassword} required autoComplete="current-password" />
        <FormError error={login.error} />
        <div style={{ marginTop: 16 }}><button type="submit" className="primary wide" disabled={login.isPending}>{login.isPending ? "Signing in…" : "Sign in"}</button></div>
        <p className="small" style={{ marginTop: 12, marginBottom: 0 }}><Link to="/forgot-password">Forgot your password?</Link></p>
      </form>
    </AuthLayout>
  );
}
