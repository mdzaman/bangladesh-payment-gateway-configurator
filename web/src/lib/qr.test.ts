// The encoder is checked against values printed in ISO/IEC 18004 and against
// whole symbols. When it was written, every symbol used here, every version
// from 1 to 20 at both ends of its capacity, and all eight masks were also read
// back with two independent decoders (jsQR 1.4 and ZXing 3.1) and gave the
// exact input. If you change the encoder and these hashes move, read the new
// output back with a real decoder before updating them.
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { MAX_VERSION, capacity, encodeQr, errorCorrection, formatBits, qrPath, versionBits, type QrCode } from "./qr";

const art = (code: QrCode) => code.modules.map((row) => row.map((dark) => (dark ? "#" : ".")).join("")).join("\n");
const sha256 = (text: string) => createHash("sha256").update(text).digest("hex");
const URI = "otpauth://totp/bdpay%20cloud:rahim%2Bshop%40example.com?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=bdpay%20cloud";

describe("QR encoder", () => {
  it("has the byte capacities of level M in the standard", () => {
    expect(Array.from({ length: MAX_VERSION }, (_, i) => capacity(i + 1))).toEqual(
      [14, 26, 42, 62, 84, 106, 122, 152, 180, 213, 251, 287, 331, 362, 412, 450, 504, 560, 624, 666],
    );
  });

  it("computes Reed-Solomon codewords", () => {
    // The data codewords of "HELLO WORLD" at version 1, level M: a widely published worked example.
    const data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17];
    expect(errorCorrection(data, 10)).toEqual([196, 35, 39, 119, 235, 215, 231, 226, 93, 23]);
  });

  it("computes the format and version bits listed in the standard", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 7].map((mask) => formatBits(mask).toString(2).padStart(15, "0"))).toEqual([
      "101010000010010", "101000100100101", "101111001111100", "101101101001011",
      "100010111111001", "100000011001110", "100111110010111", "100101010100000",
    ]);
    expect(versionBits(7).toString(2).padStart(18, "0")).toBe("000111110010010100");
    expect(versionBits(8).toString(2).padStart(18, "0")).toBe("001000010110111100");
    expect(versionBits(10).toString(2).padStart(18, "0")).toBe("001010010011010011");
  });

  it("picks the smallest version that holds the text", () => {
    expect(encodeQr("").version).toBe(1);
    expect(encodeQr("x".repeat(14)).version).toBe(1);
    expect(encodeQr("x".repeat(15)).version).toBe(2);
    expect(encodeQr("x".repeat(180)).version).toBe(9);
    expect(encodeQr("x".repeat(181)).version).toBe(10); // the length field grows from 8 to 16 bits here
    expect(encodeQr("৳".repeat(4)).version).toBe(1); // 12 bytes of UTF-8
    expect(encodeQr("৳".repeat(5)).version).toBe(2);
    const largest = encodeQr("x".repeat(666));
    expect([largest.version, largest.size]).toEqual([20, 97]);
    expect(() => encodeQr("x".repeat(667))).toThrow(/Too long/);
  });

  it("draws the fixed patterns", () => {
    for (const text of ["bdpay", URI, "x".repeat(300)]) {
      const { modules, size, version } = encodeQr(text);
      expect(size).toBe(4 * version + 17);
      expect(modules.length).toBe(size);
      const finder = ["#######", "#.....#", "#.###.#", "#.###.#", "#.###.#", "#.....#", "#######"];
      for (const [left, top] of [[0, 0], [size - 7, 0], [0, size - 7]]) {
        const found = finder.map((_, y) => modules[top + y].slice(left, left + 7).map((dark) => (dark ? "#" : ".")).join(""));
        expect(found).toEqual(finder);
      }
      for (let i = 8; i < size - 8; i++) {
        expect(modules[6][i]).toBe(i % 2 === 0);
        expect(modules[i][6]).toBe(i % 2 === 0);
      }
      expect(modules[size - 8][8]).toBe(true);
      if (version > 1) {
        // The alignment pattern nearest the bottom-right corner: dark centre, light ring, dark ring.
        const c = size - 7;
        expect([modules[c][c], modules[c][c + 1], modules[c][c + 2], modules[c + 1][c + 1], modules[c + 2][c + 2]])
          .toEqual([true, false, true, false, true]);
      }
    }
  });

  it("encodes a short text exactly as verified", () => {
    const code = encodeQr("bdpay");
    expect([code.version, code.mask]).toEqual([1, 4]);
    expect(art(code)).toBe([
      "#######.####..#######",
      "#.....#...##..#.....#",
      "#.###.#...#.#.#.###.#",
      "#.###.#.##.#..#.###.#",
      "#.###.#.#.#.#.#.###.#",
      "#.....#.#..#..#.....#",
      "#######.#.#.#.#######",
      "........##.##........",
      "#...#.####.#.#####..#",
      "###.##..##.##..#.###.",
      ".####.#....#..####.#.",
      "######.#..#..##.#...#",
      "##.#.##...#.###..#.##",
      "........###.###...##.",
      "#######.#.#.##...#.#.",
      "#.....#...###..#...#.",
      "#.###.#.####..###...#",
      "#.###.#....##.....###",
      "#.###.#..#.#..###....",
      "#.....#..##..##.#....",
      "#######.#...###.##..#",
    ].join("\n"));
  });

  it("encodes an authenticator link and the largest symbol exactly as verified", () => {
    const link = encodeQr(URI);
    expect([link.version, link.mask]).toEqual([7, 6]);
    expect(sha256(art(link))).toBe("984f8d68dec160bf16933051160aaaa2f868343256626be49b271333c82e36d1");
    const largest = encodeQr("x".repeat(666));
    expect([largest.version, largest.mask]).toEqual([20, 0]);
    expect(sha256(art(largest))).toBe("444723e60be059247a82da701388ba25443dcef5b36cfe0cca9d51b8da23b3b8");
  });

  it("can be told which mask to use", () => {
    const all = [0, 1, 2, 3, 4, 5, 6, 7].map((mask) => encodeQr("bdpay", mask));
    expect(all.map((code) => code.mask)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(new Set(all.map(art)).size).toBe(8);
    expect(art(all[4])).toBe(art(encodeQr("bdpay"))); // the one it chooses by itself
    expect(() => encodeQr("bdpay", 8)).toThrow(/No such mask/);
  });

  it("turns the symbol into one SVG path with a quiet zone", () => {
    const code = encodeQr("bdpay");
    const path = qrPath(code);
    expect(path.startsWith("M4 4h7v1h-7z")).toBe(true); // the top edge of the first finder, four modules in
    const drawn = [...path.matchAll(/M(\d+) (\d+)h(\d+)v1h-(\d+)z/g)];
    expect(drawn.map((m) => m[0]).join("")).toBe(path);
    expect(drawn.reduce((sum, m) => sum + Number(m[3]), 0)).toBe(code.modules.flat().filter(Boolean).length);
    expect(drawn.every((m) => Number(m[1]) >= 4 && Number(m[1]) + Number(m[3]) <= code.size + 4)).toBe(true);
    expect(qrPath(code, 0).startsWith("M0 0h7v1h-7z")).toBe(true);
  });
});
