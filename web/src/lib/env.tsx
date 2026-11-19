// The workspace and environment a dashboard page is looking at, from the URL
// (/app/:slug/:mode/...), plus what this person's role lets them do there.
import { useParams } from "react-router-dom";
import type { Env } from "../api/endpoints";
import type { Mode, Role, Workspace } from "../api/types";
import { useSession } from "./session";

const RANK: Record<Role, number> = { owner: 4, admin: 3, developer: 2, analyst: 1 };

export interface EnvContext {
  slug: string;
  mode: Mode;
  env: Env;
  workspace: Workspace;
  role: Role;
  /** May change things here: developer in sandbox, admin in live (with two-factor sign-in, if the workspace asks for it). */
  canWrite: boolean;
  /** The role is enough, but live changes here also need two-factor sign-in and this person has none. */
  needsTwoFactor: boolean;
  isDeveloper: boolean;
  isAdmin: boolean;
  isOwner: boolean;
  liveActive: boolean;
  /** Path inside this environment, e.g. path("payments"). */
  path: (sub?: string) => string;
  /** react-query key prefix, so switching workspace or mode never shows stale data. */
  key: (...parts: unknown[]) => unknown[];
}

export function atLeast(role: Role | null | undefined, minimum: Role): boolean {
  return RANK[role ?? "analyst"] >= RANK[minimum] && role !== null && role !== undefined;
}

export function useEnv(): EnvContext {
  const params = useParams();
  const { me } = useSession();
  const slug = params.slug ?? "";
  const mode: Mode = params.mode === "live" ? "live" : "sandbox";
  const workspace = me?.workspaces.find((w) => w.slug === slug);
  if (!workspace) throw new Error("useEnv outside a workspace route");
  const role = (workspace.role ?? "analyst") as Role;
  const roleAllows = atLeast(role, mode === "live" ? "admin" : "developer");
  const needsTwoFactor = roleAllows && mode === "live" && workspace.require_two_factor && !me?.user.two_factor_enabled;
  return {
    slug, mode, env: { slug, mode }, workspace, role,
    canWrite: roleAllows && !needsTwoFactor,
    needsTwoFactor,
    isDeveloper: atLeast(role, "developer"),
    isAdmin: atLeast(role, "admin"),
    isOwner: role === "owner",
    liveActive: workspace.live_status === "active",
    path: (sub = "") => `/app/${slug}/${mode}${sub ? `/${sub}` : ""}`,
    key: (...parts) => ["env", slug, mode, ...parts],
  };
}
