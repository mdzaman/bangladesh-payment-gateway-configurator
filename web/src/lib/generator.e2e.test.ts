// Generates real projects and runs each one's own test-suite, the way a person
// who downloads the zip would. Needs a Python with the reference requirements:
// the repository's .venv (after `uv sync`) or BDPAY_PYTHON. Skipped otherwise.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import files from "../generated/coreFiles.json";
import { SELF_HOST_GATEWAYS } from "./catalog";
import { build } from "./generator";
import { filled } from "./testing";

const here = dirname(fileURLToPath(import.meta.url));
const candidates = [process.env.BDPAY_PYTHON, join(here, "../../../.venv/bin/python")].filter(Boolean) as string[];
const python = candidates.find((p) => existsSync(p) && spawnSync(p, ["-c", "import httpx, fastapi, pytest, cryptography"]).status === 0);
const ALL = SELF_HOST_GATEWAYS.map((g) => g.id);

function runTests(ids: string[]) {
  const dir = mkdtempSync(join(tmpdir(), "bdpay-generated-"));
  const project = build(filled(ids), files as Record<string, string>);
  for (const [path, content] of Object.entries(project)) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), content);
  }
  // -I: ignore the caller's environment, so only the generated folder is on the path.
  const env = { PATH: process.env.PATH ?? "", BDPAY_DOTENV: "/nonexistent" };
  const tests = spawnSync(python!, ["-I", "-m", "pytest", "-q", "-p", "no:cacheprovider"], { cwd: dir, env, encoding: "utf8" });
  const doctor = spawnSync(python!, ["-I", "-c", "import sys; sys.path.insert(0, '.'); import bdpay.registry as r; print(sorted(r.DRIVERS))"],
    { cwd: dir, env, encoding: "utf8" });
  return { tests, doctor, files: Object.keys(project).length };
}

describe.skipIf(!python)("a generated project passes its own tests", () => {
  it.each([...ALL.map((id) => [id]), ["bkash", "sslcommerz"], ALL].map((ids) => [ids.join("+"), ids] as const))(
    "%s", (_label, ids) => {
      const { tests, doctor } = runTests([...ids]);
      expect(tests.stdout + tests.stderr).toMatch(/\d+ passed/);
      expect(tests.stdout + tests.stderr).not.toMatch(/failed|error/i);
      expect(tests.status).toBe(0);
      expect(doctor.status).toBe(0);
    }, 60_000);

  it("runs all 35 reference tests when every gateway is selected", () => {
    const { tests } = runTests(ALL);
    expect(tests.stdout).toMatch(/35 passed/);
  }, 60_000);
});
