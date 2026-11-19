import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../../api/endpoints";
import { useEnv } from "../../lib/env";
import { useSession } from "../../lib/session";
import { ConfirmButton, CopyRow, FormError, PageHead, TextField, useToast } from "../../ui";
import GoLive from "./GoLive";

export default function Settings() {
  const ctx = useEnv();
  const { me, refresh } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const ws = ctx.workspace;
  const ownTwoFactor = me?.user.two_factor_enabled ?? false;
  const requireTwoFactor = useMutation({
    mutationFn: (on: boolean) => api.workspaces.update(ctx.slug, { require_two_factor: on }),
    onSuccess: async (data) => {
      await refresh();
      toast(data.require_two_factor ? "Live changes now need two-factor sign-in" : "Two-factor sign-in is no longer required");
    },
  });
  const [name, setName] = useState(ws.name);
  const [displayName, setDisplayName] = useState(ws.display_name);
  const [supportEmail, setSupportEmail] = useState(ws.support_email);
  const [supportPhone, setSupportPhone] = useState(ws.support_phone);
  const [locale, setLocale] = useState(ws.locale);
  const save = useMutation({
    mutationFn: () => api.workspaces.update(ctx.slug, {
      name, display_name: displayName, support_email: supportEmail, support_phone: supportPhone, locale: locale || undefined,
    }),
    onSuccess: async () => { await refresh(); toast("Workspace saved"); },
  });

  const envUsable = ctx.mode === "sandbox" || ctx.liveActive;
  const envKey = ["env", ctx.slug, ctx.mode, "settings"];
  const envSettings = useQuery({ queryKey: envKey, queryFn: () => api.envSettings.get(ctx.env), enabled: envUsable });
  const [resultUrl, setResultUrl] = useState("");
  useEffect(() => setResultUrl(envSettings.data?.result_url ?? ""), [envSettings.data]);
  const saveEnv = useMutation({
    mutationFn: () => api.envSettings.update(ctx.env, resultUrl.trim() || null),
    onSuccess: (data) => { queryClient.setQueryData(envKey, data); toast("Saved"); },
  });
  const archive = useMutation({ mutationFn: () => api.workspaces.archive(ctx.slug) });

  return (
    <div className="stack">
      <PageHead title="Settings">{ws.name}</PageHead>

      <section className="card">
        <h2>Workspace</h2>
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <div className="row2">
            <TextField label="Workspace name" help="Shown in this dashboard." value={name} onChange={setName} required disabled={!ctx.isAdmin} maxLength={80} />
            <TextField label="Name customers see" help="On checkout pages. Empty: the workspace name." value={displayName} onChange={setDisplayName} disabled={!ctx.isAdmin} maxLength={80} />
          </div>
          <div className="row2">
            <TextField label="Support email" help="Shown on checkout pages so customers can reach you." type="email" value={supportEmail} onChange={setSupportEmail} disabled={!ctx.isAdmin} />
            <TextField label="Support phone" type="tel" value={supportPhone} onChange={setSupportPhone} disabled={!ctx.isAdmin} placeholder="01XXXXXXXXX" />
          </div>
          <label className="f" htmlFor="locale">Checkout language<span className="help">Customers can switch on the page. "Automatic" follows their browser.</span></label>
          <select id="locale" value={locale} onChange={(e) => setLocale(e.target.value)} disabled={!ctx.isAdmin} style={{ maxWidth: 260 }}>
            <option value="">Automatic</option><option value="en">English</option><option value="bn">বাংলা</option>
          </select>
          <FormError error={save.error} />
          {ctx.isAdmin && <div style={{ marginTop: 16 }}><button type="submit" className="primary" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save workspace"}</button></div>}
        </form>
      </section>

      {envUsable && (
        <section className="card">
          <h2>{ctx.mode === "live" ? "Live" : "Sandbox"} environment</h2>
          <form onSubmit={(e) => { e.preventDefault(); saveEnv.mutate(); }}>
            <TextField label="After-payment page" type="url" value={resultUrl} onChange={setResultUrl} disabled={!ctx.canWrite}
              placeholder="https://shop.example/thanks?order={order_id}&status={status}"
              help="Where customers land after paying when an order sets no return_url of its own. {order_id} and {status} are filled in. Empty: our own result page." />
            <FormError error={saveEnv.error} />
            {ctx.canWrite && <div style={{ marginTop: 14 }}><button type="submit" className="primary" disabled={saveEnv.isPending}>Save</button></div>}
          </form>
          {envSettings.data && (
            <>
              <h4 style={{ marginTop: 18 }}>Environment key</h4>
              <p className="help">Part of the callback and IPN URLs given to providers. It identifies this environment; it is not a secret.</p>
              <CopyRow value={envSettings.data.environment_key} />
            </>
          )}
        </section>
      )}

      <section className="card">
        <h2>Sign-in security</h2>
        <label className="check">
          <input type="checkbox" checked={ws.require_two_factor} onChange={(e) => requireTwoFactor.mutate(e.target.checked)}
            disabled={!ctx.isOwner || requireTwoFactor.isPending || !ownTwoFactor} />
          <span>
            Require two-factor sign-in for changes in live mode
            <span className="help">
              Members without it can still look at live mode, but cannot change gateways, API keys or webhooks, or refund payments,
              until they turn it on for their own account. Sandbox is not affected.
            </span>
          </span>
        </label>
        {!ctx.isOwner ? <p className="small mute" style={{ marginBottom: 0 }}>Only an owner can change this.</p>
          : !ownTwoFactor && (
            <p className="small" style={{ marginBottom: 0 }}>
              To change this, turn it on for <Link to="/account#two-factor">your own account</Link> first.
            </p>
          )}
        <FormError error={requireTwoFactor.error} />
      </section>

      <section className="card" id="live">
        <h2>Live mode</h2>
        <div style={{ marginTop: 10 }}><GoLive embedded /></div>
      </section>

      {ctx.isOwner && (
        <section className="card">
          <h2>Archive this workspace</h2>
          <p className="lede">Its API keys stop working at once and it disappears from every member's dashboard. Payment records are kept, not erased.</p>
          <ConfirmButton label="Archive workspace" title={`Archive ${ws.name}?`} confirmLabel="Archive" danger className="danger"
            body={<p>This cannot be undone from the dashboard. Payments already at a provider are still recorded when they finish.</p>}
            onConfirm={async () => { await archive.mutateAsync(); await refresh(); navigate("/app"); }} />
        </section>
      )}
    </div>
  );
}
