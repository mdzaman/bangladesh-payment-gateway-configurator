// The dashboard frame: workspace switcher and navigation on the left, the
// sandbox/live switch on top. Live mode is always unmistakable (red, like the
// "LIVE MODE: real money" banner of the reference console).
import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import * as api from "../api/endpoints";
import type { Mode } from "../api/types";
import { atLeast } from "../lib/env";
import { useSession } from "../lib/session";
import { Loading, useToast } from "../ui";
import GoLive from "../pages/app/GoLive";

/** Pages that work on the workspace itself, so they open even before live mode is active. */
const WORKSPACE_PAGES = ["team", "settings", "audit"];

export function Brand({ to = "/" }: { to?: string }) {
  return (
    <Link to={to} className="brand">
      <span className="brand-mark" aria-hidden="true" />
      bdpay cloud
    </Link>
  );
}

export default function Shell() {
  const { slug = "", mode: rawMode } = useParams();
  const { me, signOut, refresh } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const mode: Mode = rawMode === "live" ? "live" : "sandbox";
  const workspace = me?.workspaces.find((w) => w.slug === slug);
  const page = location.pathname.split("/")[4] ?? "overview";
  const liveBlocked = mode === "live" && workspace?.live_status !== "active";

  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    try {
      if (workspace) localStorage.setItem("bdpay.last", `${slug}/${mode}`);
    } catch { /* private mode: nothing to remember */ }
  }, [slug, mode, workspace]);

  const overview = useQuery({
    queryKey: ["env", slug, mode, "overview", 30],
    queryFn: () => api.overview({ slug, mode }, 30),
    enabled: Boolean(workspace) && !liveBlocked,
    refetchInterval: 60_000,
  });

  if (!me) return null;
  if (!workspace || (rawMode !== "sandbox" && rawMode !== "live")) return <Navigate to="/app" replace />;

  const base = `/app/${slug}/${mode}`;
  const stats = overview.data?.stats;
  const attention = (stats?.needs_review_now ?? 0);
  const switchMode = (next: Mode) => navigate(`/app/${slug}/${next}/${WORKSPACE_PAGES.includes(page) ? page : page.split("/")[0]}`);
  const item = (to: string, label: string, badge?: number) => (
    <NavLink to={`${base}/${to}`}>
      {label}
      {badge ? <span className="count" title={`${badge} need attention`}>{badge}</span> : null}
    </NavLink>
  );

  return (
    <div className="shell">
      <aside className={`side${open ? " open" : ""}`} aria-label="Workspace">
        <Brand to={`${base}/overview`} />
        <label>
          <span className="sr-only">Workspace</span>
          <select value={slug} onChange={(e) => {
            if (e.target.value === "__new") navigate("/app?new=1");
            else navigate(`/app/${e.target.value}/${mode === "live" && me.workspaces.find((w) => w.slug === e.target.value)?.live_status !== "active" ? "sandbox" : mode}/overview`);
          }}>
            {me.workspaces.map((w) => <option key={w.id} value={w.slug}>{w.name}</option>)}
            <option value="__new">+ New workspace…</option>
          </select>
        </label>
        <nav className="nav" aria-label="Sections">
          {item("overview", "Overview")}
          {item("gateways", "Gateways")}
          {item("payments", "Payments", attention)}
          {item("links", "Checkout & links")}
          {atLeast(workspace.role, "developer") && item("developers", "Developers")}
          <span className="group">Workspace</span>
          {item("team", "Team")}
          {item("settings", "Settings")}
          {atLeast(workspace.role, "admin") && item("audit", "Audit log")}
          {me.user.is_platform_admin && <><span className="group">Platform</span><NavLink to="/admin">All workspaces</NavLink></>}
        </nav>
        <div className="side-foot">
          <span title={me.user.email} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{me.user.name || me.user.email}</span>
          <span className="small" style={{ opacity: 0.8 }}>{workspace.role}</span>
          <div className="row">
            <Link to="/account" className="btn small" style={{ background: "none", borderColor: "rgba(255,255,255,.35)" }}>Account</Link>
            <button type="button" className="small" onClick={async () => { await signOut(); navigate("/login"); }}>Sign out</button>
          </div>
        </div>
      </aside>

      <div className="main">
        <div className={`modebar ${mode}`} role="status">
          {mode === "live" ? "LIVE MODE: real money" : "Sandbox mode: no real money moves"}
        </div>
        <header className="topbar">
          <div className="row">
            <button type="button" className="small quiet menu-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>Menu</button>
            <b>{workspace.name}</b>
            {workspace.suspended && <span className="pill bad">Suspended</span>}
          </div>
          <div className="seg" role="group" aria-label="Environment">
            <button type="button" aria-pressed={mode === "sandbox"} onClick={() => switchMode("sandbox")}>Sandbox</button>
            <button type="button" className="live" aria-pressed={mode === "live"} onClick={() => switchMode("live")}>Live</button>
          </div>
        </header>
        <main className="content">
          {workspace.suspended && (
            <p className="banner bad" role="alert">
              This workspace is suspended{workspace.suspended_reason ? `: ${workspace.suspended_reason}` : ""}. New payments are
              refused; payments already in progress are still recorded. Contact the platform operator.
            </p>
          )}
          {!me.user.email_verified && (
            <p className="banner warn" style={{ marginBottom: 16 }}>
              Confirm your email address ({me.user.email}) to be able to activate live mode.{" "}
              <button type="button" className="bare" onClick={async () => {
                try {
                  await api.auth.resendVerification();
                  toast("Confirmation email sent");
                } catch (e) {
                  toast(e instanceof Error ? e.message : "Could not send the email", "bad");
                }
              }}>Send it again</button>{" "}
              <button type="button" className="bare" onClick={() => refresh()}>I have confirmed</button>
            </p>
          )}
          {mode === "live" && !liveBlocked && workspace.require_two_factor && !me.user.two_factor_enabled && atLeast(workspace.role, "admin") && (
            <p className="banner warn" style={{ marginBottom: 16 }}>
              This workspace requires two-factor sign-in for changes in live mode. You can look, but not change anything here, until
              you <Link to="/account#two-factor">turn it on for your account</Link>.
            </p>
          )}
          <Suspense fallback={<Loading />}>
            {liveBlocked && !WORKSPACE_PAGES.includes(page) ? <GoLive /> : <Outlet />}
          </Suspense>
        </main>
      </div>
    </div>
  );
}
