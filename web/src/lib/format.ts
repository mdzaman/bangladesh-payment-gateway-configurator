import type { GatewayStatus, OrderStatus } from "../api/types";

/** "1250.5" or 125050 poisha -> "৳1,250.50" */
export function taka(amount: string | number | null | undefined, fromPoisha = false): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const value = fromPoisha ? Number(amount) / 100 : Number(String(amount).replace(/,/g, ""));
  if (!Number.isFinite(value)) return "—";
  return "৳" + value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Compact amount for chart axes: 1250000 poisha -> "৳12.5k" */
export function takaShort(poisha: number): string {
  const v = poisha / 100;
  if (v >= 10_000_000) return `৳${trim(v / 10_000_000)}cr`;
  if (v >= 100_000) return `৳${trim(v / 100_000)}L`;
  if (v >= 1000) return `৳${trim(v / 1000)}k`;
  return `৳${trim(v)}`;
}
const trim = (n: number) => (n >= 100 ? n.toFixed(0) : n.toFixed(1).replace(/\.0$/, ""));

export function percent(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${(rate * 100).toFixed(rate === 1 || rate === 0 ? 0 : 1)}%`;
}

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function toDate(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function when(value: string | number | null | undefined): string {
  const d = toDate(value);
  return d ? dateTime.format(d) : "—";
}

export function day(value: string | null | undefined): string {
  const d = toDate(value);
  return d ? dateOnly.format(d) : "—";
}

export function ago(value: string | number | null | undefined, now = Date.now()): string {
  const d = toDate(value);
  if (!d) return "never";
  const seconds = Math.round((d.getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return relative.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 30) return relative.format(Math.round(seconds / 86400), "day");
  return dateOnly.format(d);
}

export const STATUS_LABEL: Record<OrderStatus, string> = {
  created: "Not started",
  pending: "Pending",
  paid: "Paid",
  failed: "Failed",
  cancelled: "Cancelled",
  expired: "Expired",
  needs_review: "Needs review",
  partially_refunded: "Partly refunded",
  refunded: "Refunded",
};

export const STATUS_TONE: Record<OrderStatus, string> = {
  created: "grey",
  pending: "info",
  paid: "ok",
  failed: "bad",
  cancelled: "grey",
  expired: "grey",
  needs_review: "warn",
  partially_refunded: "warn",
  refunded: "grey",
};

export const STATUS_HELP: Partial<Record<OrderStatus, string>> = {
  needs_review:
    "The provider reported a payment whose amount, currency or order number did not match this order. Check it in the provider's panel before you ship anything.",
  pending: "The customer was sent to the provider. The result has not been confirmed yet; it is re-checked automatically.",
  created: "The order exists but the customer has not been sent to the provider yet.",
};

export const GATEWAY_STATUS: Record<GatewayStatus, [string, string]> = {
  unconfigured: ["Not set up", "grey"],
  unverified: ["Not verified yet", "warn"],
  verified: ["Working", "ok"],
  rejected: ["Credentials rejected", "bad"],
};

export const ROLE_HELP: Record<string, string> = {
  owner: "Everything, including live mode, owners and archiving the workspace.",
  admin: "Live credentials, API keys, webhooks, refunds, and the team.",
  developer: "Sandbox credentials, keys and webhooks. Read-only in live mode.",
  analyst: "Can see payments and reports. Cannot change anything.",
};

export function plural(n: number, one: string, many = one + "s"): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** Turn `code` marks in a plain string into parts a component can render. */
export function splitCode(text: string): { code: boolean; text: string }[] {
  return text.split(/(`[^`]+`)/g).filter(Boolean).map((part) =>
    part.startsWith("`") && part.endsWith("`") ? { code: true, text: part.slice(1, -1) } : { code: false, text: part });
}
