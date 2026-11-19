import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { crc32, zipBytes } from "./zip";

const text = (s: string) => new TextEncoder().encode(s);

describe("zip", () => {
  it("computes the standard CRC-32", () => {
    expect(crc32(text("123456789"))).toBe(0xcbf43926);
    expect(crc32(text(""))).toBe(0);
  });

  it("writes one entry per file with a valid central directory", () => {
    const bytes = zipBytes({ "README.md": "# hi\n", "bdpay/core.py": "x = 1\n" }, "proj");
    const view = new DataView(bytes.buffer);
    expect(view.getUint32(0, true)).toBe(0x04034b50);
    const end = bytes.length - 22;
    expect(view.getUint32(end, true)).toBe(0x06054b50);
    expect(view.getUint16(end + 10, true)).toBe(2);
    expect(view.getUint32(view.getUint32(end + 16, true), true)).toBe(0x02014b50);
  });

  const unzip = spawnSync("unzip", ["-v"]).status === 0;
  it.skipIf(!unzip)("produces an archive real tools open, with secrets private", () => {
    const files = { ".env": "BDPAY_SECRET=abc\n", "bdpay/drivers/bkash.py": "# বিকাশ ৳\nprint('ok')\n", "keys/k.pem": "KEY\n" };
    const dir = mkdtempSync(join(tmpdir(), "bdpay-zip-"));
    const archive = join(dir, "out.zip");
    writeFileSync(archive, zipBytes(files, "bdpay-service", { isPrivate: (p) => p === ".env" || p.startsWith("keys/") }));
    expect(spawnSync("unzip", ["-tq", archive]).status).toBe(0); // CRCs check out
    expect(spawnSync("unzip", ["-q", archive, "-d", dir]).status).toBe(0);
    for (const [path, content] of Object.entries(files)) {
      expect(readFileSync(join(dir, "bdpay-service", path), "utf8")).toBe(content);
    }
    expect(statSync(join(dir, "bdpay-service", ".env")).mode & 0o777).toBe(0o600);
    expect(statSync(join(dir, "bdpay-service", "bdpay/drivers/bkash.py")).mode & 0o777).toBe(0o644);
  });
});
