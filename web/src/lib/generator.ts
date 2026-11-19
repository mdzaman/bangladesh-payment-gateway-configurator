// The self-host generator: turns a gateway selection and the credentials typed
// into the page into a complete, runnable bdpay project. Everything here runs
// in the browser. Nothing typed into the generator is sent anywhere.
//
// Ported from the original bdpay configurator. The files come from the
// reference engine in core/ (bundled at build time), so a generated project is
// the audited reference, cut down to the gateways that were ticked.
import type { FieldDef, GatewayDef } from "../api/types";

export interface GeneratorInput {
  gateways: GatewayDef[];
  env: "sandbox" | "live";
  publicUrl: string;
  pkg: string;
  owner: string;
  resultUrl: string;
  secret: string;
  admin: string;
  /** Values typed into the credential forms, keyed by field key. */
  vals: Record<string, string>;
  today?: string;
}

export type Project = Record<string, string>;

const CORE = [
  "bdpay/__init__.py", "bdpay/core.py", "bdpay/config.py", "bdpay/http.py", "bdpay/store.py", "bdpay/security.py",
  "bdpay/registry.py", "bdpay/service.py", "bdpay/web.py", "bdpay/__main__.py", "bdpay/drivers/__init__.py",
];
const IPN_GATEWAYS = ["sslcommerz", "portpos", "paystation", "uddoktapay"];
const STABILITY_TEXT: Record<GatewayDef["st"], string> = { stable: "Stable", beta: "Beta: check pack", exp: "Experimental" };

export function randomHex(bytes: number): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function countFiles(list: GatewayDef[]): number {
  return 26 + list.length * 2 + (list.some((g) => g.id === "bkash") ? 1 : 0) + (list.some((g) => g.id === "nagad") ? 2 : 0);
}

/** The value a field contributes: what was typed, else its default. */
function fieldValue(f: FieldDef, vals: Record<string, string>): string {
  return String(vals[f.k] ?? f.val ?? "").trim();
}

/**
 * One `KEY=value` line that both readers of `.env` agree on: the reference
 * loader (strips one pair of matching quotes, no escapes) and Docker Compose's
 * `env_file` (single quotes are literal). Plain when possible, single-quoted
 * when the value has characters a shell-style parser would trip over.
 */
export function dotenvLine(key: string, value: string | undefined | null): string {
  const v = String(value ?? "");
  if (!/[\s#"'$`\\]/.test(v)) return `${key}=${v}`;
  if (!v.includes("'")) return `${key}='${v}'`;
  if (!isAwkward(v)) return `${key}="${v}"`;
  // See isAwkward: Compose's double-quoted form, flagged by `warnings`.
  return `${key}="${v.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/**
 * A value holding a single quote together with `"`, `$`, a backtick or a
 * backslash cannot be written to `.env` so that every reader agrees on it.
 */
export function isAwkward(value: string): boolean {
  return value.includes("'") && /["$`\\]/.test(value);
}

/** Things worth telling the person that do not stop the build. */
export function warnings(input: GeneratorInput): string[] {
  const out: string[] = [];
  for (const g of input.gateways) {
    for (const f of g.f) {
      if (!f.file && isAwkward(input.vals[f.k] ?? "")) {
        out.push(`${g.name}: ${f.l} mixes quote characters that .env files cannot carry reliably. `
          + `After downloading, set ${f.k} as a real environment variable instead.`);
      }
    }
  }
  return out;
}

export function validate(input: GeneratorInput): string[] {
  const errs: string[] = [];
  const url = input.publicUrl.trim();
  const { gateways: list, vals } = input;
  if (!list.length) errs.push("Choose at least one gateway");
  if (!/^https?:\/\/[^\s]+$/.test(url)) errs.push("Public URL must start with http:// or https://");
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,60}$/.test(input.pkg.trim())) {
    errs.push("Project name: letters, digits, dot, dash or underscore");
  }
  if (input.secret.length < 32) errs.push("Callback signing secret must be at least 32 characters");
  for (const g of list) {
    for (const f of g.f) {
      if (f.req && !(vals[f.k] || "").trim()) {
        errs.push(`${g.name}: ${f.t === "check" ? "tick the compliance box" : f.l + " is required"}`);
      }
    }
  }
  if (input.env === "live") {
    if (!url.startsWith("https://")) errs.push("Live mode needs an https:// public URL");
    if (/localhost|127\.0\.0\.1/.test(url)) errs.push("Live mode cannot use localhost");
    for (const g of list) {
      for (const f of g.f) {
        if (f.demo && vals[f.k] === f.demo) {
          errs.push(`${g.name}: ${f.l} is a sandbox demo value; live mode needs your live credentials`);
        }
      }
    }
  }
  const key = vals.NAGAD_PRIVATE_KEY || "";
  if (list.some((g) => g.id === "nagad") && key && !/PRIVATE KEY|^[A-Za-z0-9+/=\s]+$/.test(key)) {
    errs.push("Nagad private key does not look like a PEM or base64 key");
  }
  return errs;
}

/** Go-live steps for a selection. `code` is marked with backticks. */
export function golive(list: GatewayDef[]): string[] {
  const has = (...ids: string[]) => list.some((g) => ids.includes(g.id));
  const steps = [
    "Live credentials stored only in `.env` or your secret manager; `BDPAY_ENV=live`; new `BDPAY_SECRET`.",
    "`BDPAY_PUBLIC_URL` is your https domain and reachable from the internet.",
  ];
  if (has("bkash")) steps.push("bKash: live app key/secret and username/password from bKash; run one real payment and a refund of the minimum amount.");
  if (has("nagad")) steps.push("Nagad: live keys in `keys/`; server public IPv4 and callback domain whitelisted by Nagad.");
  if (has("sslcommerz")) steps.push("SSLCOMMERZ: IPN URL set in My Store → IPN Settings; allow inbound traffic from SSLCOMMERZ on ports 80/443.");
  if (has("upay")) steps.push("Upay: production IPv4 whitelisted; base URL from the live pack.");
  if (has("eps", "portpos", "paystation", "upay")) {
    steps.push("Beta drivers: compare every field with the provider pack and run the full sandbox test list.");
  }
  steps.push(
    "`python -m bdpay doctor` shows all ✓.",
    "Reconcile job running; watch for `needs_review` orders for the first week.",
    "Test console off in live (default) and `BDPAY_ADMIN_TOKEN` set.",
  );
  return steps;
}

export function readme(input: GeneratorInput): string {
  const { gateways: list, pkg } = input;
  const u = input.publicUrl.trim().replace(/\/$/, "");
  const rows = list.map((g) => `| ${g.name} | ${STABILITY_TEXT[g.st]} | ${g.refund} | ${g.verify} | ${g.test} |`).join("\n");
  const urls = list
    .map((g) => `- **${g.name}**: callbacks \`${u}/bdpay/callback/${g.id}/...\` (signed per order, automatic)`
      + (IPN_GATEWAYS.includes(g.id) ? `; IPN \`${u}/bdpay/ipn/${g.id}\`` : ""))
    .join("\n");
  return `# ${pkg}

Bangladesh payment service generated by the bdpay configurator for: ${list.map((g) => g.name).join(", ")}.
Environment: **${input.env}**. Not affiliated with any payment provider.

## Quick start

\`\`\`bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q          # offline tests against mock gateways
python -m bdpay doctor       # settings, keys, reachability, Dhaka time
uvicorn app:app --reload     # http://localhost:8000 test console
\`\`\`

Docker: \`docker compose up -d --build\` (service + reconcile worker).

## Gateways

| Gateway | Status | Refund by API | Verification | Sandbox test data |
|---|---|---|---|---|
${rows}

Beta drivers follow public and community references. Check every field against the provider's onboarding pack before going live; base URLs can be overridden with \`<GATEWAY>_BASE_URL\`.

## URLs to register with providers

${urls}

## HTTP API (send header \`X-BDPAY-ADMIN: <BDPAY_ADMIN_TOKEN>\`)

| Method | Path | Purpose |
|---|---|---|
| POST | /bdpay/orders | Create order \`{order_id, amount, gateway, customer}\` (amount is saved server-side) |
| POST | /bdpay/orders/{id}/pay | Get \`redirect_url\` to send the customer to |
| GET | /bdpay/orders/{id} | Status and audit trail |
| POST | /bdpay/orders/{id}/requery | Ask the provider again |
| POST | /bdpay/orders/{id}/refund | \`{amount?, reason}\` |
| GET | /bdpay/gateways | Enabled gateways |
| GET/POST | /bdpay/callback/... and /bdpay/ipn/{gateway} | Used by providers |

Order IDs: letters, digits, \`-\` or \`_\`, up to 40 characters (Nagad: letters and digits only, up to 20). Use a new order ID for each new payment attempt.

## Use inside a Python app

\`\`\`python
from bdpay import BdPay, Status
pay = BdPay.from_env()
pay.create_order("INV1001", "1250.00", "${list[0]?.id ?? "bkash"}", {"name": "Rahim", "phone": "01700000000"})
url = pay.start_payment("INV1001")        # redirect the customer
# FastAPI: app.include_router(bdpay.web.build_router(pay, on_paid=your_fulfilment))
\`\`\`

Fulfil orders in \`on_paid\` in \`app.py\`: it fires once, only after server-side verification.

## Statuses

created → pending → paid | failed | cancelled | expired | needs_review; paid → partially_refunded → refunded.
\`needs_review\` means the provider reported a payment whose amount, currency or order number did not match: check it by hand.

## Operations

- \`python -m bdpay reconcile\` every 10 minutes (the compose file does this).
- Logs mask secrets and phone numbers; every state change is in the \`events\` table.
- SQLite is the default store. For several replicas, implement the same \`Store\` and \`TokenCache\` methods on Postgres/Redis.

## Go-live

${golive(list).map((x, i) => `${i + 1}. ${x}`).join("\n")}

## Security

Never commit \`.env\` or \`keys/\` (already in .gitignore; CI runs gitleaks). Report vulnerabilities privately to the maintainer.

## Licence

MIT. See LICENSE.
`;
}

/** Assemble the project. `files` is the bundled reference source (coreFiles.json). */
export function build(input: GeneratorInput, files: Record<string, string>): Project {
  const out: Project = {};
  const { gateways: list, vals } = input;
  const ids = list.map((g) => g.id);
  const pkg = input.pkg.trim();
  const today = input.today ?? new Date().toLocaleDateString("en-CA");
  const take = (path: string) => {
    const content = files[path];
    if (content === undefined) throw new Error(`reference file missing from the bundle: ${path}`);
    out[path] = content;
  };
  CORE.forEach(take);
  ids.forEach((id) => take(`bdpay/drivers/${id}.py`));
  take("app.py");
  ["tests/conftest.py", "tests/test_core.py"].forEach(take);
  ids.forEach((id) => take(`tests/test_${id}.py`));
  if (ids.includes("bkash")) take("tests/test_web.py"); // the route test drives a fake bKash

  // secrets and configuration
  const lines = [
    `# Generated by the bdpay configurator on ${today}. KEEP SECRET: never commit or share this file.`,
    dotenvLine("BDPAY_ENV", input.env),
    dotenvLine("BDPAY_GATEWAYS", ids.join(",")),
    dotenvLine("BDPAY_PUBLIC_URL", input.publicUrl.trim().replace(/\/$/, "")),
    dotenvLine("BDPAY_SECRET", input.secret),
    dotenvLine("BDPAY_ADMIN_TOKEN", input.admin),
    "BDPAY_DB=bdpay.sqlite3",
    dotenvLine("BDPAY_RESULT_URL", input.resultUrl.trim()),
    "BDPAY_LOG_LEVEL=INFO",
  ];
  const example = [
    "# Copy to .env and fill in. Generated by the bdpay configurator.",
    "BDPAY_ENV=sandbox",
    "BDPAY_GATEWAYS=" + ids.join(","),
    "BDPAY_PUBLIC_URL=https://your-domain.example",
    "BDPAY_SECRET=",
    "BDPAY_ADMIN_TOKEN=",
    "BDPAY_DB=bdpay.sqlite3",
    "BDPAY_RESULT_URL=",
    "BDPAY_LOG_LEVEL=INFO",
  ];
  for (const g of list) {
    lines.push("", `# --- ${g.name} ---`);
    example.push("", `# --- ${g.name} ---`);
    for (const f of g.f) {
      if (f.file && f.env) {
        // Key material goes to a git-ignored file; .env only holds its path.
        lines.push(dotenvLine(f.env, f.file));
        example.push(`${f.env}=${f.file}`);
        out[f.file] = (vals[f.k] || "").trim() + "\n";
        continue;
      }
      const v = fieldValue(f, vals);
      if (!v && !f.req) continue;
      if (isAwkward(v)) lines.push(`# ${f.k} mixes quote characters: .env readers disagree on it. Prefer a real environment variable.`);
      lines.push(dotenvLine(f.k, v));
      example.push(`${f.k}=${f.t === "secret" ? "" : f.demo ? "" : v}`);
    }
  }
  out[".env"] = lines.join("\n") + "\n";
  out[".env.example"] = example.join("\n") + "\n";
  out["requirements.txt"] = ["httpx>=0.27,<1", "fastapi>=0.110", "uvicorn[standard]>=0.29", "python-multipart>=0.0.9"]
    .concat(ids.includes("nagad") ? ["cryptography>=42"] : [])
    .join("\n") + "\n";
  ["requirements-dev.txt", "Dockerfile", "docker-compose.yml", ".gitignore", ".dockerignore", ".github/workflows/ci.yml"].forEach(take);
  out["LICENSE"] = (files["LICENSE"] ?? "").replace("__OWNER__", input.owner.trim() || "the authors");
  out["pyproject.toml"] = (files["pyproject.toml"] ?? "").replace("__PKGNAME__", pkg.toLowerCase());
  out["README.md"] = readme(input);
  return out;
}

// ------------------------------------------------------------ presentation
export const isSecretPath = (path: string) => path === ".env" || path.startsWith("keys/");

/** What the page shows for a file. Secrets are masked unless revealed. */
export function mask(path: string, content: string, reveal: boolean): string {
  if (reveal || !isSecretPath(path)) return content;
  if (path.startsWith("keys/")) {
    return content.trim() ? `(key hidden: ${content.trim().length} characters)` : "(empty)";
  }
  return content
    .split("\n")
    .map((line) => {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (!m || !/KEY|SECRET|PASSWORD|TOKEN|PASS/.test(m[1]!) || !m[2]) return line;
      return `${m[1]}=${"•".repeat(Math.min(16, m[2].length))}`;
    })
    .join("\n");
}

/** Files in the order the tree shows them: top level first, then folders. */
export function treeGroups(project: Project): { dir: string; paths: string[] }[] {
  const groups = new Map<string, string[]>();
  for (const path of Object.keys(project).sort((a, b) => a.localeCompare(b))) {
    const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    groups.set(dir, [...(groups.get(dir) ?? []), path]);
  }
  return [...groups.keys()]
    .sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)))
    .map((dir) => ({ dir, paths: groups.get(dir)! }));
}

export function lineCount(project: Project): number {
  return Object.values(project).reduce((n, s) => n + s.split("\n").length, 0);
}

/** The whole project as one Markdown document, secrets masked. */
export function bundleMarkdown(project: Project, pkg: string): string {
  const ext = (p: string) => (p.match(/\.(\w+)$/) || [])[1] || "";
  const lang: Record<string, string> = { py: "python", md: "markdown", yml: "yaml", toml: "toml", txt: "text", json: "json" };
  const sections = Object.keys(project)
    .sort()
    .map((p) => {
      const body = isSecretPath(p) ? mask(p, project[p]!, false) : project[p]!;
      const fence = body.includes("```") ? "````" : "```";
      const language = lang[ext(p)] || (p.includes("Dockerfile") ? "dockerfile" : "");
      return `## \`${p}\`\n\n${fence}${language}\n${body.replace(/\n$/, "")}\n${fence}\n`;
    })
    .join("\n");
  return `# ${pkg} - complete source bundle\n\nEvery file of the generated project. Recreate each file at its path, or give this document to an AI coding tool and ask it to write the files exactly as shown.\n\n${sections}\n> Secrets in .env and keys/ are masked in this bundle. Use the .zip for the real values.\n`;
}
