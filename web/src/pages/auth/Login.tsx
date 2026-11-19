import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../api/endpoints";
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
  const next = safeNext(params.get("next"));
  const login = useMutation({
    mutationFn: () => api.auth.login(email.trim(), password),
    onSuccess: (data) => { signedIn(data); navigate(next, { replace: true }); },
  });
  if (me) return <Navigate to={next} replace />;
  return (
    <AuthLayout title="Sign in" footer={platform?.allow_signup !== false ? <>New here? <Link to="/signup">Create a workspace</Link></> : undefined}>
      <form onSubmit={(e) => { e.preventDefault(); login.mutate(); }}>
        <TextField label="Email" type="email" value={email} onChange={setEmail} required autoComplete="username" autoFocus />
        <TextField label="Password" type="password" value={password} onChange={setPassword} required autoComplete="current-password" />
        <FormError error={login.error} />
        <div style={{ marginTop: 16 }}><button type="submit" className="primary wide" disabled={login.isPending}>{login.isPending ? "Signing in…" : "Sign in"}</button></div>
        <p className="small" style={{ marginTop: 12, marginBottom: 0 }}><Link to="/forgot-password">Forgot your password?</Link></p>
      </form>
    </AuthLayout>
  );
}
