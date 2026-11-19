import { describe, expect, it } from "vitest";
import files from "../generated/coreFiles.json";
import { SELF_HOST_GATEWAYS } from "./catalog";
import {
  build, bundleMarkdown, countFiles, dotenvLine, golive, isSecretPath, lineCount, mask, treeGroups, validate, warnings,
} from "./generator";
import { filled, pick, referenceDotenv } from "./testing";

const FILES = files as Record<string, string>;
const ALL = SELF_HOST_GATEWAYS.map((g) => g.id);

describe("build", () => {
  it("offers the ten reference gateways and never the cloud-only simulator", () => {
    expect(ALL).toEqual(["bkash", "nagad", "sslcommerz", "aamarpay", "shurjopay", "eps", "portpos", "paystation", "upay", "uddoktapay"]);
  });

  it.each([[["bkash", "sslcommerz"]], [["aamarpay"]], [["nagad"]], [["bkash", "nagad"]], [ALL]])(
    "writes exactly the files the summary promised for %j", (ids) => {
      const project = build(filled(ids), FILES);
      expect(Object.keys(project)).toHaveLength(countFiles(pick(...ids)));
    });

  it("contains only the drivers and tests that were ticked", () => {
    const project = build(filled(["bkash", "sslcommerz"]), FILES);
    const drivers = Object.keys(project).filter((p) => p.startsWith("bdpay/drivers/")).sort();
    expect(drivers).toEqual(["bdpay/drivers/__init__.py", "bdpay/drivers/bkash.py", "bdpay/drivers/sslcommerz.py"]);
    expect(project["tests/test_web.py"]).toBeDefined(); // drives a fake bKash
    expect(build(filled(["sslcommerz"]), FILES)["tests/test_web.py"]).toBeUndefined();
    expect(project["tests/test_nagad.py"]).toBeUndefined();
  });

  it("ships the reference source byte for byte", () => {
    const project = build(filled(ALL), FILES);
    for (const path of ["bdpay/core.py", "bdpay/service.py", "bdpay/store.py", "bdpay/drivers/bkash.py", "app.py", "Dockerfile", "tests/test_core.py"]) {
      expect(project[path]).toBe(FILES[path]);
    }
  });

  it("puts the owner in the licence and the project name in pyproject", () => {
    const project = build(filled(["aamarpay"], { owner: "Karim Traders Ltd", pkg: "Karim-Pay" }), FILES);
    expect(project["LICENSE"]).toContain("Copyright (c) 2026 Karim Traders Ltd");
    expect(project["pyproject.toml"]).toContain('name = "karim-pay"');
    expect(project["LICENSE"]).not.toContain("__OWNER__");
    expect(build(filled(["aamarpay"], { owner: "  " }), FILES)["LICENSE"]).toContain("Copyright (c) 2026 the authors");
  });

  it("needs cryptography only when Nagad is in", () => {
    expect(build(filled(["bkash"]), FILES)["requirements.txt"]).not.toContain("cryptography");
    expect(build(filled(["nagad"]), FILES)["requirements.txt"]).toContain("cryptography>=42");
  });

  it("writes Nagad keys to git-ignored files, never into .env", () => {
    const input = filled(["nagad"]);
    input.vals.NAGAD_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----";
    const project = build(input, FILES);
    expect(project["keys/nagad_merchant_private.pem"]).toBe(input.vals.NAGAD_PRIVATE_KEY + "\n");
    expect(project[".env"]).toContain("NAGAD_PRIVATE_KEY_PATH=keys/nagad_merchant_private.pem");
    expect(project[".env"]).not.toContain("MIIEvQ");
    expect(project[".gitignore"]).toContain("keys/");
  });

  it("writes an .env the reference loader reads back exactly", () => {
    const awkward = { BKASH_APP_SECRET: 'p@ss "quoted" #hash $x \\ back', BKASH_PASSWORD: "it's got spaces & more", BKASH_USERNAME: "plain" };
    const input = filled(["bkash", "shurjopay"], { resultUrl: "https://shop.example/thanks?order={order_id}&status={status}" });
    Object.assign(input.vals, awkward);
    const env = referenceDotenv(build(input, FILES)[".env"]!);
    expect(env.BKASH_APP_SECRET).toBe(awkward.BKASH_APP_SECRET);
    expect(env.BKASH_PASSWORD).toBe(awkward.BKASH_PASSWORD);
    expect(env.SHURJOPAY_PASSWORD).toBe("pyyk97hu&6u6");
    expect(env.BDPAY_GATEWAYS).toBe("bkash,shurjopay");
    expect(env.BDPAY_SECRET).toBe("a".repeat(64));
    expect(env.BDPAY_RESULT_URL).toBe("https://shop.example/thanks?order={order_id}&status={status}");
    expect(env.BKASH_API_VERSION).toBe("v1.2.0-beta"); // defaults are written too
  });

  it("keeps secrets and demo values out of .env.example", () => {
    const example = build(filled(["bkash", "paystation"]), FILES)[".env.example"]!;
    expect(example).toContain("BKASH_APP_SECRET=\n");
    expect(example).toContain("BKASH_APP_KEY=\n"); // a demo value is not a default
    expect(example).toContain("PAYSTATION_PAY_WITH_CHARGE=0");
    expect(example).not.toContain("4f6o0cjiki2rfm34kfdadl1eqq");
    expect(example).toContain("BDPAY_SECRET=\n");
  });

  it("lists the IPN URL only for gateways that use one", () => {
    const readme = build(filled(["bkash", "sslcommerz"], { publicUrl: "https://pay.shop.example/" }), FILES)["README.md"]!;
    expect(readme).toContain("IPN `https://pay.shop.example/bdpay/ipn/sslcommerz`");
    expect(readme).not.toContain("/bdpay/ipn/bkash");
    expect(readme).toContain("| bKash | Stable | Partial |");
    expect(readme).toContain('pay.create_order("INV1001", "1250.00", "bkash"');
  });
});

describe("dotenvLine", () => {
  it.each([
    ["plain", "K=plain"], ["", "K="], ["a b", "K='a b'"], ["has#hash", "K='has#hash'"], ['say "hi"', `K='say "hi"'`],
    ["it's", `K="it's"`], ["$money", "K='$money'"],
  ])("%j", (value, line) => {
    expect(dotenvLine("K", value)).toBe(line);
    expect(referenceDotenv(line).K).toBe(value);
  });
});

describe("warnings", () => {
  it("flags a value no .env file can carry, and says what to do", () => {
    const input = filled(["bkash"]);
    expect(warnings(input)).toEqual([]);
    input.vals.BKASH_PASSWORD = `it's "both" kinds`;
    expect(warnings(input)).toEqual([expect.stringContaining("bKash: Password mixes quote characters")]);
    expect(build(input, FILES)[".env"]).toContain("# BKASH_PASSWORD mixes quote characters");
  });
});

describe("validate", () => {
  it("accepts a filled form", () => expect(validate(filled(["bkash", "sslcommerz"]))).toEqual([]));

  it("names every missing required field", () => {
    const input = filled(["bkash"]);
    input.vals = {};
    expect(validate(input)).toEqual(["bKash: App key is required", "bKash: App secret is required",
      "bKash: Username is required", "bKash: Password is required"]);
  });

  it("refuses sandbox demo values, http and localhost in live mode", () => {
    const errors = validate(filled(["aamarpay"], { env: "live" }));
    expect(errors).toContain("Live mode needs an https:// public URL");
    expect(errors).toContain("Live mode cannot use localhost");
    expect(errors).toContain("aamarPay: Store ID is a sandbox demo value; live mode needs your live credentials");
  });

  it("checks the project name, the secret and the compliance box", () => {
    expect(validate(filled(["bkash"], { pkg: "bad name!" }))[0]).toMatch(/^Project name/);
    expect(validate(filled(["bkash"], { secret: "short" }))[0]).toMatch(/at least 32 characters/);
    const input = filled(["uddoktapay"]);
    input.vals.BDPAY_ALLOW_EXPERIMENTAL = "";
    expect(validate(input)).toEqual(["UddoktaPay: tick the compliance box"]);
    expect(validate(filled([]))).toContain("Choose at least one gateway");
  });
});

describe("presentation", () => {
  const project = build(filled(["bkash", "nagad"]), FILES);

  it("masks secrets until revealed", () => {
    expect(isSecretPath(".env")).toBe(true);
    expect(isSecretPath("keys/nagad_public.pem")).toBe(true);
    expect(isSecretPath(".env.example")).toBe(false);
    const shown = mask(".env", project[".env"]!, false);
    expect(shown).toContain("BDPAY_SECRET=••••••••••••••••");
    expect(shown).toContain("BKASH_USERNAME=sandboxTokenizedUser02"); // not a secret
    expect(shown).not.toContain("a".repeat(64));
    expect(mask(".env", project[".env"]!, true)).toBe(project[".env"]);
    expect(mask("keys/nagad_public.pem", "MIIB\n", false)).toBe("(key hidden: 4 characters)");
  });

  it("bundles everything as Markdown with secrets still masked", () => {
    const md = bundleMarkdown(project, "bdpay-service");
    expect(md).toContain("## `bdpay/service.py`");
    expect(md).toContain("```python\n");
    expect(md).not.toContain("a".repeat(64));
    expect(md).toContain("Secrets in .env and keys/ are masked");
  });

  it("orders the tree and counts lines", () => {
    const groups = treeGroups(project);
    expect(groups[0]!.dir).toBe("");
    expect(groups.map((g) => g.dir)).toContain("bdpay/drivers");
    expect(lineCount(project)).toBeGreaterThan(1500);
  });

  it("adds go-live steps per gateway", () => {
    expect(golive(pick("nagad")).some((s) => s.startsWith("Nagad: live keys in `keys/`"))).toBe(true);
    expect(golive(pick("bkash")).some((s) => s.includes("Beta drivers"))).toBe(false);
    expect(golive(pick("eps")).some((s) => s.includes("Beta drivers"))).toBe(true);
  });
});
