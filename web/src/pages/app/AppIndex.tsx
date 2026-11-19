// /app: send the person to the workspace they used last, or let them make one.
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../api/endpoints";
import { useSession } from "../../lib/session";
import { Brand } from "../../layout/Shell";
import { FormError, TextField } from "../../ui";

export default function AppIndex() {
  const { me, refresh, signOut } = useSession();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.workspaces.create(name.trim()),
    onSuccess: async (ws) => { await refresh(); navigate(`/app/${ws.slug}/sandbox/overview`); },
  });
  if (!me) return null;
  const wantNew = params.get("new") === "1";
  if (me.workspaces.length > 0 && !wantNew) {
    let last = "";
    try {
      last = localStorage.getItem("bdpay.last") ?? "";
    } catch { /* ignore */ }
    const [slug, mode] = last.split("/");
    const ws = me.workspaces.find((w) => w.slug === slug) ?? me.workspaces[0]!;
    const usable = mode === "live" && ws.live_status === "active" ? "live" : "sandbox";
    return <Navigate to={`/app/${ws.slug}/${usable}/overview`} replace />;
  }
  return (
    <div className="authbox">
      <div style={{ marginBottom: 20 }}><Brand to="/app" /></div>
      <div className="card">
        <h1 style={{ fontSize: 26 }}>{me.workspaces.length ? "New workspace" : "Create your workspace"}</h1>
        <p className="lede">One workspace per business. It gets its own gateways, API keys, payments and team.</p>
        <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <TextField label="Business name" value={name} onChange={setName} required minLength={2} maxLength={80} placeholder="Rahim Store" autoFocus />
          <FormError error={create.error} />
          <div className="row" style={{ marginTop: 16 }}>
            <button type="submit" className="primary" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create workspace"}</button>
            {me.workspaces.length > 0 ? <Link className="btn quiet" to="/app">Cancel</Link>
              : <button type="button" className="quiet" onClick={async () => { await signOut(); navigate("/login"); }}>Sign out</button>}
          </div>
        </form>
      </div>
    </div>
  );
}
