// One fetch wrapper for the dashboard API. The session is an HttpOnly cookie the
// browser sends by itself; writes also carry the CSRF token the server gave us.

export interface FieldError {
  field: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  errors: FieldError[];
  requestId?: string;

  constructor(status: number, code: string, message: string, errors: FieldError[] = [], requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.errors = errors;
    this.requestId = requestId;
  }

  /** Message for one form field, if the server blamed it. */
  field(name: string): string | undefined {
    return this.errors.find((e) => e.field === name)?.message;
  }
}

let csrfToken = "";
let onUnauthenticated: (() => void) | null = null;

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export function setUnauthenticatedHandler(handler: (() => void) | null): void {
  onUnauthenticated = handler;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

export async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  let response: Response;
  try {
    response = await fetch(withQuery(path, query), {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "NETWORK", "Could not reach the server. Check your connection and try again.");
  }
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const d = (typeof detail === "object" && detail !== null ? detail : {}) as {
      code?: string; message?: string; errors?: FieldError[]; request_id?: string;
    };
    const message = d.message ?? (typeof detail === "string" ? detail : `Request failed (${response.status})`);
    // A refused sign-in (either step) is not a session that ended.
    if (response.status === 401 && !path.startsWith("/api/auth/login") && path !== "/api/auth/me") onUnauthenticated?.();
    throw new ApiError(response.status, d.code ?? "ERROR", message, d.errors ?? [], d.request_id);
  }
  return data as T;
}

export const get = <T>(path: string, query?: Query) => request<T>("GET", path, undefined, query);
export const post = <T>(path: string, body: unknown = {}) => request<T>("POST", path, body);
export const put = <T>(path: string, body: unknown = {}) => request<T>("PUT", path, body);
export const patch = <T>(path: string, body: unknown = {}) => request<T>("PATCH", path, body);
export const del = <T>(path: string) => request<T>("DELETE", path);

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}
