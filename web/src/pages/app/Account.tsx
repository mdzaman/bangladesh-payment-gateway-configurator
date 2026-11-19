import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../../api/endpoints";
import { useSession } from "../../lib/session";
import { Brand } from "../../layout/Shell";
import { ErrorNote, FormError, Loading, PageHead, Pill, TextField, Time, useToast } from "../../ui";
import TwoFactor from "./TwoFactor";

export default function Account() {
  const { me, refresh } = useSession();
  const toast = useToast();
  const [name, setName] = useState(me?.user.name ?? "");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.auth.sessions });
  const profile = useMutation({ mutationFn: () => api.auth.updateProfile(name.trim()), onSuccess: async () => { await refresh(); toast("Profile saved"); } });
  const password = useMutation({
    mutationFn: () => api.auth.changePassword(current, next),
    onSuccess: () => { setCurrent(""); setNext(""); sessions.refetch(); toast("Password changed. Other devices were signed out."); },
  });
  const revoke = useMutation({ mutationFn: api.auth.revokeOtherSessions, onSuccess: () => { sessions.refetch(); toast("Signed out everywhere else"); } });
  const resend = useMutation({ mutationFn: api.auth.resendVerification, onSuccess: () => toast("Confirmation email sent") });
  if (!me) return null;
  return (
    <div className="band">
      <div className="wrap stack" style={{ maxWidth: 760 }}>
        <div className="row between"><Brand to="/app" /><Link className="btn small quiet" to="/app">Back to the dashboard</Link></div>
        <PageHead title="Your account">{me.user.email}</PageHead>

        <section className="card">
          <h2>Profile</h2>
          <form onSubmit={(e) => { e.preventDefault(); profile.mutate(); }}>
            <TextField label="Name" value={name} onChange={setName} required maxLength={80} autoComplete="name" />
            <p style={{ marginTop: 12 }}>
              Email: <b>{me.user.email}</b>{" "}
              {me.user.email_verified ? <Pill tone="ok">Confirmed</Pill> : (
                <><Pill tone="warn">Not confirmed</Pill>{" "}
                  <button type="button" className="bare" disabled={resend.isPending} onClick={() => resend.mutate()}>Send the confirmation email again</button></>
              )}
            </p>
            <FormError error={profile.error ?? resend.error} />
            <button type="submit" className="primary" disabled={profile.isPending}>Save profile</button>
          </form>
        </section>

        <section className="card">
          <h2>Password</h2>
          <form onSubmit={(e) => { e.preventDefault(); password.mutate(); }}>
            <TextField label="Current password" type="password" value={current} onChange={setCurrent} required autoComplete="current-password" />
            <TextField label="New password" help="At least 10 characters. Length matters more than symbols." type="password" value={next}
              onChange={setNext} required minLength={10} autoComplete="new-password" />
            <FormError error={password.error} />
            <div style={{ marginTop: 14 }}><button type="submit" className="primary" disabled={password.isPending}>Change password</button></div>
          </form>
        </section>

        <TwoFactor onSessionsChanged={() => sessions.refetch()} />

        <section className="card">
          <div className="card-head">
            <h2>Signed-in devices</h2>
            <button type="button" className="small quiet" disabled={revoke.isPending} onClick={() => revoke.mutate()}>Sign out everywhere else</button>
          </div>
          {sessions.isPending ? <Loading /> : sessions.error ? <ErrorNote error={sessions.error} /> : (
            <table>
              <thead><tr><th>Device</th><th>Address</th><th>Last active</th></tr></thead>
              <tbody>
                {sessions.data.data.map((s, i) => (
                  <tr key={i}>
                    <td className="small">{s.user_agent || "Unknown"} {s.current && <Pill tone="ok">This device</Pill>}</td>
                    <td className="small"><code>{s.ip ?? "—"}</code></td>
                    <td className="small"><Time value={s.last_seen_at} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
