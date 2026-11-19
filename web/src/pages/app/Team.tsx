import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../../api/endpoints";
import type { Role } from "../../api/types";
import { useEnv } from "../../lib/env";
import { ROLE_HELP } from "../../lib/format";
import { useSession } from "../../lib/session";
import { ConfirmButton, ErrorNote, FormError, Loading, PageHead, TextField, Time, useToast } from "../../ui";

const ROLES: Role[] = ["owner", "admin", "developer", "analyst"];

export default function Team() {
  const ctx = useEnv();
  const { refresh } = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const key = ["workspace", ctx.slug, "members"];
  const query = useQuery({ queryKey: key, queryFn: () => api.workspaces.members(ctx.slug) });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("developer");
  const reload = () => queryClient.invalidateQueries({ queryKey: key });
  const invite = useMutation({
    mutationFn: () => api.workspaces.invite(ctx.slug, email.trim(), role),
    onSuccess: (inv) => { reload(); setEmail(""); toast(`Invitation sent to ${inv.email}`); },
  });
  const change = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: Role }) => api.workspaces.changeRole(ctx.slug, userId, role),
    onSuccess: reload,
  });
  const remove = useMutation({
    mutationFn: (userId: string) => api.workspaces.removeMember(ctx.slug, userId),
    onSuccess: reload,
  });
  const revoke = useMutation({ mutationFn: (id: string) => api.workspaces.revokeInvitation(ctx.slug, id), onSuccess: reload });

  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorNote error={query.error} />;
  const { members, invitations } = query.data;
  const assignable = ROLES.filter((r) => r !== "owner" || ctx.isOwner);

  return (
    <div className="stack">
      <PageHead title="Team">People who can open {ctx.workspace.name}, and what each may do.</PageHead>

      <section className="card flush">
        <div className="scroll">
          <table className="cards">
            <thead><tr><th>Person</th><th>Role</th><th>Joined</th><th /></tr></thead>
            <tbody>
              {members.map((m) => {
                const locked = !ctx.isAdmin || (m.role === "owner" && !ctx.isOwner);
                return (
                  <tr key={m.user_id}>
                    <td data-l="Person"><b>{m.name || m.email}</b>{m.you && <span className="mute"> (you)</span>}<div className="small mute">{m.email}</div></td>
                    <td data-l="Role">
                      {locked ? m.role : (
                        <select value={m.role} style={{ width: "auto" }} aria-label={`Role of ${m.email}`} disabled={change.isPending}
                          onChange={(e) => change.mutate({ userId: m.user_id, role: e.target.value as Role })}>
                          {assignable.map((r) => <option key={r} value={r}>{r}</option>)}
                        </select>
                      )}
                    </td>
                    <td data-l="Joined" className="small"><Time value={m.joined_at} /></td>
                    <td className="actions">
                      {m.you ? (
                        <ConfirmButton label="Leave" title={`Leave ${ctx.workspace.name}?`} confirmLabel="Leave workspace" danger className="small quiet danger"
                          body={<p>You lose access at once. Someone on the team has to invite you again to return.</p>}
                          onConfirm={async () => { await remove.mutateAsync(m.user_id); await refresh(); navigate("/app"); }} />
                      ) : !locked && (
                        <ConfirmButton label="Remove" title={`Remove ${m.name || m.email}?`} confirmLabel="Remove" danger className="small quiet danger"
                          body={<p>They lose access to this workspace at once. API keys they created keep working until revoked.</p>}
                          onConfirm={() => remove.mutateAsync(m.user_id)} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <FormError error={change.error ?? remove.error} />

      {ctx.isAdmin && (
        <section className="card">
          <h3>Invite someone</h3>
          <form className="row" style={{ alignItems: "flex-end", marginTop: 4 }} onSubmit={(e) => { e.preventDefault(); invite.mutate(); }}>
            <div className="grow" style={{ minWidth: 220 }}>
              <TextField label="Email" type="email" value={email} onChange={setEmail} required placeholder="colleague@example.com" />
            </div>
            <div>
              <label className="f" htmlFor="invite-role">Role</label>
              <select id="invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)} style={{ width: "auto" }}>
                {assignable.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <button type="submit" className="primary" disabled={invite.isPending}>{invite.isPending ? "Sending…" : "Send invitation"}</button>
          </form>
          <FormError error={invite.error} />
          {invitations.length > 0 && (
            <table style={{ marginTop: 16 }}>
              <thead><tr><th>Waiting for</th><th>Role</th><th>Expires</th><th /></tr></thead>
              <tbody>
                {invitations.map((i) => (
                  <tr key={i.id}>
                    <td>{i.email}</td><td>{i.role}</td><td className="small"><Time value={i.expires_at} /></td>
                    <td className="actions"><button type="button" className="small quiet" disabled={revoke.isPending} onClick={() => revoke.mutate(i.id)}>Cancel invitation</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      <section className="card">
        <h3>What each role can do</h3>
        <dl className="kv" style={{ marginTop: 10 }}>
          {ROLES.map((r) => <div key={r} style={{ display: "contents" }}><dt><b>{r}</b></dt><dd>{ROLE_HELP[r]}</dd></div>)}
        </dl>
      </section>
    </div>
  );
}
