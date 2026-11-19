// Platform operators only: every workspace on this deployment.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import * as api from "../../api/endpoints";
import { useSession } from "../../lib/session";
import { Brand } from "../../layout/Shell";
import { ConfirmButton, Empty, ErrorNote, FormError, Loading, PageHead, Pill, Time } from "../../ui";

export default function Admin() {
  const { me } = useSession();
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const query = useQuery({ queryKey: ["admin", "workspaces", search, status], queryFn: () => api.admin.workspaces(search, status), enabled: Boolean(me?.user.is_platform_admin) });
  const act = useMutation({
    mutationFn: ({ id, action, reason }: { id: string; action: "suspend" | "unsuspend" | "approve-live" | "revoke-live"; reason?: string }) =>
      api.admin.act(id, action, reason),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin", "workspaces"] }),
  });
  if (!me) return null;
  if (!me.user.is_platform_admin) return <Navigate to="/app" replace />;
  return (
    <div className="band">
      <div className="wrap stack">
        <div className="row between"><Brand to="/app" /><Link className="btn small quiet" to="/app">Back to the dashboard</Link></div>
        <PageHead title="All workspaces">Platform operator view. You can see who is here and stop a workspace, never its credentials.</PageHead>
        <form className="filters" onSubmit={(e) => { e.preventDefault(); setSearch(q.trim()); }}>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or address" style={{ maxWidth: 320 }} aria-label="Search workspaces" />
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "auto" }} aria-label="Live status">
            <option value="">Any live status</option><option value="pending">Waiting for approval</option><option value="active">Live</option><option value="inactive">Sandbox only</option>
          </select>
          <button type="submit" className="quiet">Search</button>
        </form>
        <FormError error={act.error} />
        <section className="card flush">
          {query.isPending ? <Loading /> : query.error ? <ErrorNote error={query.error} /> : query.data.data.length === 0 ? <Empty title="No workspaces match" /> : (
            <div className="scroll">
              <table className="cards">
                <thead><tr><th>Workspace</th><th>Live</th><th>Legal name</th><th className="right">Members</th><th>Created</th><th /></tr></thead>
                <tbody>
                  {query.data.data.map((w) => (
                    <tr key={w.id}>
                      <td data-l="Workspace"><b>{w.name}</b> {w.suspended && <Pill tone="bad">Suspended</Pill>} {w.archived && <Pill tone="grey">Archived</Pill>}
                        <div className="small mute"><code>{w.slug}</code>{w.suspended_reason ? ` · ${w.suspended_reason}` : ""}</div></td>
                      <td data-l="Live"><Pill tone={w.live_status === "active" ? "live" : w.live_status === "pending" ? "warn" : "grey"}>{w.live_status}</Pill></td>
                      <td data-l="Legal name" className="small">{w.attestation ? <>{w.attestation.legal_name}<div className="mute">{w.attestation.email}</div></> : <span className="mute">—</span>}</td>
                      <td data-l="Members" className="right num">{w.members}</td>
                      <td data-l="Created" className="small"><Time value={w.created_at} /></td>
                      <td className="actions">
                        {w.live_status === "pending" && <button type="button" className="small primary" disabled={act.isPending} onClick={() => act.mutate({ id: w.id, action: "approve-live" })}>Approve live</button>}{" "}
                        {w.live_status === "active" && (
                          <ConfirmButton label="Revoke live" title={`Revoke live mode for ${w.name}?`} confirmLabel="Revoke" danger
                            body={<p>Its live API keys stop working. Payments in progress are still recorded.</p>}
                            onConfirm={() => act.mutateAsync({ id: w.id, action: "revoke-live" })} />
                        )}{" "}
                        {w.suspended
                          ? <button type="button" className="small quiet" disabled={act.isPending} onClick={() => act.mutate({ id: w.id, action: "unsuspend" })}>Lift suspension</button>
                          : (
                            <ConfirmButton label="Suspend" title={`Suspend ${w.name}?`} confirmLabel="Suspend" danger className="small quiet danger"
                              body={<p>No new payments can start in either mode. Payments already at a provider are still recorded.</p>}
                              onConfirm={() => act.mutateAsync({ id: w.id, action: "suspend", reason: "suspended by the platform operator" })} />
                          )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
