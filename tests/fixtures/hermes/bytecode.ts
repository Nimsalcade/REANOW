import { createHash } from "node:crypto";
import { hermesOpcodeTable } from "../../../src/hermes/HermesOpcodes.js";

type Kind = "string" | "identifier" | "predefined";

export interface HermesFixtureFunction {
  /** String id of the function name. */
  readonly name: number;
  readonly paramCount?: number;
  /** `[opcode name, ...operand values]` in instruction order. */
  readonly code: readonly (readonly [string, ...number[]])[];
  readonly handlers?: readonly {
    readonly start: number;
    readonly end: number;
    readonly target: number;
  }[];
  /** Store the metadata in an overflowed large header. */
  readonly large?: boolean;
  /** Bytes appended after the encoded instructions, such as a jump table. */
  readonly trailer?: Buffer;
  /** Replace the encoded instructions with these exact bytes. */
  readonly rawCode?: Buffer;
  /** Point this header at the bytecode of an earlier function instead. */
  readonly sameBodyAs?: number;
}

export interface HermesFixture {
  readonly version?: number;
  readonly strings: readonly (
    | string
    | { readonly value: string; readonly kind: Kind }
  )[];
  readonly functions: readonly HermesFixtureFunction[];
  readonly arrayBuffer?: Buffer;
  readonly objectKeyBuffer?: Buffer;
  readonly objectValueBuffer?: Buffer;
  readonly trailing?: Buffer;
  readonly corruptFooter?: boolean;
}

const KIND_CODES: Readonly<Record<Kind, number>> = {
  string: 0,
  identifier: 1,
  predefined: 2,
};

const u32 = (...values: readonly number[]): Buffer => {
  const bytes = Buffer.alloc(values.length * 4);
  for (const [index, value] of values.entries())
    bytes.writeUInt32LE(value >>> 0, index * 4);
  return bytes;
};

class Writer {
  readonly #parts: Buffer[] = [];
  #length = 0;

  get length(): number {
    return this.#length;
  }

  align(): void {
    const padding = (4 - (this.#length % 4)) % 4;
    if (padding > 0) this.push(Buffer.alloc(padding));
  }

  push(bytes: Buffer): number {
    const offset = this.#length;
    this.#parts.push(bytes);
    this.#length += bytes.length;
    return offset;
  }

  finish(): Buffer {
    return Buffer.concat(this.#parts);
  }
}

/** Encode instructions with the generated layout of one bytecode version. */
export const encodeHermesCode = (
  version: number,
  code: HermesFixtureFunction["code"],
): Buffer => {
  const table = hermesOpcodeTable(version);
  if (table === undefined) throw new Error(`No opcode table for ${version}`);
  const parts: Buffer[] = [];
  for (const [name, ...values] of code) {
    const index = table.opcodes.findIndex((opcode) => opcode.name === name);
    const opcode = table.opcodes[index];
    if (opcode === undefined) throw new Error(`Unknown opcode ${name}`);
    if (values.length !== opcode.operands.length)
      throw new Error(`${name} takes ${opcode.operands.length} operands`);
    const bytes = Buffer.alloc(opcode.size);
    bytes.writeUInt8(index, 0);
    for (const [position, operand] of opcode.operands.entries()) {
      const value = values[position] ?? 0;
      if (operand.code === "d") bytes.writeDoubleLE(value, operand.offset);
      else if (operand.code === "a") bytes.writeInt8(value, operand.offset);
      else if (operand.code === "A" || operand.code === "i")
        bytes.writeInt32LE(value, operand.offset);
      else bytes.writeUIntLE(value, operand.offset, operand.width);
    }
    parts.push(bytes);
  }
  return Buffer.concat(parts);
};

/** One tagged literal sequence: a short tag for fewer than 16 values. */
export const hermesLiteralSequence = (
  kind: "byte-string" | "short-string" | "long-string" | "integer" | "null",
  values: readonly number[],
): Buffer => {
  const tags = {
    null: [0x00, 0],
    "long-string": [0x40, 4],
    "short-string": [0x50, 2],
    "byte-string": [0x60, 1],
    integer: [0x70, 4],
  } as const;
  const [tag, width] = tags[kind];
  const head =
    values.length < 16
      ? Buffer.from([tag | values.length])
      : Buffer.from([0x80 | tag | (values.length >> 8), values.length & 0xff]);
  const body = Buffer.alloc(values.length * width);
  if (width > 0)
    for (const [index, value] of values.entries())
      body.writeUIntLE(value, index * width, width);
  return Buffer.concat([head, body]);
};

interface StringTables {
  readonly kinds: Buffer;
  readonly identifierCount: number;
  readonly small: Buffer;
  readonly overflow: Buffer;
  readonly storage: Buffer;
}

const stringTables = (
  version: number,
  strings: HermesFixture["strings"],
): StringTables => {
  const countBits = version < 71 ? 30 : 31;
  const runs: number[] = [];
  const small: Buffer[] = [];
  const overflow: Buffer[] = [];
  const storage = new Writer();
  let identifierCount = 0;
  let previous: Kind | null = null;
  for (const entry of strings) {
    const { value, kind } =
      typeof entry === "string"
        ? { value: entry, kind: "string" as const }
        : entry;
    if (kind !== "string") identifierCount += 1;
    if (kind === previous) runs[runs.length - 1] = (runs.at(-1) ?? 0) + 1;
    else runs.push(KIND_CODES[kind] * 2 ** countBits + 1);
    previous = kind;
    // eslint-disable-next-line no-control-regex
    const utf16 = /[^\x00-\x7f]/u.test(value);
    const offset = storage.push(
      Buffer.from(value, utf16 ? "utf16le" : "latin1"),
    );
    const fits = value.length < 0xff;
    const location = fits
      ? offset
      : overflow.push(u32(offset, value.length)) - 1;
    small.push(
      u32(
        (utf16 ? 1 : 0) + location * 2 + (fits ? value.length : 0xff) * 2 ** 24,
      ),
    );
  }
  return {
    kinds: u32(...runs),
    identifierCount,
    small: Buffer.concat(small),
    overflow: Buffer.concat(overflow),
    storage: storage.finish(),
  };
};

interface PlacedFunction {
  readonly source: HermesFixtureFunction;
  readonly offset: number;
  readonly size: number;
  infoOffset: number;
  largeOffset: number | null;
}

const flagsOf = (source: HermesFixtureFunction): number =>
  2 +
  (source.handlers === undefined ? 0 : 0b1000) +
  (source.large === true ? 0b10_0000 : 0);

const smallHeader = (placed: PlacedFunction): Buffer => {
  const { source } = placed;
  const bytes = Buffer.alloc(16);
  if (placed.largeOffset !== null) {
    bytes.writeUInt32LE(placed.largeOffset & 0xffff, 0);
    bytes.writeUInt32LE(placed.largeOffset >>> 16, 8);
  } else {
    bytes.writeUInt32LE(
      (placed.offset + (source.paramCount ?? 0) * 2 ** 25) >>> 0,
      0,
    );
    bytes.writeUInt32LE((placed.size + source.name * 2 ** 15) >>> 0, 4);
    bytes.writeUInt32LE((placed.infoOffset + 3 * 2 ** 25) >>> 0, 8);
  }
  bytes.writeUInt8(flagsOf(source), 15);
  return bytes;
};

const largeHeader = (placed: PlacedFunction): Buffer =>
  Buffer.concat([
    u32(
      placed.offset,
      placed.source.paramCount ?? 0,
      placed.size,
      placed.source.name,
      placed.infoOffset,
      3,
      0,
    ),
    Buffer.from([0, 0, flagsOf(placed.source)]),
  ]);

const headerBytes = (
  version: number,
  fields: {
    readonly fileLength: number;
    readonly functionCount: number;
    readonly tables: StringTables;
    readonly stringCount: number;
    readonly buffers: readonly [number, number, number];
    readonly debugInfoOffset: number;
  },
): Buffer => {
  const bytes = Buffer.alloc(128);
  bytes.writeBigUInt64LE(0x1f19_03c1_03bc_1fc6n, 0);
  bytes.writeUInt32LE(version, 8);
  bytes.fill(0xab, 12, 32);
  const words = [
    fields.fileLength,
    0,
    fields.functionCount,
    fields.tables.kinds.length / 4,
    fields.tables.identifierCount,
    fields.stringCount,
    fields.tables.overflow.length / 8,
    fields.tables.storage.length,
    ...(version >= 89 ? [0, 0] : []),
    0,
    0,
    ...fields.buffers,
    0,
    0,
    ...(version >= 84 ? [0] : []),
    fields.debugInfoOffset,
  ];
  u32(...words).copy(bytes, 32);
  return bytes;
};

/** Build a complete execution-form file the way the compiler lays one out. */
export const createHermesBytecode = (fixture: HermesFixture): Buffer => {
  const version = fixture.version ?? 96;
  const tables = stringTables(version, fixture.strings);
  const buffers = [
    fixture.arrayBuffer ?? Buffer.alloc(0),
    fixture.objectKeyBuffer ?? Buffer.alloc(0),
    fixture.objectValueBuffer ?? Buffer.alloc(0),
  ] as const;
  const writer = new Writer();
  writer.push(Buffer.alloc(128));
  const headersAt = writer.push(Buffer.alloc(fixture.functions.length * 16));
  for (const section of [
    tables.kinds,
    Buffer.alloc(tables.identifierCount * 4),
    tables.small,
    tables.overflow,
    tables.storage,
    ...buffers,
  ]) {
    writer.align();
    writer.push(section);
  }
  writer.align();
  const placed: PlacedFunction[] = [];
  for (const source of fixture.functions) {
    const shared =
      source.sameBodyAs === undefined ? undefined : placed[source.sameBodyAs];
    if (shared !== undefined) {
      placed.push({ ...shared, source, infoOffset: 0, largeOffset: null });
      continue;
    }
    const code = source.rawCode ?? encodeHermesCode(version, source.code);
    const offset = writer.push(code);
    if (source.trailer !== undefined) {
      writer.align();
      writer.push(source.trailer);
    }
    placed.push({
      source,
      offset,
      size: code.length,
      infoOffset: 0,
      largeOffset: null,
    });
  }
  for (const entry of placed) {
    writer.align();
    entry.infoOffset = writer.length;
    if (entry.source.large === true)
      entry.largeOffset = writer.push(largeHeader(entry));
    if (entry.source.handlers !== undefined) {
      writer.align();
      writer.push(
        u32(
          entry.source.handlers.length,
          ...entry.source.handlers.flatMap((handler) => [
            handler.start,
            handler.end,
            handler.target,
          ]),
        ),
      );
    }
  }
  writer.align();
  const debugInfoOffset = writer.push(Buffer.alloc(28));
  const footer = version >= 76 ? 20 : 0;
  const body = writer.finish();
  headerBytes(version, {
    fileLength: body.length + footer,
    functionCount: placed.length,
    tables,
    stringCount: fixture.strings.length,
    buffers: [buffers[0].length, buffers[1].length, buffers[2].length],
    debugInfoOffset,
  }).copy(body, 0);
  for (const [index, entry] of placed.entries())
    smallHeader(entry).copy(body, headersAt + index * 16);
  const hash = createHash("sha1").update(body).digest();
  if (fixture.corruptFooter === true) hash.writeUInt8(hash[0]! ^ 0xff, 0);
  return Buffer.concat([
    body,
    footer === 0 ? Buffer.alloc(0) : hash,
    fixture.trailing ?? Buffer.alloc(0),
  ]);
};

/** A small application-shaped bundle used across Hermes tests. */
export const sampleHermesFixture = (version = 96): HermesFixture => ({
  version,
  strings: [
    "",
    "global",
    "https://api.example.test/login",
    "Sign in failed",
    "ünïcode ✓",
    "x".repeat(300),
    { value: "fetch", kind: "identifier" },
    { value: "login", kind: "identifier" },
    { value: "token", kind: "identifier" },
    { value: "unused", kind: "identifier" },
  ],
  objectKeyBuffer: hermesLiteralSequence("byte-string", [8]),
  objectValueBuffer: hermesLiteralSequence("byte-string", [3]),
  arrayBuffer: hermesLiteralSequence("short-string", [4, 5]),
  functions: [
    {
      name: 1,
      code: [
        ["CreateEnvironment", 0],
        ["CreateClosure", 1, 0, 1],
        ["CreateClosure", 2, 0, 2],
        ["Ret", 1],
      ],
    },
    {
      name: 7,
      paramCount: 2,
      handlers: [{ start: 0, end: 10, target: 22 }],
      code: [
        ["LoadConstString", 0, 2],
        ["GetById", 1, 0, 1, 6],
        ["Jmp", 2],
        ["NewObjectWithBuffer", 2, 1, 1, 0, 0],
        ["Ret", 0],
      ],
    },
    {
      name: 0,
      large: true,
      code: [
        ["NewArrayWithBuffer", 0, 2, 2, 0],
        ["CallDirectLongIndex", 0, 1, 1],
        ["Ret", 0],
      ],
    },
  ],
});
