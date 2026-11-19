// The gateway catalog bundled at build time (scripts/bundle-core.mjs). It is
// the same file the service validates against, so forms and server agree.
import raw from "../generated/catalog.json";
import type { Catalog, GatewayDef } from "../api/types";

export const CATALOG = raw as unknown as Catalog;

export const STABILITY: Record<GatewayDef["st"], [string, string]> = {
  stable: ["Stable", "p-stable"],
  beta: ["Beta: check pack", "p-beta"],
  exp: ["Experimental", "p-exp"],
};

/** Gateways a self-hosted project can contain (the simulator only exists in the cloud). */
export const SELF_HOST_GATEWAYS = CATALOG.gateways.filter((g) => !g.cloudOnly);

const byId = new Map(CATALOG.gateways.map((g) => [g.id, g]));

export function gatewayDef(id: string): GatewayDef | undefined {
  return byId.get(id);
}

export function gatewayName(id: string): string {
  return byId.get(id)?.name ?? id;
}
