import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import * as api from "../../api/endpoints";
import AuthLayout from "../../layout/AuthLayout";
import { useSession } from "../../lib/session";
import { FormError, TextField } from "../../ui";

export default function Signup() {
  const { me, signedIn, platform } = useSession();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const invite = params.get("invite") ?? "";
  const [name, setName] = useState("");
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [workspace, setWorkspace] = useState("");
  const signup = useMutation({
    mutationFn: () => api.auth.signup({
      email: email.trim(), password, name: name.trim(), workspace_name: invite ? undefined : workspace.trim() || undefined,
      invite_token: invite || undefined,
    }),
    onSuccess: (data) => { signedIn(data); navigate("/app", { replace: true }); },
  });
  if (me) return <Navigate to="/app" replace />;
  if (platform && !platform.allow_signup && !invite) {
    return (
      <AuthLayout title="Sign-up is by invitation" footer={<>Already have an account? <Link to="/login">Sign in</Link></>}>
        <p>This deployment does not accept open sign-ups. Ask an owner of the workspace you want to join to invite you by email.</p>
      </AuthLayout>
    );
  }
  const fieldError = (pattern: RegExp) => (signup.error instanceof ApiError && pattern.test(signup.error.message) ? signup.error.message : undefined);
  return (
    <AuthLayout title={invite ? "Create your account" : "Create a workspace"}
      lede={invite ? "You were invited to a workspace. Set a password to join it." : "Start in the sandbox: a built-in test gateway is ready, so you can take a test payment in a minute."}
      footer={<>Already have an account? <Link to="/login">Sign in</Link></>}>
      <form onSubmit={(e) => { e.preventDefault(); signup.mutate(); }}>
        <TextField label="Your name" value={name} onChange={setName} required maxLength={80} autoComplete="name" autoFocus />
        <TextField label="Email" type="email" value={email} onChange={setEmail} required autoComplete="username" readOnly={Boolean(invite && params.get("email"))}
          error={fieldError(/email|account/i)} />
        <TextField label="Password" help="At least 10 characters. A few random words work well." type="password" value={password}
          onChange={setPassword} required minLength={10} autoComplete="new-password" error={fieldError(/password|characters|guess/i)} />
        {!invite && <TextField label="Business name" help="The name of your first workspace. You can change it later." value={workspace} onChange={setWorkspace} maxLength={80} placeholder="Rahim Store" />}
        {signup.error && !fieldError(/email|account|password|characters|guess/i) ? <FormError error={signup.error} /> : null}
        <div style={{ marginTop: 16 }}><button type="submit" className="primary wide" disabled={signup.isPending}>{signup.isPending ? "Creating…" : invite ? "Join workspace" : "Create workspace"}</button></div>
      </form>
    </AuthLayout>
  );
}
