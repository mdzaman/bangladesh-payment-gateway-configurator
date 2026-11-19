// Shapes returned by the service. Kept by hand next to the Python serialisers
// (services/*.py `*_json`, api/account.py); money is always a decimal string
// such as "1250.00" plus integer poisha.

export type Mode = "sandbox" | "live";
export type Role = "owner" | "admin" | "developer" | "analyst";
export type OrderStatus =
  | "created" | "pending" | "paid" | "failed" | "cancelled" | "expired" | "needs_review" | "partially_refunded" | "refunded";

export interface User {
  id: string;
  email: string;
  name: string;
  email_verified: boolean;
  two_factor_enabled: boolean;
  is_platform_admin: boolean;
  created_at: string;
}

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  role: Role | null;
  display_name: string;
  support_email: string;
  support_phone: string;
  locale: string;
  /** Members need two-factor sign-in before they can change anything in live mode. */
  require_two_factor: boolean;
  live_status: "inactive" | "pending" | "active";
  live_activated_at: string | null;
  suspended: boolean;
  suspended_reason: string | null;
  created_at: string;
}

export interface Me {
  user: User;
  csrf_token: string;
  workspaces: Workspace[];
}

/** The password was right; a code is still owed before there is a session. */
export interface TwoFactorChallenge {
  two_factor_required: true;
  challenge: string;
  expires_in: number;
}

export interface TwoFactorStatus {
  enabled: boolean;
  enabled_at: string | null;
  recovery_codes_left: number;
}

export interface TwoFactorSetup {
  /** Base32, for typing into an authenticator app by hand. */
  secret: string;
  /** The same thing as an otpauth:// link, for the QR code. */
  otpauth_uri: string;
  /** Seconds this set-up waits for its first code before it has to be started again. */
  expires_in: number;
}

export interface Platform {
  brand_name: string;
  version: string;
  public_url: string;
  allow_signup: boolean;
  live_mode: "self_serve" | "approval" | "disabled";
  require_verified_email_for_live: boolean;
  simulator: boolean;
  allow_experimental: boolean;
  egress_ips: string[];
  docs_enabled: boolean;
  support_email: string;
  attestation: string;
  is_production: boolean;
}

// ------------------------------------------------------------------ catalog
export interface FieldDef {
  k: string;
  l: string;
  t: "text" | "secret" | "textarea" | "select" | "check";
  req?: number;
  demo?: string;
  help?: string;
  cloudHelp?: string;
  val?: string;
  cloudVal?: string;
  opts?: [string, string][];
  file?: string;
  env?: string;
  url?: boolean;
}

export interface GatewayDef {
  id: string;
  name: string;
  kind: string;
  st: "stable" | "beta" | "exp";
  refund: string;
  verify: string;
  docs: string;
  get: string;
  sandbox: string;
  test: string;
  need: string[];
  demoNote?: string;
  ipn?: boolean;
  whitelist?: boolean;
  cloudOnly?: boolean;
  sandboxOnly?: boolean;
  checkout: { label: string; hint: string; label_bn: string; hint_bn: string };
  f: FieldDef[];
}

export interface ViaDef {
  id: string;
  name: string;
  via: string;
  noApi?: boolean;
}

export interface Catalog {
  gateways: GatewayDef[];
  via: ViaDef[];
}

// ----------------------------------------------------------------- gateways
export type GatewayStatus = "unconfigured" | "unverified" | "verified" | "rejected";

export interface TestResult {
  result: "verified" | "rejected" | "reachable" | "unreachable";
  message: string;
  checked_at: string;
}

export interface GatewayState {
  id: string;
  configured: boolean;
  enabled: boolean;
  status: GatewayStatus;
  values: Record<string, string>;
  secrets: Record<string, { set: boolean; hint: string }>;
  position: number;
  refunds: "none" | "full" | "partial";
  urls: { callback: string; ipn: string | null };
  last_test_at: string | null;
  last_test: TestResult | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
  updated_at: string | null;
}

export interface GatewayListing {
  gateways: GatewayState[];
  egress_ips: string[];
  callback_domain: string;
}

export interface ChecklistItem {
  id: string;
  label: string;
  state: "done" | "todo" | "manual";
  detail: string;
  gateway: string | null;
  manual: boolean;
}

export interface EnvSettings {
  result_url: string | null;
  environment_key: string;
}

// ------------------------------------------------------------------- orders
export interface Order {
  order_id: string;
  gateway: string;
  amount: string;
  currency: string;
  status: OrderStatus;
  provider_payment_id: string | null;
  provider_txn_id: string | null;
  refunded: string;
  mode: Mode;
  amount_poisha: number;
  refunded_poisha: number;
  customer: Record<string, string>;
  metadata: Record<string, string>;
  source: string;
  checkout_session_id: string | null;
  return_url: string | null;
  created_at: string;
  updated_at: string;
  paid_at: string | null;
}

export interface OrderEvent {
  at: number; // unix seconds
  kind: string;
  from_status: string | null;
  to_status: string | null;
  detail: Record<string, unknown>;
}

export interface OrderDetail extends Order {
  events: OrderEvent[];
  refund_capability: "none" | "full" | "partial";
  webhook_events: { id: string; type: string; created_at: string }[];
}

export interface Paged<T> {
  data: T[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface RefundResponse {
  ok: boolean;
  refund_ref: string | null;
  status: string;
  order: Order;
}

export interface DailyStat {
  day: string;
  paid: number;
  paid_poisha: number;
  unpaid: number;
  open: number;
  paid_amount: string;
}

export interface Stats {
  days: number;
  orders: number;
  paid_count: number;
  paid_amount: string;
  paid_poisha: number;
  refunded_amount: string;
  success_rate: number | null;
  by_status: Record<OrderStatus, number>;
  by_gateway: {
    gateway: string; orders: number; paid: number; paid_poisha: number; unpaid: number; paid_amount: string;
    success_rate: number | null;
  }[];
  daily: DailyStat[];
  pending_now: number;
  needs_review_now: number;
}

export interface Overview {
  stats: Stats;
  gateways: { id: string; enabled: boolean; status: GatewayStatus; last_error: string | null; last_success_at: string | null }[];
  checklist: ChecklistItem[];
  usable: boolean;
}

// --------------------------------------------------------------- developers
export interface ApiKey {
  id: string;
  name: string;
  display: string;
  mode: Mode;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface WebhookEndpoint {
  id: string;
  url: string;
  description: string;
  event_types: string[];
  enabled: boolean;
  consecutive_failures: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  created_at: string;
}

export interface Delivery {
  id: string;
  event_id: string;
  event_type: string | null;
  endpoint_id: string;
  status: "pending" | "succeeded" | "failed";
  attempts: number;
  next_attempt_at: string | null;
  last_attempt_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  last_response: string | null;
  duration_ms: number | null;
  created_at: string;
  delivered_at: string | null;
}

export interface EventPayload {
  id: string;
  object: "event";
  type: string;
  mode: Mode;
  created_at: string;
  data: Record<string, unknown>;
}

// ----------------------------------------------------------------- checkout
export interface CheckoutSession {
  id: string;
  mode: Mode;
  url: string;
  status: "open" | "paid" | "expired" | "cancelled";
  amount: string;
  amount_poisha: number;
  currency: string;
  reference: string | null;
  description: string;
  customer: Record<string, string>;
  metadata: Record<string, string>;
  gateways: string[];
  success_url: string | null;
  cancel_url: string | null;
  paid_order_id: string | null;
  payment_link_id: string | null;
  expires_at: string;
  created_at: string;
  completed_at: string | null;
}

export interface PaymentLink {
  id: string;
  mode: Mode;
  url: string;
  title: string;
  description: string;
  amount: string | null;
  amount_poisha: number | null;
  min_amount: string | null;
  max_amount: string | null;
  gateways: string[];
  collect: string[];
  success_url: string | null;
  active: boolean;
  max_payments: number | null;
  paid_count: number;
  created_at: string;
}

export interface CheckoutOption {
  id: string;
  label: string;
  hint: string;
  label_bn: string;
  hint_bn: string;
}

// --------------------------------------------------------------------- team
export interface Member {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  joined_at: string;
  you: boolean;
  /** null when this person's role does not let them see it. */
  two_factor: boolean | null;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  at: string;
  mode: Mode | null;
  actor_type: "user" | "api_key" | "system";
  actor: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip: string | null;
  detail: Record<string, unknown>;
}

export interface SessionInfo {
  current: boolean;
  created_at: string;
  last_seen_at: string;
  ip: string | null;
  user_agent: string | null;
}

export interface InvitationPreview {
  workspace: string;
  email: string;
  role: Role;
  inviter: string;
  has_account: boolean;
}

export interface AdminWorkspace extends Workspace {
  members: number;
  archived: boolean;
  attestation: { legal_name: string; email: string; at: string; ip: string } | null;
}
