// The QR encoder, checked against an independent implementation.
//
// The expected values below were produced once with the qrcodegen reference
// encoder (Project Nayuki, version 1.8.0): byte mode, error-correction level M.
// A digest is the first 16 hex digits of the SHA-256 of the symbol written as
// lines of 0 and 1. During development the same comparison was run module for
// module on 2,416 symbols, and a ZXing reader decoded 302 of them back to
// their text; these are the cases kept.
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { encodeQr, qrPath } from "./qr";

const rows = (modules: boolean[][]) => modules.map((row) => row.map((dark) => (dark ? "1" : "0")).join(""));
const digest = (modules: boolean[][]) => createHash("sha256").update(rows(modules).join("\n")).digest("hex").slice(0, 16);

/** An otpauth-looking link cut to exactly n characters. */
const sample = (n: number) => ("otpauth://totp/Shop:rahim%40example.com?secret=" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".repeat(80)).slice(0, n);

// Most bytes a symbol of each version (1 to 40) holds at level M.
const CAPACITY = [
  14, 26, 42, 62, 84, 106, 122, 152, 180, 213, 251, 287, 331, 362,
  412, 450, 504, 560, 624, 666, 711, 779, 857, 911, 997, 1059, 1125, 1190,
  1264, 1370, 1452, 1538, 1628, 1722, 1809, 1911, 1989, 2099, 2213, 2331,
];

// For the fullest text of each version: the mask the reference picks, and the digest of its symbol.
const FULL: [number, string][] = [
  [2, "f45a4d73c34279a2"], [2, "ca204513538bc08c"], [6, "b4a3a1c65219ded6"], [2, "3a485e122c4d03d4"],
  [4, "d3a5bfcc3b943e6b"], [4, "9b6865579717d7b6"], [4, "e24495877a1dbf85"], [4, "5210939f074750e5"],
  [4, "b45777917f9f39da"], [2, "e5a66271f3d3315a"], [4, "6da27fa6ec911cdb"], [4, "3a31031136bc698d"],
  [3, "18ca0ccdcd1e0522"], [4, "3ba2326e18e6da55"], [4, "b57bbcb308a999df"], [1, "e37307336613afab"],
  [3, "445ef91310bdcaa5"], [4, "597c7efcc07c5fde"], [1, "38584769090146af"], [4, "dd751c7133ac506d"],
  [1, "67d3896ada97ba68"], [1, "408fc6d83698abc1"], [4, "528533cfe2545f5f"], [4, "52cfbe946e27626e"],
  [4, "48351002f51d2e6d"], [1, "13942d3b2f7503b1"], [1, "e290678c4ee8ac6e"], [1, "a62f802f822aa481"],
  [1, "6ceac94146e134ea"], [1, "247595f8e19ef66d"], [1, "dd5f484083300163"], [1, "99e88dff900417fd"],
  [1, "e34b00b8d2593648"], [1, "317624b89649426b"], [1, "73ec01a2b394a46f"], [1, "40a5b257ec0f0031"],
  [1, "68479485dd4f331d"], [3, "93a8fbe01c54d09d"], [1, "5176b7749859416a"], [1, "26e9e24bb8094b32"],
];

const LINK = "otpauth://totp/bdpay%20cloud:rahim%40example.com?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=bdpay%20cloud&algorithm=SHA1&digits=6&period=30";
const LINK_BY_MASK = [
  "2c4da774bba4bce2", "83df64d3fa72717c", "709d2185af8ab978", "82d1fa523af60b8a",
  "445c39b104760c1f", "5ad518946c0ad28a", "0eef6cfbee0a4771", "d1461c69c4aea383",
];

const BDPAY = [
  "111111101111001111111",
  "100000100011001000001",
  "101110100010101011101",
  "101110101101001011101",
  "101110101010101011101",
  "100000101001001000001",
  "111111101010101111111",
  "000000001101100000000",
  "100010111101011111001",
  "111011001101100101110",
  "011110100001001111010",
  "111111010010011010001",
  "110101100010111001011",
  "000000001110111000110",
  "111111101010110001010",
  "100000100011100100010",
  "101110101111001110001",
  "101110100001100000111",
  "101110100101001110000",
  "100000100110011010000",
  "111111101000111011001",
];

describe("qr", () => {
  it("draws a small symbol exactly as the reference does", () => {
    const code = encodeQr("bdpay");
    expect(code).toMatchObject({ version: 1, size: 21, mask: 4 });
    expect(rows(code.modules)).toEqual(BDPAY);
  });

  it("matches the reference for every version, filled to the last byte", () => {
    CAPACITY.forEach((bytes, i) => {
      const code = encodeQr(sample(bytes));
      expect([code.version, code.size, code.mask, digest(code.modules)], `version ${i + 1}`).toEqual([i + 1, 21 + 4 * i, ...FULL[i]]);
    });
  });

  it("moves to the next version one byte past each capacity, and refuses what cannot fit", () => {
    CAPACITY.slice(0, 39).forEach((bytes, i) => expect(encodeQr(sample(bytes + 1)).version).toBe(i + 2));
    expect(() => encodeQr(sample(CAPACITY[39] + 1))).toThrow(/too much/i);
    expect(encodeQr("").version).toBe(1);
  });

  it("matches the reference under each of the eight masks, and picks the same one", () => {
    for (let mask = 0; mask < 8; mask++) expect(digest(encodeQr(LINK, mask).modules), `mask ${mask}`).toBe(LINK_BY_MASK[mask]);
    const chosen = encodeQr(LINK);
    expect([chosen.version, chosen.mask]).toEqual([8, 2]);
    expect(digest(chosen.modules)).toBe(LINK_BY_MASK[2]);
  });

  it("encodes text outside ASCII as UTF-8", () => {
    const code = encodeQr("বিকাশ ৳1,250");
    expect([code.version, code.mask, digest(code.modules)]).toEqual([2, 3, "1110b6871764b56a"]);
  });

  it("turns the dark modules into one SVG path inside a quiet zone", () => {
    const code = encodeQr("bdpay");
    const path = qrPath(code);
    const dark = code.modules.flat().filter(Boolean).length;
    expect(path.match(/M/g)?.length).toBe(dark);
    expect(path.startsWith("M4,4h1v1h-1z")).toBe(true); // the corner of the first finder, four modules in
    expect(qrPath(code, 0).startsWith("M0,0h1v1h-1z")).toBe(true);
  });
});
