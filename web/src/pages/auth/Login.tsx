import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as api from "../../api/endpoints";
import type { Me } from "../../api/types";
import AuthLayout from "../../layout/AuthLayout";
import { useSession } from "../../lib/session";
import { FormError, NO_FACTOR, SecondFactorField, TextField, asSecondFactor, type FactorInput } from "../../ui";

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
  // Set once the password is accepted for an account with two-factor on: the second step is owed.
  const [challenge, setChallenge] = useState<string | null>(null);
  const [factor, setFactor] = useState<FactorInput>(NO_FACTOR);
  const [notice, setNotice] = useState("");
  const next = safeNext(params.get("next"));
  const enter = (data: Me) => { signedIn(data); navigate(next, { replace: true }); };
  const login = useMutation({
    mutationFn: () => api.auth.login(email.trim(), password),
    onSuccess: (data) => {
      if (!("two_factor_required" in data)) return enter(data);
      setChallenge(data.challenge);
      setPassword(""); // not needed again, so not kept
      setFactor(NO_FACTOR);
      setNotice("");
    },
  });
  const verify = useMutation({
    mutationFn: () => api.auth.loginTwoFactor(challenge ?? "", asSecondFactor(factor)),
    onSuccess: enter,
    onError: (error) => {
      // The few minutes for the second step ran out: only the password step can start it again.
      if (error instanceof ApiError && error.code === "CHALLENGE_EXPIRED") startAgain(error.message);
    },
  });
  const startAgain = (why = "") => {
    setChallenge(null);
    setFactor(NO_FACTOR);
    setNotice(why);
    login.reset();
    verify.reset();
  };
  if (me) return <Navigate to={next} replace />;
  if (challenge) {
    return (
      <AuthLayout title="Two-factor authentication" lede={<>Your password is right. One more step to sign in as <b>{email.trim()}</b>.</>}
        footer={<button type="button" className="bare" onClick={() => startAgain()}>Sign in with a different account</button>}>
        <form onSubmit={(e) => { e.preventDefault(); verify.mutate(); }}>
          <SecondFactorField factor={factor} onChange={(value) => { setFactor(value); verify.reset(); }} autoFocus />
          <FormError error={verify.error} />
          <div style={{ marginTop: 16 }}>
            <button type="submit" className="primary wide" disabled={verify.isPending || !factor.value.trim()}>{verify.isPending ? "Checking…" : "Sign in"}</button>
          </div>
        </form>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout title="Sign in" footer={platform?.allow_signup !== false ? <>New here? <Link to="/signup">Create a workspace</Link></> : undefined}>
      <form onSubmit={(e) => { e.preventDefault(); login.mutate(); }}>
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
