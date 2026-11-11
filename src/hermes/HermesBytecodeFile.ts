import { createHash } from "node:crypto";
import { HermesFormatFailure, malformed } from "./HermesBytecodeFailures.js";
import {
  HERMES_SUPPORTED_VERSIONS,
  hermesOpcodeTable,
  type HermesOpcodeTable,
} from "./HermesOpcodes.js";

/** "Hermes" in ancient Greek, UTF-16BE, truncated to eight bytes. */
const MAGIC = 0x1f19_03c1_03bc_1fc6n;
/** Bitwise complement of the magic marks the delta-diffing form. */
const DELTA_MAGIC = MAGIC ^ 0xffff_ffff_ffff_ffffn;
const HEADER_BYTES = 128;
const FOOTER_BYTES = 20 as const;
const SMALL_FUNCTION_HEADER_BYTES = 16;
const LARGE_FUNCTION_HEADER_BYTES = 31;
const OVERFLOWED_STRING_LENGTH = 0xff;

/** Version-dependent facts of the otherwise shared 59 through 96 layout. */
interface HermesLayout {
  /** Run-length bits of a string-kind entry; the remaining high bits are the kind. */
  readonly kindCountBits: 30 | 31;
  readonly bigInts: boolean;
  readonly functionSources: boolean;
  /** Header word that precedes cjsModuleCount. */
  readonly segmentField: "cjs_module_offset" | "segment_id";
  /** Entry size of a statically resolved CommonJS module table. */
  readonly staticCjsEntryBytes: 4 | 8;
  readonly hasAsyncOption: boolean;
  /** Bytes of the trailing SHA-1 footer; older versions end without one. */
  readonly footerBytes: 0 | 20;
}

const layoutFor = (version: number): HermesLayout => ({
  kindCountBits: version < 71 ? 30 : 31,
  bigInts: version >= 89,
  functionSources: version >= 84,
  segmentField: version < 83 ? "cjs_module_offset" : "segment_id",
  staticCjsEntryBytes: version < 83 ? 4 : 8,
  hasAsyncOption: version >= 84,
  footerBytes: version >= 76 ? FOOTER_BYTES : 0,
});

/** Header counters; null marks a field the version does not store. */
export interface HermesHeader {
  readonly version: number;
  readonly sourceHash: string;
  readonly fileLength: number;
  readonly globalCodeIndex: number;
  readonly functionCount: number;
  readonly stringKindCount: number;
  readonly identifierCount: number;
  readonly stringCount: number;
  readonly overflowStringCount: number;
  readonly stringStorageSize: number;
  readonly bigIntCount: number | null;
  readonly bigIntStorageSize: number | null;
  readonly regExpCount: number;
  readonly regExpStorageSize: number;
  readonly arrayBufferSize: number;
  readonly objKeyBufferSize: number;
  readonly objValueBufferSize: number;
  readonly segmentField: HermesLayout["segmentField"];
  readonly segmentValue: number;
  readonly cjsModuleCount: number;
  readonly functionSourceCount: number | null;
  readonly debugInfoOffset: number;
  readonly staticBuiltins: boolean;
  readonly cjsModulesStaticallyResolved: boolean;
  readonly hasAsync: boolean | null;
}

/** Contiguous table or storage region in file order. */
export interface HermesSection {
  readonly name: HermesSectionName;
  readonly offset: number;
  readonly bytes: number;
}

export const HERMES_SECTION_NAMES = [
  "function_headers",
  "string_kinds",
  "identifier_hashes",
  "small_string_table",
  "overflow_string_table",
  "string_storage",
  "array_buffer",
  "object_key_buffer",
  "object_value_buffer",
  "bigint_table",
  "bigint_storage",
  "regexp_table",
  "regexp_storage",
  "cjs_module_table",
  "function_source_table",
] as const;
export type HermesSectionName = (typeof HERMES_SECTION_NAMES)[number];

export type HermesStringKind = "string" | "identifier" | "predefined";

/** Function metadata from a small header or the large header it points to. */
export interface HermesFunctionHeader {
  readonly id: number;
  /** File offset of the first instruction. */
  readonly offset: number;
  readonly paramCount: number;
  readonly bytecodeSizeInBytes: number;
  readonly functionName: number;
  /** File offset of the optional exception table and debug offsets. */
  readonly infoOffset: number;
  readonly frameSize: number;
  readonly environmentSize: number;
  readonly highestReadCacheIndex: number;
  readonly highestWriteCacheIndex: number;
  readonly prohibitInvoke: number;
  readonly strictMode: boolean;
  readonly hasExceptionHandler: boolean;
  readonly hasDebugInfo: boolean;
  readonly overflowed: boolean;
}

/** Try range and handler target as offsets from the first instruction. */
export interface HermesExceptionHandler {
  readonly start: number;
  readonly end: number;
  readonly target: number;
}

interface KindRun {
  readonly start: number;
  readonly end: number;
  readonly kind: HermesStringKind;
}

const KINDS: readonly (HermesStringKind | undefined)[] = [
  "string",
  "identifier",
  "predefined",
];

const align4 = (offset: number): number => (offset + 3) & ~3;

/** Bounds-checked view of one execution-form Hermes bytecode file. */
export class HermesBytecodeFile {
  readonly header: HermesHeader;
  readonly opcodeTable: HermesOpcodeTable;
  readonly sections: readonly HermesSection[];
  readonly trailingBytes: number;
  readonly #bytes: Buffer;
  readonly #offsets: Readonly<Record<HermesSectionName, number>>;
  readonly #kindRuns: readonly KindRun[];
  readonly #footerBytes: 0 | 20;

  constructor(bytes: Buffer) {
    this.#bytes = bytes;
    const version = readVersion(bytes);
    const table = hermesOpcodeTable(version);
    if (table === undefined)
      throw new HermesFormatFailure(
        "unsupported",
        `Hermes bytecode version ${String(version)} is not decoded. Decoded released versions: ${HERMES_SUPPORTED_VERSIONS.join(", ")}.`,
      );
    this.opcodeTable = table;
    const layout = layoutFor(version);
    this.#footerBytes = layout.footerBytes;
    this.header = readHeader(bytes, version, layout);
    this.trailingBytes = bytes.length - this.header.fileLength;
    this.sections = readSections(this.header, layout);
    this.#offsets = sectionOffsets(this.sections);
    this.#kindRuns = this.#readKindRuns(layout.kindCountBits);
  }

  /** SHA-1 stored in the footer as lowercase hex; null before version 76. */
  get footerHash(): string | null {
    if (this.#footerBytes === 0) return null;
    const end = this.header.fileLength;
    return this.#bytes.subarray(end - FOOTER_BYTES, end).toString("hex");
  }

  /** SHA-1 of every byte that precedes the footer; null without a footer. */
  computeContentHash(): string | null {
    if (this.#footerBytes === 0) return null;
    return createHash("sha1")
      .update(this.#bytes.subarray(0, this.header.fileLength - FOOTER_BYTES))
      .digest("hex");
  }

  /** Byte at an absolute file offset the caller has already bounded. */
  byteAt(offset: number): number {
    return this.#bytes[offset] ?? malformed("Read past the end of the file.");
  }

  /** Raw bytes; consumers must stay inside header.fileLength. */
  get bytes(): Buffer {
    return this.#bytes;
  }

  sectionOffset(name: HermesSectionName): number {
    return this.#offsets[name];
  }

  stringKind(id: number): HermesStringKind {
    let low = 0;
    let high = this.#kindRuns.length - 1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      const run = this.#kindRuns[middle];
      if (run === undefined) break;
      if (id < run.start) high = middle - 1;
      else if (id >= run.end) low = middle + 1;
      else return run.kind;
    }
    return malformed(`String ${String(id)} has no string-kind run.`);
  }

  /** UTF-16 code-unit length of one string without decoding it. */
  stringLength(id: number): number {
    return this.#stringRange(id).length;
  }

  /** Decode at most `maximumUnits` leading UTF-16 code units of one string. */
  stringAt(id: number, maximumUnits = Number.POSITIVE_INFINITY): string {
    const range = this.#stringRange(id);
    const units = Math.min(range.length, maximumUnits);
    return range.utf16
      ? this.#bytes.toString("utf16le", range.start, range.start + units * 2)
      : this.#bytes.toString("latin1", range.start, range.start + units);
  }

  functionHeader(id: number): HermesFunctionHeader {
    const { functionCount, fileLength, stringCount } = this.header;
    if (!Number.isInteger(id) || id < 0 || id >= functionCount)
      malformed(`Function ${String(id)} is outside the function table.`);
    const small = this.#smallHeader(id);
    const header = small.overflowed ? this.#largeHeader(id, small) : small;
    if (
      header.offset > fileLength ||
      header.bytecodeSizeInBytes > fileLength - header.offset
    )
      malformed(`Function ${String(id)} bytecode is outside the file.`);
    if (header.functionName >= stringCount)
      malformed(`Function ${String(id)} name is outside the string table.`);
    return header;
  }

  exceptionHandlers(header: HermesFunctionHeader): HermesExceptionHandler[] {
    if (!header.hasExceptionHandler) return [];
    const limit = this.header.fileLength;
    const start = align4(header.infoOffset);
    if (start + 4 > limit)
      malformed(`Function ${String(header.id)} exception table is truncated.`);
    const count = this.#bytes.readUInt32LE(start);
    if (count > (limit - start - 4) / 12)
      malformed(`Function ${String(header.id)} exception table is truncated.`);
    const handlers: HermesExceptionHandler[] = [];
    for (let index = 0; index < count; index += 1) {
      const entry = start + 4 + index * 12;
      handlers.push({
        start: this.#bytes.readUInt32LE(entry),
        end: this.#bytes.readUInt32LE(entry + 4),
        target: this.#bytes.readUInt32LE(entry + 8),
      });
    }
    return handlers;
  }

  #readKindRuns(countBits: 30 | 31): KindRun[] {
    const runs: KindRun[] = [];
    const base = this.#offsets.string_kinds;
    const countMask = 2 ** countBits - 1;
    let start = 0;
    for (let index = 0; index < this.header.stringKindCount; index += 1) {
      const datum = this.#bytes.readUInt32LE(base + index * 4);
      const kind = KINDS[Math.floor(datum / 2 ** countBits)];
      const count = datum & countMask;
      if (kind === undefined || count === 0)
        malformed(`String-kind run ${String(index)} is invalid.`);
      else runs.push({ start, end: start + count, kind });
      start += count;
    }
    if (start !== this.header.stringCount)
      malformed(
        `String-kind runs cover ${String(start)} strings; the header records ${String(this.header.stringCount)}.`,
      );
    return runs;
  }

  #stringRange(id: number): {
    readonly start: number;
    readonly length: number;
    readonly utf16: boolean;
  } {
    const header = this.header;
    if (!Number.isInteger(id) || id < 0 || id >= header.stringCount)
      malformed(`String ${String(id)} is outside the string table.`);
    const entry = this.#bytes.readUInt32LE(
      this.#offsets.small_string_table + id * 4,
    );
    const utf16 = (entry & 1) === 1;
    let offset = (entry >>> 1) & 0x7f_ffff;
    let length = entry >>> 24;
    if (length === OVERFLOWED_STRING_LENGTH) {
      if (offset >= header.overflowStringCount)
        malformed(`String ${String(id)} overflow entry is out of range.`);
      const overflow = this.#offsets.overflow_string_table + offset * 8;
      offset = this.#bytes.readUInt32LE(overflow);
      length = this.#bytes.readUInt32LE(overflow + 4);
    }
    const byteLength = utf16 ? length * 2 : length;
    if (
      offset > header.stringStorageSize ||
      byteLength > header.stringStorageSize - offset
    )
      malformed(`String ${String(id)} is outside string storage.`);
    return { start: this.#offsets.string_storage + offset, length, utf16 };
  }

  #smallHeader(id: number): HermesFunctionHeader {
    const base =
      this.#offsets.function_headers + id * SMALL_FUNCTION_HEADER_BYTES;
    const first = this.#bytes.readUInt32LE(base);
    const second = this.#bytes.readUInt32LE(base + 4);
    const third = this.#bytes.readUInt32LE(base + 8);
    return {
      id,
      offset: first & 0x1ff_ffff,
      paramCount: first >>> 25,
      bytecodeSizeInBytes: second & 0x7fff,
      functionName: second >>> 15,
      infoOffset: third & 0x1ff_ffff,
      frameSize: third >>> 25,
      environmentSize: this.byteAt(base + 12),
      highestReadCacheIndex: this.byteAt(base + 13),
      highestWriteCacheIndex: this.byteAt(base + 14),
      ...flagsOf(this.byteAt(base + 15)),
    };
  }

  #largeHeader(id: number, small: HermesFunctionHeader): HermesFunctionHeader {
    const base = small.infoOffset * 0x1_0000 + small.offset;
    if (base + LARGE_FUNCTION_HEADER_BYTES > this.header.fileLength)
      malformed(`Function ${String(id)} large header is outside the file.`);
    const word = (index: number): number =>
      this.#bytes.readUInt32LE(base + index * 4);
    return {
      id,
      offset: word(0),
      paramCount: word(1),
      bytecodeSizeInBytes: word(2),
      functionName: word(3),
      infoOffset: base + LARGE_FUNCTION_HEADER_BYTES,
      frameSize: word(5),
      environmentSize: word(6),
      highestReadCacheIndex: this.byteAt(base + 28),
      highestWriteCacheIndex: this.byteAt(base + 29),
      ...flagsOf(this.byteAt(base + 30)),
      overflowed: true,
    };
  }
}

const flagsOf = (
  flags: number,
): Pick<
  HermesFunctionHeader,
  | "prohibitInvoke"
  | "strictMode"
  | "hasExceptionHandler"
  | "hasDebugInfo"
  | "overflowed"
> => ({
  prohibitInvoke: flags & 0b11,
  strictMode: (flags & 0b100) !== 0,
  hasExceptionHandler: (flags & 0b1000) !== 0,
  hasDebugInfo: (flags & 0b1_0000) !== 0,
  overflowed: (flags & 0b10_0000) !== 0,
});

const readVersion = (bytes: Buffer): number => {
  if (bytes.length < 12)
    throw new HermesFormatFailure(
      "unsupported",
      "The file is shorter than a Hermes bytecode magic and version.",
    );
  const magic = bytes.readBigUInt64LE(0);
  if (magic === DELTA_MAGIC)
    throw new HermesFormatFailure(
      "unsupported",
      "The file is delta-form Hermes bytecode, which is prepared for binary diffing and is not decoded.",
    );
  if (magic !== MAGIC)
    throw new HermesFormatFailure(
      "unsupported",
      "The file does not start with the Hermes bytecode magic. A plain JavaScript bundle is analyzed with the JavaScript tools instead.",
    );
  return bytes.readUInt32LE(8);
};

const readHeader = (
  bytes: Buffer,
  version: number,
  layout: HermesLayout,
): HermesHeader => {
  if (bytes.length < HEADER_BYTES)
    malformed("The file is shorter than the 128-byte Hermes file header.");
  let cursor = 32;
  const next = (): number => {
    const value = bytes.readUInt32LE(cursor);
    cursor += 4;
    return value;
  };
  const fileLength = next();
  const globalCodeIndex = next();
  const functionCount = next();
  const stringKindCount = next();
  const identifierCount = next();
  const stringCount = next();
  const overflowStringCount = next();
  const stringStorageSize = next();
  const bigIntCount = layout.bigInts ? next() : null;
  const bigIntStorageSize = layout.bigInts ? next() : null;
  const regExpCount = next();
  const regExpStorageSize = next();
  const arrayBufferSize = next();
  const objKeyBufferSize = next();
  const objValueBufferSize = next();
  const segmentValue = next();
  const cjsModuleCount = next();
  const functionSourceCount = layout.functionSources ? next() : null;
  const debugInfoOffset = next();
  const options = bytes[cursor] ?? 0;
  if (fileLength > bytes.length)
    malformed(
      `The header records ${String(fileLength)} bytes; the file has ${String(bytes.length)}.`,
    );
  if (fileLength < HEADER_BYTES + layout.footerBytes)
    malformed("The recorded file length cannot hold a header and footer.");
  if (functionCount === 0) malformed("The file declares no functions.");
  if (globalCodeIndex >= functionCount)
    malformed("The global function index is outside the function table.");
  if (debugInfoOffset > fileLength)
    malformed("The debug info offset is outside the file.");
  return {
    version,
    sourceHash: bytes.subarray(12, 32).toString("hex"),
    fileLength,
    globalCodeIndex,
    functionCount,
    stringKindCount,
    identifierCount,
    stringCount,
    overflowStringCount,
    stringStorageSize,
    bigIntCount,
    bigIntStorageSize,
    regExpCount,
    regExpStorageSize,
    arrayBufferSize,
    objKeyBufferSize,
    objValueBufferSize,
    segmentField: layout.segmentField,
    segmentValue,
    cjsModuleCount,
    functionSourceCount,
    debugInfoOffset,
    staticBuiltins: (options & 0b1) !== 0,
    cjsModulesStaticallyResolved: (options & 0b10) !== 0,
    hasAsync: layout.hasAsyncOption ? (options & 0b100) !== 0 : null,
  };
};

const sectionSizes = (
  header: HermesHeader,
  layout: HermesLayout,
): readonly (readonly [HermesSectionName, number])[] => {
  const cjsEntryBytes = header.cjsModulesStaticallyResolved
    ? layout.staticCjsEntryBytes
    : 8;
  return [
    ["function_headers", header.functionCount * SMALL_FUNCTION_HEADER_BYTES],
    ["string_kinds", header.stringKindCount * 4],
    ["identifier_hashes", header.identifierCount * 4],
    ["small_string_table", header.stringCount * 4],
    ["overflow_string_table", header.overflowStringCount * 8],
    ["string_storage", header.stringStorageSize],
    ["array_buffer", header.arrayBufferSize],
    ["object_key_buffer", header.objKeyBufferSize],
    ["object_value_buffer", header.objValueBufferSize],
    ...(header.bigIntCount === null || header.bigIntStorageSize === null
      ? []
      : ([
          ["bigint_table", header.bigIntCount * 8],
          ["bigint_storage", header.bigIntStorageSize],
        ] as const)),
    ["regexp_table", header.regExpCount * 8],
    ["regexp_storage", header.regExpStorageSize],
    ["cjs_module_table", header.cjsModuleCount * cjsEntryBytes],
    ...(header.functionSourceCount === null
      ? []
      : ([["function_source_table", header.functionSourceCount * 8]] as const)),
  ];
};

/** Each table starts at the next four-byte boundary after its predecessor. */
const readSections = (
  header: HermesHeader,
  layout: HermesLayout,
): HermesSection[] => {
  const sections: HermesSection[] = [];
  const limit = header.fileLength - layout.footerBytes;
  let cursor = HEADER_BYTES;
  for (const [name, bytes] of sectionSizes(header, layout)) {
    const offset = align4(cursor);
    if (offset > limit || bytes > limit - offset)
      malformed(`The ${name} table extends past the end of the file.`);
    sections.push({ name, offset, bytes });
    cursor = offset + bytes;
  }
  return sections;
};

const sectionOffsets = (
  sections: readonly HermesSection[],
): Record<HermesSectionName, number> => {
  const offsets = Object.fromEntries(
    HERMES_SECTION_NAMES.map((name) => [name, 0]),
  ) as Record<HermesSectionName, number>;
  for (const section of sections) offsets[section.name] = section.offset;
  return offsets;
};
