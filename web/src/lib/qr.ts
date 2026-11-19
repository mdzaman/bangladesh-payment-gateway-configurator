// A small QR Code encoder (ISO/IEC 18004), enough to show an otpauth:// link to
// an authenticator app. It runs here, in the page, on purpose: the link carries
// the two-factor secret, so it must not go to a QR service or a script from a CDN.
//
// Byte mode, error-correction level M, versions 1 to 40, mask picked by the
// standard's penalty rules. qr.test.ts checks the output module for module
// against matrices from an independent encoder.

export interface QrCode {
  version: number;
  /** Modules per side. */
  size: number;
  mask: number;
  /** modules[y][x] is true for a dark module. The four-module quiet zone is not included. */
  modules: boolean[][];
}

// Level M, by version: error-correction codewords per block, and number of blocks.
const ECC_PER_BLOCK = [
  10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
  26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
];
const BLOCKS = [
  1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
  17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49,
];
const FORMAT_LEVEL_M = 0; // the two format bits that name the error-correction level

/** Modules left for data and error correction once the fixed patterns are placed. */
function rawModules(version: number): number {
  let count = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const align = Math.floor(version / 7) + 2;
    count -= (25 * align - 10) * align - 55;
    if (version >= 7) count -= 36; // two copies of the version number
  }
  return count;
}

const totalCodewords = (version: number) => Math.floor(rawModules(version) / 8);
const dataCodewords = (version: number) => totalCodewords(version) - ECC_PER_BLOCK[version - 1] * BLOCKS[version - 1];

/** Centres of the alignment patterns along one axis. */
function alignmentCentres(version: number): number[] {
  if (version === 1) return [];
  const count = Math.floor(version / 7) + 2;
  const step = version === 32 ? 26 : Math.ceil((version * 4 + 4) / (count * 2 - 2)) * 2;
  const centres = [6];
  for (let at = version * 4 + 10; centres.length < count; at -= step) centres.splice(1, 0, at);
  return centres;
}

// ------------------------------------------------- Reed-Solomon over GF(256)
// The field is built on x^8 + x^4 + x^3 + x^2 + 1 (0x11d), with generator 2.
const EXP = new Uint8Array(510);
const LOG = new Uint8Array(256);
for (let i = 0, x = 1; i < 255; i++) {
  EXP[i] = EXP[i + 255] = x;
  LOG[x] = i;
  x <<= 1;
  if (x & 0x100) x ^= 0x11d;
}
const multiply = (a: number, b: number) => (a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]]);

/** Coefficients of (x - 2^0)(x - 2^1)...(x - 2^(degree-1)), highest power first, without the leading 1. */
function generatorPolynomial(degree: number): number[] {
  let poly = [1];
  for (let i = 0; i < degree; i++) {
    const next = Array.from({ length: poly.length + 1 }, () => 0);
    poly.forEach((coefficient, j) => {
      next[j] ^= coefficient;
      next[j + 1] ^= multiply(coefficient, EXP[i]);
    });
    poly = next;
  }
  return poly.slice(1);
}

/** The error-correction codewords of one block: the remainder of data / generator. */
function errorCorrection(data: number[], generator: number[]): number[] {
  const remainder = generator.map(() => 0);
  for (const byte of data) {
    const factor = byte ^ remainder[0];
    remainder.shift();
    remainder.push(0);
    generator.forEach((coefficient, i) => { remainder[i] ^= multiply(coefficient, factor); });
  }
  return remainder;
}

// ------------------------------------------------------------------ the data
/** Smallest version whose data area holds this many bytes in byte mode. */
function versionFor(byteLength: number): number {
  for (let version = 1; version <= 40; version++) {
    const header = 4 + (version <= 9 ? 8 : 16); // mode indicator, then the length
    if (header + byteLength * 8 <= dataCodewords(version) * 8) return version;
  }
  throw new Error("Too much text for a QR code");
}

/** Mode, length, the bytes, terminator and padding: exactly the version's data codewords. */
function dataStream(bytes: Uint8Array, version: number): number[] {
  const bits: number[] = [];
  const push = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4); // byte mode
  push(bytes.length, version <= 9 ? 8 : 16);
  bytes.forEach((byte) => push(byte, 8));
  const capacity = dataCodewords(version) * 8;
  push(0, Math.min(4, capacity - bits.length)); // terminator
  push(0, (8 - (bits.length % 8)) % 8); // up to a whole byte
  for (let pad = 0xec; bits.length < capacity; pad ^= 0xec ^ 0x11) push(pad, 8);
  const codewords: number[] = [];
  for (let i = 0; i < bits.length; i += 8) codewords.push(bits.slice(i, i + 8).reduce((byte, bit) => (byte << 1) | bit, 0));
  return codewords;
}

/** Split into blocks, add each block's error correction, and interleave them the way the standard reads them back. */
function withErrorCorrection(data: number[], version: number): number[] {
  const blocks = BLOCKS[version - 1];
  const eccLength = ECC_PER_BLOCK[version - 1];
  const total = totalCodewords(version);
  const shortBlocks = blocks - (total % blocks);
  const shortLength = Math.floor(total / blocks) - eccLength; // data codewords in a short block
  const generator = generatorPolynomial(eccLength);
  const dataBlocks: number[][] = [];
  const eccBlocks: number[][] = [];
  for (let b = 0, at = 0; b < blocks; b++) {
    const block = data.slice(at, at + shortLength + (b < shortBlocks ? 0 : 1));
    at += block.length;
    dataBlocks.push(block);
    eccBlocks.push(errorCorrection(block, generator));
  }
  const out: number[] = [];
  for (let i = 0; i <= shortLength; i++) for (const block of dataBlocks) if (i < block.length) out.push(block[i]);
  for (let i = 0; i < eccLength; i++) for (const block of eccBlocks) out.push(block[i]);
  return out;
}

// ---------------------------------------------------------------- the symbol
const MASKS: ((x: number, y: number) => boolean)[] = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
];

/** Remainder of value * x^degree divided by the generator: the check bits of a BCH code. */
function bchRemainder(value: number, degree: number, generator: number): number {
  let remainder = value;
  for (let i = 0; i < degree; i++) remainder = (remainder << 1) ^ ((remainder >>> (degree - 1)) * generator);
  return remainder;
}

class Matrix {
  readonly version: number;
  readonly size: number;
  readonly modules: boolean[][];
  /** Fixed patterns and format areas: data never goes here and masks never touch them. */
  private readonly fixed: boolean[][];

  constructor(version: number) {
    this.version = version;
    this.size = version * 4 + 17;
    const blank = () => Array.from({ length: this.size }, () => Array.from({ length: this.size }, () => false));
    this.modules = blank();
    this.fixed = blank();
    this.drawFixedPatterns();
  }

  private set(x: number, y: number, dark: boolean): void {
    this.modules[y][x] = dark;
    this.fixed[y][x] = true;
  }

  private drawFixedPatterns(): void {
    const last = this.size - 1;
    for (let i = 0; i <= last; i++) {
      this.set(6, i, i % 2 === 0); // timing patterns
      this.set(i, 6, i % 2 === 0);
    }
    // Finder patterns with their light separators, in three corners.
    for (const [cx, cy] of [[3, 3], [last - 3, 3], [3, last - 3]]) {
      for (let dy = -4; dy <= 4; dy++) {
        for (let dx = -4; dx <= 4; dx++) {
          const x = cx + dx;
          const y = cy + dy;
          const ring = Math.max(Math.abs(dx), Math.abs(dy));
          if (x >= 0 && x <= last && y >= 0 && y <= last) this.set(x, y, ring !== 2 && ring !== 4);
        }
      }
    }
    const centres = alignmentCentres(this.version);
    centres.forEach((cx, i) => centres.forEach((cy, j) => {
      const onFinder = (i === 0 && j === 0) || (i === 0 && j === centres.length - 1) || (i === centres.length - 1 && j === 0);
      if (onFinder) return;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) this.set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }));
    this.drawFormat(0); // reserves the area; the real bits follow once the mask is known
    if (this.version >= 7) {
      // The version number, 6 bits plus 12 check bits, beside two of the finders.
      const bits = (this.version << 12) | bchRemainder(this.version, 12, 0x1f25);
      for (let i = 0; i < 18; i++) {
        const dark = ((bits >>> i) & 1) === 1;
        const a = this.size - 11 + (i % 3);
        const b = Math.floor(i / 3);
        this.set(a, b, dark);
        this.set(b, a, dark);
      }
    }
  }

  /** Error-correction level and mask, 5 bits plus 10 check bits, written twice around the finders. */
  drawFormat(mask: number): void {
    const data = (FORMAT_LEVEL_M << 3) | mask;
    const bits = ((data << 10) | bchRemainder(data, 10, 0x537)) ^ 0x5412;
    const bit = (i: number) => ((bits >>> i) & 1) === 1;
    const last = this.size - 1;
    for (let i = 0; i <= 5; i++) this.set(8, i, bit(i));
    this.set(8, 7, bit(6));
    this.set(8, 8, bit(7));
    this.set(7, 8, bit(8));
    for (let i = 9; i < 15; i++) this.set(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) this.set(last - i, 8, bit(i));
    for (let i = 8; i < 15; i++) this.set(8, last - 14 + i, bit(i));
    this.set(8, last - 7, true); // always dark
  }

  /** Lay the codewords out in the zigzag the standard prescribes: two columns at a time, from the bottom right. */
  place(codewords: number[]): void {
    let i = 0;
    for (let right = this.size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5; // the vertical timing pattern takes a whole column
      const upward = ((right + 1) & 2) === 0;
      for (let step = 0; step < this.size; step++) {
        const y = upward ? this.size - 1 - step : step;
        for (const x of [right, right - 1]) {
          if (this.fixed[y][x] || i >= codewords.length * 8) continue;
          this.modules[y][x] = ((codewords[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
          i++;
        }
      }
    }
  }

  /** Flip the data modules a mask selects. Applying the same mask again undoes it. */
  applyMask(mask: number): void {
    for (let y = 0; y < this.size; y++) {
      for (let x = 0; x < this.size; x++) {
        if (!this.fixed[y][x] && MASKS[mask](x, y)) this.modules[y][x] = !this.modules[y][x];
      }
    }
  }

  /** How hard the symbol is to scan, by the standard's four rules. Lower is better. */
  penalty(): number {
    const { size, modules } = this;
    let score = 0;
    const lines: boolean[][] = [];
    for (let i = 0; i < size; i++) {
      lines.push(modules[i]);
      lines.push(modules.map((row) => row[i]));
    }
    for (const line of lines) {
      // Run lengths along the line: light, dark, light, ... The quiet zone around
      // the symbol is light, so the first and last runs reach into it.
      const runs = [size];
      let dark = false;
      for (const module of line) {
        if (module === dark) runs[runs.length - 1]++;
        else {
          runs.push(1);
          dark = module;
        }
      }
      if (dark) runs.push(0);
      runs[runs.length - 1] += size;
      runs.forEach((length, k) => {
        // Rule 1: five or more in a row of one colour (not counting the quiet zone).
        const inside = length - (k === 0 || k === runs.length - 1 ? size : 0);
        if (inside >= 5) score += inside - 2;
        // Rule 3: anything shaped like a finder pattern, dark 1:1:3:1:1 at any scale,
        // with light at least four units wide on one side.
        if (k % 2 === 0 || k < 3 || k + 3 >= runs.length) return;
        const unit = runs[k - 2];
        if (runs[k - 1] !== unit || length !== unit * 3 || runs[k + 1] !== unit || runs[k + 2] !== unit) return;
        const before = runs[k - 3];
        const after = runs[k + 3];
        if (before >= unit * 4 && after >= unit) score += 40;
        if (after >= unit * 4 && before >= unit) score += 40;
      });
    }
    // Rule 2: two-by-two blocks of one colour.
    for (let y = 0; y < size - 1; y++) {
      for (let x = 0; x < size - 1; x++) {
        const colour = modules[y][x];
        if (colour === modules[y][x + 1] && colour === modules[y + 1][x] && colour === modules[y + 1][x + 1]) score += 3;
      }
    }
    // Rule 4: dark and light should be roughly balanced. Ten points for every 5% away from half, past the first.
    const total = size * size;
    const darkCount = modules.reduce((sum, row) => sum + row.filter(Boolean).length, 0);
    score += (Math.ceil(Math.abs(darkCount * 20 - total * 10) / total) - 1) * 10;
    return score;
  }
}

/**
 * Encode text as a QR code. Leave `mask` out: the pattern that scans best is
 * chosen. (Tests pass one to compare all eight against a reference encoder.)
 */
export function encodeQr(text: string, mask?: number): QrCode {
  const bytes = new TextEncoder().encode(text);
  const version = versionFor(bytes.length);
  const symbol = new Matrix(version);
  symbol.place(withErrorCorrection(dataStream(bytes, version), version));
  let chosen = mask ?? 0;
  if (mask === undefined) {
    let best = Infinity;
    for (let candidate = 0; candidate < 8; candidate++) {
      symbol.applyMask(candidate);
      symbol.drawFormat(candidate);
      const score = symbol.penalty();
      if (score < best) [best, chosen] = [score, candidate];
      symbol.applyMask(candidate); // undo
    }
  }
  symbol.applyMask(chosen);
  symbol.drawFormat(chosen);
  return { version, size: symbol.size, mask: chosen, modules: symbol.modules };
}

/** SVG path data for the dark modules, one unit per module, offset by the quiet zone. */
export function qrPath(code: QrCode, quiet = 4): string {
  let path = "";
  code.modules.forEach((row, y) => row.forEach((dark, x) => {
    if (dark) path += `M${x + quiet},${y + quiet}h1v1h-1z`;
  }));
  return path;
}
