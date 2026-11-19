// Every dashboard endpoint, typed. Pages call these (through react-query),
// never fetch directly.
import { del, get, patch, post, put } from "./client";
import type {
  AdminWorkspace, ApiKey, AuditEntry, Catalog, CheckoutOption, CheckoutSession, ChecklistItem, Delivery, EnvSettings,
  EventPayload, GatewayListing, GatewayState, Invitation, InvitationPreview, Me, Member, Mode, Order, OrderDetail,
  Overview, Paged, PaymentLink, Platform, RefundResponse, Role, SessionInfo, TestResult, User, WebhookEndpoint,
  Workspace,
} from "./types";

export interface Env {
  slug: string;
  mode: Mode;
}

const ws = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}`;
const env = (e: Env) => `${ws(e.slug)}/${e.mode}`;
const id = encodeURIComponent;

// ------------------------------------------------------------------- public
export const platform = () => get<Platform>("/api/config");
export const catalog = () => get<Catalog>("/api/catalog");

// ------------------------------------------------------------------ account
export const auth = {
  me: () => get<Me>("/api/auth/me"),
  signup: (body: { email: string; password: string; name: string; workspace_name?: string; invite_token?: string }) =>
    post<Me>("/api/auth/signup", body),
  login: (email: string, password: string) => post<Me>("/api/auth/login", { email, password }),
  logout: () => post<{ ok: true }>("/api/auth/logout"),
  verifyEmail: (token: string) => post<{ ok: true; email: string }>("/api/auth/verify-email", { token }),
  resendVerification: () => post<{ ok: true }>("/api/auth/resend-verification"),
  forgotPassword: (email: string) => post<{ ok: true }>("/api/auth/forgot-password", { email }),
  resetPassword: (token: string, password: string) => post<{ ok: true }>("/api/auth/reset-password", { token, password }),
  changePassword: (current_password: string, new_password: string) =>
    post<{ ok: true }>("/api/auth/change-password", { current_password, new_password }),
  updateProfile: (name: string) => patch<User>("/api/auth/profile", { name }),
  sessions: () => get<{ data: SessionInfo[] }>("/api/auth/sessions"),
  revokeOtherSessions: () => post<{ ok: true }>("/api/auth/sessions/revoke-others"),
  invitation: (token: string) => get<InvitationPreview>("/api/auth/invitation", { token }),
  acceptInvitation: (token: string) => post<Workspace>("/api/auth/accept-invitation", { token }),
};

// --------------------------------------------------------------- workspaces
export const workspaces = {
  list: () => get<{ data: Workspace[] }>("/api/workspaces"),
  create: (name: string) => post<Workspace>("/api/workspaces", { name }),
  get: (slug: string) => get<Workspace>(ws(slug)),
  update: (slug: string, body: Partial<Pick<Workspace, "name" | "display_name" | "support_email" | "support_phone" | "locale">>) =>
    patch<Workspace>(ws(slug), body),
  archive: (slug: string) => del<{ ok: true }>(ws(slug)),
  goLive: (slug: string, legal_name: string) => post<Workspace>(`${ws(slug)}/go-live`, { accept: true, legal_name }),
  members: (slug: string) => get<{ members: Member[]; invitations: Invitation[] }>(`${ws(slug)}/members`),
  invite: (slug: string, email: string, role: Role) => post<Invitation>(`${ws(slug)}/invitations`, { email, role }),
  revokeInvitation: (slug: string, invitationId: string) => del<{ ok: true }>(`${ws(slug)}/invitations/${id(invitationId)}`),
  changeRole: (slug: string, userId: string, role: Role) =>
    patch<{ user_id: string; role: Role }>(`${ws(slug)}/members/${id(userId)}`, { role }),
  removeMember: (slug: string, userId: string) => del<{ ok: true }>(`${ws(slug)}/members/${id(userId)}`),
  auditLog: (slug: string, before?: number) =>
    get<{ data: AuditEntry[]; has_more: boolean; next_cursor: number | null }>(`${ws(slug)}/audit-log`, { before, limit: 50 }),
};

// ---------------------------------------------------------------- overview
export const overview = (e: Env, days = 30) => get<Overview>(`${env(e)}/overview`, { days });
export const worker = (e: Env) => get<{ running: boolean; seconds_since_heartbeat: number | null }>(`${env(e)}/worker`);
export const envSettings = {
  get: (e: Env) => get<EnvSettings>(`${env(e)}/settings`),
  update: (e: Env, result_url: string | null) => patch<EnvSettings>(`${env(e)}/settings`, { result_url }),
};

// ----------------------------------------------------------------- gateways
export const gateways = {
  list: (e: Env) => get<GatewayListing>(`${env(e)}/gateways`),
  /** Blank secret fields keep what is stored; `clear` removes optional secrets. */
  save: (e: Env, gateway: string, values: Record<string, string | boolean>, enabled?: boolean, clear: string[] = []) =>
    put<GatewayState>(`${env(e)}/gateways/${id(gateway)}`, { values, enabled, clear }),
  setEnabled: (e: Env, gateway: string, enabled: boolean) =>
    patch<GatewayState>(`${env(e)}/gateways/${id(gateway)}`, { enabled }),
  remove: (e: Env, gateway: string) => del<{ ok: true }>(`${env(e)}/gateways/${id(gateway)}`),
  test: (e: Env, gateway: string) => post<TestResult>(`${env(e)}/gateways/${id(gateway)}/test`),
  reorder: (e: Env, order: string[]) => post<{ gateways: GatewayState[] }>(`${env(e)}/gateways-order`, { order }),
  checklist: (e: Env) => get<{ items: ChecklistItem[] }>(`${env(e)}/checklist`),
  confirm: (e: Env, itemId: string, done: boolean) =>
    post<{ items: ChecklistItem[] }>(`${env(e)}/checklist`, { id: itemId, done }),
};

// ------------------------------------------------------------------- orders
export interface OrderFilters {
  status?: string;
  gateway?: string;
  q?: string;
  source?: string;
  created_from?: string;
  created_to?: string;
  cursor?: string;
  limit?: number;
}

export const orders = {
  list: (e: Env, filters: OrderFilters = {}) => get<Paged<Order>>(`${env(e)}/orders`, { ...filters }),
  get: (e: Env, orderId: string) => get<OrderDetail>(`${env(e)}/orders/${id(orderId)}`),
  /** Dashboard test payment: creates the order and returns the provider URL. */
  start: (e: Env, body: { gateway: string; amount: string; order_id?: string; customer?: Record<string, string> }) =>
    post<{ order: Order; redirect_url: string }>(`${env(e)}/orders`, body),
  requery: (e: Env, orderId: string) => post<Order>(`${env(e)}/orders/${id(orderId)}/requery`),
  refund: (e: Env, orderId: string, amount: string | null, reason: string) =>
    post<RefundResponse>(`${env(e)}/orders/${id(orderId)}/refund`, { amount, reason }),
};

// --------------------------------------------------------------- developers
export const apiKeys = {
  list: (e: Env) => get<{ data: ApiKey[]; scopes: string[] }>(`${env(e)}/api-keys`),
  create: (e: Env, body: { name: string; scopes?: string[] | null; expires_in_days?: number | null }) =>
    post<ApiKey & { key: string }>(`${env(e)}/api-keys`, body),
  revoke: (e: Env, keyId: string) => del<ApiKey>(`${env(e)}/api-keys/${id(keyId)}`),
};

export const webhooks = {
  list: (e: Env) => get<{ data: WebhookEndpoint[]; event_types: string[] }>(`${env(e)}/webhooks`),
  create: (e: Env, body: { url: string; description?: string; event_types?: string[] | null }) =>
    post<WebhookEndpoint & { secret: string }>(`${env(e)}/webhooks`, body),
  update: (e: Env, endpointId: string, body: { url?: string; description?: string; event_types?: string[]; enabled?: boolean }) =>
    patch<WebhookEndpoint>(`${env(e)}/webhooks/${id(endpointId)}`, body),
  remove: (e: Env, endpointId: string) => del<{ ok: true }>(`${env(e)}/webhooks/${id(endpointId)}`),
  rotateSecret: (e: Env, endpointId: string) =>
    post<WebhookEndpoint & { secret: string }>(`${env(e)}/webhooks/${id(endpointId)}/rotate-secret`),
  test: (e: Env, endpointId: string) => post<{ event_id: string }>(`${env(e)}/webhooks/${id(endpointId)}/test`),
  deliveries: (e: Env, filters: { endpoint_id?: string; status?: string; event_id?: string } = {}) =>
    get<{ data: Delivery[] }>(`${env(e)}/webhook-deliveries`, { ...filters, limit: 50 }),
  redeliver: (e: Env, deliveryId: string) =>
    post<{ delivery_id: string }>(`${env(e)}/webhook-deliveries/${id(deliveryId)}/redeliver`),
  events: (e: Env, type?: string) => get<{ data: EventPayload[] }>(`${env(e)}/events`, { type, limit: 50 }),
};

// ----------------------------------------------------------------- checkout
export interface NewLink {
  title: string;
  description?: string;
  amount?: string | null;
  min_amount?: string | null;
  max_amount?: string | null;
  gateways?: string[] | null;
  collect?: string[];
  success_url?: string | null;
  max_payments?: number | null;
}

export interface NewSession {
  amount: string;
  reference?: string | null;
  description?: string;
  gateways?: string[] | null;
  success_url?: string | null;
  cancel_url?: string | null;
  expires_in_minutes?: number | null;
}

export const checkout = {
  options: (e: Env) => get<{ data: CheckoutOption[] }>(`${env(e)}/checkout-options`),
  sessions: (e: Env, status?: string) => get<{ data: CheckoutSession[] }>(`${env(e)}/checkout-sessions`, { status }),
  createSession: (e: Env, body: NewSession) => post<CheckoutSession>(`${env(e)}/checkout-sessions`, body),
  links: (e: Env) => get<{ data: PaymentLink[] }>(`${env(e)}/payment-links`),
  createLink: (e: Env, body: NewLink) => post<PaymentLink>(`${env(e)}/payment-links`, body),
  setLinkActive: (e: Env, linkId: string, active: boolean) =>
    patch<PaymentLink>(`${env(e)}/payment-links/${id(linkId)}`, { active }),
};

// -------------------------------------------------------------------- admin
export const admin = {
  workspaces: (q = "", live_status = "") => get<{ data: AdminWorkspace[] }>("/api/admin/workspaces", { q, live_status }),
  act: (workspaceId: string, action: "suspend" | "unsuspend" | "approve-live" | "revoke-live", reason = "") =>
    post<Workspace>(`/api/admin/workspaces/${id(workspaceId)}/${action}${reason ? `?reason=${id(reason)}` : ""}`),
};
