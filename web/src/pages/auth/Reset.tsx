import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import * as api from "../../api/endpoints";
import AuthLayout from "../../layout/AuthLayout";
import { FormError, TextField } from "../../ui";

export default function Reset() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const reset = useMutation({ mutationFn: () => api.auth.resetPassword(token, password) });
  return (
    <AuthLayout title="Choose a new password" footer={<Link to="/login">Back to sign in</Link>}>
      {!token ? <p className="info bad">This link is incomplete. Open the link from the email again, or ask for a new one.</p>
        : reset.isSuccess ? (
          <>
            <p className="info">Your password is changed and every device was signed out.</p>
            <p style={{ marginTop: 14 }}><Link className="btn primary" to="/login">Sign in</Link></p>
          </>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); reset.mutate(); }}>
            <TextField label="New password" help="At least 10 characters." type="password" value={password} onChange={setPassword}
              required minLength={10} autoComplete="new-password" autoFocus />
            <FormError error={reset.error} />
            <div style={{ marginTop: 16 }}><button type="submit" className="primary wide" disabled={reset.isPending}>Change password</button></div>
          </form>
        )}
    </AuthLayout>
  );
}
