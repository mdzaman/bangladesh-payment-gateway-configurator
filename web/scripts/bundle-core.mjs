// Bundles two things from the rest of the repository into src/generated/ so the
// web app needs no server to show them:
//
//   coreFiles.json  every file of the reference engine in ../core. The self-host
//                   generator builds its download from these, in the browser.
//   catalog.json    the gateway catalog the service itself validates against.
//
// Run automatically before dev, build, typecheck and test.
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const core = join(repo, "core");
const out = join(here, "..", "src", "generated");

const SKIP_DIRS = new Set(["__pycache__", ".pytest_cache", ".venv", "keys", "node_modules"]);
// Written by the generator itself from what the person types.
const SKIP_FILES = new Set([".env", ".env.example", "requirements.txt", "README.md"]);

function walk(dir, files = {}) {
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (!SKIP_DIRS.has(name)) walk(path, files);
      continue;
    }
    const rel = relative(core, path).split(sep).join("/");
    if (SKIP_FILES.has(rel) || name.endsWith(".pyc") || name.includes(".sqlite3")) continue;
    files[rel] = readFileSync(path, "utf8");
  }
  return files;
}

const files = walk(core);
// A generated project needs every one of these. Fail the build rather than ship
// a generator that breaks when someone presses Generate (for example when a
// .dockerignore keeps core/tests out of the build context).
const required = ["app.py", "bdpay/core.py", "bdpay/service.py", "bdpay/drivers/bkash.py", "tests/conftest.py",
  "tests/test_core.py", "tests/test_web.py", "Dockerfile", "docker-compose.yml", ".gitignore", ".dockerignore",
  ".github/workflows/ci.yml", "requirements-dev.txt", "LICENSE", "pyproject.toml"];
const missing = required.filter((path) => !(path in files));
if (missing.length) throw new Error(`core/ is incomplete, missing: ${missing.join(", ")}`);
// The generator substitutes these two per project, like the original configurator.
files["LICENSE"] = files["LICENSE"].replace("Copyright (c) 2026 Mohammad M. Zaman", "Copyright (c) 2026 __OWNER__");
files["pyproject.toml"] = files["pyproject.toml"].replace('name = "bdpay-service"', 'name = "__PKGNAME__"');
if (!files["LICENSE"].includes("__OWNER__") || !files["pyproject.toml"].includes("__PKGNAME__")) {
  throw new Error("core/LICENSE or core/pyproject.toml no longer has the text the generator substitutes");
}

const catalog = JSON.parse(readFileSync(join(repo, "cloud", "bdpay_cloud", "catalog", "gateways.json"), "utf8"));
delete catalog._comment;

mkdirSync(out, { recursive: true });
writeFileSync(join(out, "coreFiles.json"), JSON.stringify(files));
writeFileSync(join(out, "catalog.json"), JSON.stringify(catalog));
console.log(`bundled ${Object.keys(files).length} core files and ${catalog.gateways.length} gateways into src/generated/`);
