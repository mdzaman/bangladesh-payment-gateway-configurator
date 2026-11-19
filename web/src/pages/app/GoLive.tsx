// Activating live mode. The owner confirms, in writing, the one thing that
// keeps a service like this merchant-side software: every business uses its
// own merchant accounts and the money never passes through here.
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../../api/endpoints";
import { useEnv } from "../../lib/env";
import { when } from "../../lib/format";
import { useSession } from "../../lib/session";
import { FormError, PageHead, TextField } from "../../ui";

export default function GoLive({ embedded = false }: { embedded?: boolean }) {
  const ctx = useEnv();
  const { me, platform, refresh } = useSession();
  const navigate = useNavigate();
  const [legalName, setLegalName] = useState("");
  const [accepted, setAccepted] = useState(false);
  const activate = useMutation({
    mutationFn: () => api.workspaces.goLive(ctx.slug, legalName.trim()),
    onSuccess: async (ws) => {
      await refresh();
      if (ws.live_status === "active" && !embedded) navigate(`/app/${ctx.slug}/live/gateways`);
    },
  });
  const ws = ctx.workspace;
  const needsEmail = Boolean(platform?.require_verified_email_for_live) && !me?.user.email_verified;

  const body = () => {
    if (ws.live_status === "active") {
      return <p className="info">Live mode has been active since {when(ws.live_activated_at)}. Real payments are taken with your live gateway credentials.</p>;
    }
    if (platform?.live_mode === "disabled") {
      return <p className="info warn">Live mode is switched off on this deployment. It can be used for sandbox testing only.</p>;
    }
    if (ws.live_status === "pending") {
      return <p className="info warn">Your request is with the platform operator. Live mode opens as soon as they approve it.</p>;
    }
    if (!ctx.isOwner) {
      return <p className="info warn">Only an owner of this workspace can activate live mode. You are {ctx.role === "admin" ? "an" : "a"} {ctx.role}.</p>;
    }
    return (
      <form onSubmit={(e) => { e.preventDefault(); activate.mutate(); }}>
        <ol style={{ paddingLeft: 18, margin: "0 0 12px" }}>
          <li>Sandbox and live are separate: live has its own gateway credentials, API keys, webhooks and payments.</li>
          <li>You will enter the <b>live</b> credentials each provider issued to your business. Published sandbox values are refused.</li>
          <li>Money goes from your customer, through the provider, into your own merchant account. It never passes through this service.</li>
        </ol>
        {needsEmail && <p className="info warn">Confirm your email address first (see the notice at the top of the page).</p>}
        <TextField label="Legal name of the business" help="The name on your merchant accounts with the providers." value={legalName}
          onChange={setLegalName} required minLength={2} maxLength={120} />
        <label className="check">
          <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
          <span>{platform?.attestation}</span>
        </label>
        <FormError error={activate.error} />
        <div className="row" style={{ marginTop: 14 }}>
          <button type="submit" className="danger primary" disabled={!accepted || needsEmail || legalName.trim().length < 2 || activate.isPending}>
            {activate.isPending ? "Activating…" : platform?.live_mode === "approval" ? "Request live mode" : "Activate live mode"}
          </button>
          {!embedded && <Link className="btn quiet" to={`/app/${ctx.slug}/sandbox/overview`}>Stay in sandbox</Link>}
        </div>
      </form>
    );
  };

  if (embedded) return body();
  return (
    <div className="stack">
      <PageHead title="Live mode is not active yet">Take real payments once your sandbox tests pass.</PageHead>
      <section className="card" style={{ maxWidth: 760 }}>{body()}</section>
    </div>
  );
}
