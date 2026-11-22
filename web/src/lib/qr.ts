// QR code encoder, written to the layout rules of ISO/IEC 18004: byte mode,
// error-correction level M, versions 1 to 20 (up to 666 bytes).
//
// It exists so that the secret of an authenticator app can be shown as a QR code
// drawn in this browser. Nothing is sent to a QR service, and there is no
// dependency to trust with the secret.

export interface QrCode {
  version: number;
  /** Modules per side: 4 x version + 17. */
  size: number;
  /** Which of the eight mask patterns was applied. */
  mask: number;
  /** modules[y][x] is true for a dark module. No quiet zone. */
  modules: boolean[][];
}

export const MAX_VERSION = 20;

// Level M, indexed by version (index 0 is unused): error-correction codewords
// in each block, and how many blocks the codewords are split into.
const ECC_PER_BLOCK = [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26];
const BLOCKS = [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16];

// ------------------------------------------------------------ Reed-Solomon
// Arithmetic in GF(2^8) with the polynomial x^8 + x^4 + x^3 + x^2 + 1.
const EXP = new Uint8Array(510);
const LOG = new Uint8Array(256);
for (let i = 0, value = 1; i < 255; i++) {
  EXP[i] = EXP[i + 255] = value;
  LOG[value] = i;
  value <<= 1;
  if (value & 0x100) value ^= 0x11d;
}
const multiply = (a: number, b: number): number => (a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]]);

/** The `count` error-correction codewords for one block of data codewords. */
export function errorCorrection(data: readonly number[], count: number): number[] {
  // Generator polynomial: the product of (x - 2^i) for i below count, highest power first.
  let generator = [1];
  for (let i = 0; i < count; i++) {
    const next = Array.from({ length: generator.length + 1 }, () => 0);
    generator.forEach((coefficient, j) => {
      next[j] ^= coefficient;
      next[j + 1] ^= multiply(coefficient, EXP[i]);
    });
    generator = next;
  }
  // The remainder of data * x^count divided by the generator.
  const remainder = Array.from({ length: count }, () => 0);
  for (const byte of data) {
    const factor = byte ^ remainder[0];
    remainder.shift();
    remainder.push(0);
    for (let i = 0; i < count; i++) remainder[i] ^= multiply(generator[i + 1], factor);
  }
  return remainder;
}

// --------------------------------------------------------------- capacity
/** Modules left for data and error correction once every fixed pattern is placed. */
function rawModules(version: number): number {
  const size = 4 * version + 17;
  let modules = size * size - 3 * 64 - 2 * (size - 16) - 31; // finders, timing, format information
  if (version >= 2) {
    const perSide = Math.floor(version / 7) + 2;
    // 25 modules each, except that those on a timing line share 5 with it.
    modules -= 25 * (perSide * perSide - 3) - 10 * (perSide - 2);
  }
  if (version >= 7) modules -= 36; // version information
  return modules;
}

const totalCodewords = (version: number) => Math.floor(rawModules(version) / 8);
const dataCodewords = (version: number) => totalCodewords(version) - ECC_PER_BLOCK[version] * BLOCKS[version];
const lengthBits = (version: number) => (version < 10 ? 8 : 16);

/** How many bytes fit in one code of this version. */
export function capacity(version: number): number {
  return Math.floor((dataCodewords(version) * 8 - 4 - lengthBits(version)) / 8);
}

// ------------------------------------------------------------------- data
function dataBits(bytes: Uint8Array, version: number): number[] {
  const bits: number[] = [];
  const put = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  put(0b0100, 4); // byte mode
  put(bytes.length, lengthBits(version));
  bytes.forEach((byte) => put(byte, 8));
  const room = dataCodewords(version) * 8;
  put(0, Math.min(4, room - bits.length)); // terminator
  put(0, (8 - (bits.length % 8)) % 8);
  for (let pad = 0xec; bits.length < room; pad ^= 0xec ^ 0x11) put(pad, 8);
  return bits;
}

/** Data and error-correction codewords in the order they are laid into the symbol. */
function codewords(bytes: Uint8Array, version: number): number[] {
  const bits = dataBits(bytes, version);
  const data: number[] = [];
  for (let i = 0; i < bits.length; i += 8) data.push(Number.parseInt(bits.slice(i, i + 8).join(""), 2));

  const blockCount = BLOCKS[version];
  const eccLength = ECC_PER_BLOCK[version];
  const shortLength = Math.floor(totalCodewords(version) / blockCount) - eccLength;
  const longBlocks = totalCodewords(version) % blockCount; // these carry one data codeword more, and come last
  const blocks: { data: number[]; ecc: number[] }[] = [];
  for (let i = 0, at = 0; i < blockCount; i++) {
    const length = shortLength + (i >= blockCount - longBlocks ? 1 : 0);
    const part = data.slice(at, at + length);
    blocks.push({ data: part, ecc: errorCorrection(part, eccLength) });
    at += length;
  }
  const out: number[] = [];
  for (let i = 0; i <= shortLength; i++) {
    for (const block of blocks) if (i < block.data.length) out.push(block.data[i]);
  }
  for (let i = 0; i < eccLength; i++) {
    for (const block of blocks) out.push(block.ecc[i]);
  }
  return out;
}

// ----------------------------------------------------------------- symbol
/** Centres of the alignment patterns along one side. */
function alignmentCentres(version: number): number[] {
  if (version === 1) return [];
  const perSide = Math.floor(version / 7) + 2;
  const last = 4 * version + 10;
  // Evenly spaced back from the last one, on even steps; the first is always 6.
  const step = Math.ceil((last - 6) / (perSide - 1) / 2) * 2;
  const centres = [6];
  for (let i = perSide - 2; i >= 0; i--) centres.push(last - i * step);
  return centres;
}

/** Appends the remainder of `value` divided by `polynomial` (a BCH code). */
function withCheckBits(value: number, checkBits: number, polynomial: number): number {
  let remainder = value << checkBits;
  for (let bit = 31 - Math.clz32(remainder); bit >= checkBits; bit = 31 - Math.clz32(remainder)) {
    remainder ^= polynomial << (bit - checkBits);
  }
  return (value << checkBits) | remainder;
}

/** The 15 format bits for level M and one mask, as placed in the symbol. */
export function formatBits(mask: number): number {
  return withCheckBits(mask, 10, 0x537) ^ 0x5412; // level M is 00, so the five data bits are the mask alone
}

/** The 18 version bits (versions 7 and up). */
export function versionBits(version: number): number {
  return withCheckBits(version, 12, 0x1f25);
}

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

function build(version: number, stream: number[], mask: number): boolean[][] {
  const size = 4 * version + 17;
  const modules = Array.from({ length: size }, () => Array.from({ length: size }, () => false));
  const fixed = Array.from({ length: size }, () => Array.from({ length: size }, () => false));
  const set = (x: number, y: number, dark: boolean) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    modules[y][x] = dark;
    fixed[y][x] = true;
  };

  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  // Finder patterns with their light separators.
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const ring = Math.max(Math.abs(dx), Math.abs(dy));
        set(cx + dx, cy + dy, ring !== 2 && ring !== 4);
      }
    }
  }
  const centres = alignmentCentres(version);
  centres.forEach((cx, i) => centres.forEach((cy, j) => {
    const last = centres.length - 1;
    if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return; // a finder is there
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }));

  // Format information, twice: around the top-left finder, and split between the other two.
  const format = formatBits(mask);
  const bit = (value: number, i: number) => ((value >>> i) & 1) === 1;
  for (let i = 0; i < 15; i++) {
    const dark = bit(format, i);
    if (i < 6) set(8, i, dark);
    else if (i < 8) set(8, i + 1, dark);
    else if (i === 8) set(7, 8, dark);
    else set(14 - i, 8, dark);
    if (i < 8) set(size - 1 - i, 8, dark);
    else set(8, size - 15 + i, dark);
  }
  set(8, size - 8, true); // always dark
  if (version >= 7) {
    const info = versionBits(version);
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      set(a, b, bit(info, i));
      set(b, a, bit(info, i));
    }
  }

  // Data: two columns at a time from the right edge, snaking up and down.
  let next = 0;
  let upward = true;
  for (let right = size - 1; right > 0; right -= 2) {
    if (right === 6) right = 5; // the vertical timing line takes a whole column
    for (let n = 0; n < size; n++) {
      const y = upward ? size - 1 - n : n;
      for (const x of [right, right - 1]) {
        if (fixed[y][x]) continue;
        const dark = next < stream.length * 8 && bit(stream[next >>> 3], 7 - (next & 7));
        modules[y][x] = dark !== MASKS[mask](x, y);
        next++;
      }
    }
    upward = !upward;
  }
  return modules;
}

/** The standard's four penalty rules; the mask with the lowest total is used. */
function penalty(modules: boolean[][]): number {
  const size = modules.length;
  let total = 0;
  let darkCount = 0;
  const lines: boolean[][] = [];
  for (let i = 0; i < size; i++) {
    lines.push(modules[i], modules.map((row) => row[i]));
  }
  const finderLike = [true, false, true, true, true, false, true, false, false, false, false];
  for (const line of lines) {
    for (let i = 0, run = 1; i < size; i++, run++) {
      if (i + 1 < size && line[i + 1] === line[i]) continue;
      if (run >= 5) total += run - 2; // 3 for five in a row, one more for each beyond
      run = 0;
    }
    for (let i = 0; i + 11 <= size; i++) {
      if (finderLike.every((dark, j) => line[i + j] === dark) || finderLike.every((dark, j) => line[i + 10 - j] === dark)) {
        total += 40;
      }
    }
  }
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (modules[y][x]) darkCount++;
      if (x + 1 < size && y + 1 < size && modules[y][x] === modules[y][x + 1]
        && modules[y][x] === modules[y + 1][x] && modules[y][x] === modules[y + 1][x + 1]) total += 3;
    }
  }
  total += Math.floor(Math.abs((darkCount * 100) / (size * size) - 50) / 5) * 10;
  return total;
}

/**
 * Encodes `text` (as UTF-8). Throws if it does not fit in a version 20 symbol.
 * `forceMask` skips the choice of the best mask (any of the eight is readable).
 */
export function encodeQr(text: string, forceMask?: number): QrCode {
  const bytes = new TextEncoder().encode(text);
  let version = 1;
  while (version <= MAX_VERSION && capacity(version) < bytes.length) version++;
  if (version > MAX_VERSION) throw new Error(`Too long for a QR code here: ${bytes.length} bytes, at most ${capacity(MAX_VERSION)}`);
  const stream = codewords(bytes, version);
  let best: QrCode | null = null;
  let bestPenalty = Infinity;
  for (let mask = 0; mask < MASKS.length; mask++) {
    if (forceMask !== undefined && mask !== forceMask) continue;
    const modules = build(version, stream, mask);
    const score = penalty(modules);
    if (score < bestPenalty) {
      bestPenalty = score;
      best = { version, size: modules.length, mask, modules };
    }
  }
  if (!best) throw new Error(`No such mask: ${forceMask}`);
  return best;
}

/** An SVG path for the dark modules, one unit per module, offset by `margin` (the quiet zone). */
export function qrPath(code: QrCode, margin = 4): string {
  const parts: string[] = [];
  code.modules.forEach((row, y) => {
    for (let x = 0; x < code.size; x++) {
      if (!row[x]) continue;
      let end = x;
      while (end + 1 < code.size && row[end + 1]) end++;
      parts.push(`M${x + margin} ${y + margin}h${end - x + 1}v1h-${end - x + 1}z`);
      x = end;
    }
  });
  return parts.join("");
}
