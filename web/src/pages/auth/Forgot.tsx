import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../../api/endpoints";
import AuthLayout from "../../layout/AuthLayout";
import { FormError, TextField } from "../../ui";

export default function Forgot() {
  const [email, setEmail] = useState("");
  const send = useMutation({ mutationFn: () => api.auth.forgotPassword(email.trim()) });
  return (
    <AuthLayout title="Reset your password" footer={<Link to="/login">Back to sign in</Link>}>
      {send.isSuccess ? (
        <p className="info">If an account exists for <b>{email}</b>, a link to choose a new password is on its way. It works for one hour.</p>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
          <TextField label="Email" type="email" value={email} onChange={setEmail} required autoComplete="username" autoFocus />
          <FormError error={send.error} />
          <div style={{ marginTop: 16 }}><button type="submit" className="primary wide" disabled={send.isPending}>Email me a reset link</button></div>
        </form>
      )}
    </AuthLayout>
  );
}
