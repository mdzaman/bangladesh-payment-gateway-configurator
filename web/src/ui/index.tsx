// The small set of building blocks every page uses. Plain elements and CSS
// classes from styles/app.css; no component library.
import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState,
  type ReactNode,
} from "react";
import { ApiError, errorMessage } from "../api/client";
import type { GatewayStatus, OrderStatus, SecondFactor } from "../api/types";
import { GATEWAY_STATUS, STATUS_LABEL, STATUS_TONE, ago, splitCode, when } from "../lib/format";

// -------------------------------------------------------------------- text
export function Pill({ tone = "grey", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return <span className={`pill ${tone}`} title={title}>{children}</span>;
}

export function StatusPill({ status }: { status: OrderStatus }) {
  return <Pill tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Pill>;
}

export function GatewayStatusPill({ status, enabled }: { status: GatewayStatus; enabled?: boolean }) {
  if (enabled === false && status !== "unconfigured") return <Pill tone="grey">Switched off</Pill>;
  const [label, tone] = GATEWAY_STATUS[status];
  return <Pill tone={tone}>{label}</Pill>;
}

/** Renders `code` marks inside ordinary text. */
export function Rich({ text }: { text: string }) {
  return <>{splitCode(text).map((part, i) => (part.code ? <code key={i}>{part.text}</code> : <span key={i}>{part.text}</span>))}</>;
}

export function Time({ value }: { value: string | number | null | undefined }) {
  if (value === null || value === undefined) return <span className="mute">never</span>;
  return <time title={when(value)}>{ago(value)}</time>;
}

// ------------------------------------------------------------------ states
export function Loading({ label = "Loading" }: { label?: string }) {
  return <div className="loading" role="status"><span className="spinner" aria-hidden="true" />{label}…</div>;
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <div className="info bad" role="alert">
      <p><b>{errorMessage(error)}</b></p>
      {requestId && <p className="small mute">Request id <code>{requestId}</code></p>}
      {retry && <button type="button" className="small" onClick={retry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children}</div>;
}

/** Field errors the server sent back for a form (ApiError.errors), or one message. */
export function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  const fields = error instanceof ApiError ? error.errors.filter((e) => e.message) : [];
  if (fields.length > 1) {
    return <ul className="errors" role="alert">{fields.map((e, i) => <li key={i}>{e.message}</li>)}</ul>;
  }
  return <p className="field-error" role="alert">{fields[0]?.message ?? errorMessage(error)}</p>;
}

// ------------------------------------------------------------------- forms
export function Field({ label, help, error, required, children, htmlFor }: {
  label: ReactNode; help?: ReactNode; error?: string; required?: boolean; children: ReactNode; htmlFor?: string;
}) {
  return (
    <div>
      <label className="f" htmlFor={htmlFor}>
        {label}{required && <em> *</em>}
        {help && <span className="help">{help}</span>}
      </label>
      {children}
      {error && <span className="field-error" role="alert">{error}</span>}
    </div>
  );
}

export function TextField({ label, help, error, required, value, onChange, type = "text", ...rest }: {
  label: ReactNode; help?: ReactNode; error?: string; required?: boolean; value: string;
  onChange: (value: string) => void; type?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type">) {
  const id = useId();
  return (
    <Field label={label} help={help} error={error} required={required} htmlFor={id}>
      <input id={id} type={type} value={value} required={required} aria-invalid={error ? true : undefined}
        onChange={(e) => onChange(e.target.value)} {...rest} />
    </Field>
  );
}

/** What someone is typing as their second factor: the app's code, or a recovery code instead. */
export interface FactorInput {
  kind: "code" | "recovery_code";
  value: string;
}

export const NO_FACTOR: FactorInput = { kind: "code", value: "" };

export function asSecondFactor(factor: FactorInput): SecondFactor {
  return factor.kind === "code" ? { code: factor.value.trim() } : { recovery_code: factor.value.trim() };
}

/** The second step of proving who you are, with the way out for a lost or dead phone. */
export function SecondFactorField({ factor, onChange, autoFocus }: {
  factor: FactorInput; onChange: (factor: FactorInput) => void; autoFocus?: boolean;
}) {
  const recovery = factor.kind === "recovery_code";
  const type = (value: string) => onChange({ ...factor, value });
  return (
    <>
      {recovery ? (
        <TextField key="recovery" label="Recovery code" help="One of the codes you saved when you switched two-factor on. Each works once."
          value={factor.value} onChange={type} required autoComplete="off" autoCapitalize="none" spellCheck={false}
          placeholder="xxxxx-xxxxx" className="otp" autoFocus={autoFocus} />
      ) : (
        <TextField key="code" label="6-digit code" help="From your authenticator app. It changes every 30 seconds."
          value={factor.value} onChange={type} required inputMode="numeric" autoComplete="one-time-code" maxLength={12}
          placeholder="123456" className="otp" autoFocus={autoFocus} />
      )}
      <p className="small" style={{ margin: "6px 0 0" }}>
        <button type="button" className="bare" onClick={() => onChange({ kind: recovery ? "code" : "recovery_code", value: "" })}>
          {recovery ? "Use the authenticator app instead" : "Cannot use your phone? Enter a recovery code"}
        </button>
      </p>
    </>
  );
}

// -------------------------------------------------------------------- copy
export function CopyButton({ text, label = "Copy", className = "small quiet" }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  const toast = useToast();
  return (
    <button type="button" className={className} onClick={async () => {
      try {
        await navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1600);
      } catch {
        toast("Copy was blocked: select the text and copy it by hand", "bad");
      }
    }}>{done ? "Copied" : label}</button>
  );
}

export function CodeBlock({ code, copy = true }: { code: string; copy?: boolean }) {
  return (
    <div className="codebox">
      <pre className="code" tabIndex={0}>{code}</pre>
      {copy && <CopyButton text={code} className="small" />}
    </div>
  );
}

export function CopyRow({ value, label }: { value: string; label?: string }) {
  return (
    <div className="urlrow">
      <code aria-label={label}>{value}</code>
      <CopyButton text={value} />
    </div>
  );
}

/** A secret the server shows exactly once (API key, webhook signing secret). */
export function SecretOnce({ title, value, children }: { title: string; value: string; children?: ReactNode }) {
  return (
    <div className="secret-once" role="status">
      <b>{title}</b>
      <code>{value}</code>
      <div className="row between">
        <span className="small">{children ?? "Copy it now. It is not stored in a readable form and cannot be shown again."}</span>
        <CopyButton text={value} className="small primary" />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- modal
export function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  if (!open) return null;
  return (
    <dialog ref={ref} className={`modal${wide ? " wide" : ""}`} aria-label={title} onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      <div className="modal-head">
        <h3>{title}</h3>
        <button type="button" className="small quiet" onClick={onClose} aria-label="Close">Close</button>
      </div>
      <div className="modal-body">{children}</div>
      {footer && <div className="modal-foot">{footer}</div>}
    </dialog>
  );
}

/** A button that asks before it acts. */
export function ConfirmButton({ label, title, body, confirmLabel, onConfirm, danger, className = "small quiet", disabled }: {
  label: ReactNode; title: string; body: ReactNode; confirmLabel: string; onConfirm: () => Promise<unknown> | void;
  danger?: boolean; className?: string; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <>
      <button type="button" className={className} disabled={disabled} onClick={() => { setError(null); setOpen(true); }}>{label}</button>
      <Modal open={open} onClose={() => setOpen(false)} title={title} footer={
        <>
          <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>
          <button type="button" className={danger ? "danger primary" : "primary"} disabled={busy} onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await onConfirm();
              setOpen(false);
            } catch (e) {
              setError(e);
            } finally {
              setBusy(false);
            }
          }}>{busy ? "Working…" : confirmLabel}</button>
        </>
      }>
        {body}
        <FormError error={error} />
      </Modal>
    </>
  );
}

// ------------------------------------------------------------------ toasts
type ToastFn = (message: string, tone?: "ok" | "bad") => void;
const ToastContext = createContext<ToastFn>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; message: string; tone: string }[]>([]);
  const next = useRef(1);
  const push = useCallback<ToastFn>((message, tone = "ok") => {
    const id = next.current++;
    setItems((list) => [...list.slice(-2), { id, message, tone }]);
    setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), tone === "bad" ? 5000 : 2600);
  }, []);
  const value = useMemo(() => push, [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.message}</div>)}
      </div>
    </ToastContext.Provider>
  );
}

export function PageHead({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · bdpay cloud`;
  }, [title]);
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

/** Explains why a control is disabled for this person's role. */
export function ReadOnlyNote({ show, live }: { show: boolean; live: boolean }) {
  if (!show) return null;
  return (
    <p className="info warn small">
      You can look but not change things here. {live ? "Changes in live mode need the admin or owner role." : "Changes need the developer role or higher."}
    </p>
  );
}
