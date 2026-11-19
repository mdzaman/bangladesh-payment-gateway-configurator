// Helpers shared by the generator tests (not shipped: only *.test.ts import it).
import type { GatewayDef } from "../api/types";
import { SELF_HOST_GATEWAYS } from "./catalog";
import type { GeneratorInput } from "./generator";

export function pick(...ids: string[]): GatewayDef[] {
  return ids.map((id) => {
    const g = SELF_HOST_GATEWAYS.find((x) => x.id === id);
    if (!g) throw new Error(`no such gateway: ${id}`);
    return g;
  });
}

/** A filled-in form: published demo values where they exist, placeholders elsewhere. */
export function filled(ids: string[], over: Partial<GeneratorInput> = {}): GeneratorInput {
  const gateways = pick(...ids);
  const vals: Record<string, string> = {};
  for (const g of gateways) {
    for (const f of g.f) {
      if (f.t === "check") vals[f.k] = "1";
      else if (f.demo) vals[f.k] = f.demo;
      else if (f.req) vals[f.k] = f.url ? "https://gateway.example" : `value-for-${f.k.toLowerCase()}`;
    }
  }
  return {
    gateways, env: "sandbox", publicUrl: "http://localhost:8000", pkg: "bdpay-service", owner: "Rahim Uddin",
    resultUrl: "", secret: "a".repeat(64), admin: "b".repeat(40), vals, today: "2026-11-18", ...over,
  };
}

/** The reference `.env` reader (core/bdpay/config.py load_dotenv), line for line. */
export function referenceDotenv(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const at = line.indexOf("=");
    const key = line.slice(0, at).trim();
    let value = line.slice(at + 1).trim();
    if (value.length >= 2 && value[0] === value[value.length - 1] && `"'`.includes(value[0]!)) value = value.slice(1, -1);
    env[key] = value;
  }
  return env;
}
