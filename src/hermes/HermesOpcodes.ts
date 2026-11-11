import {
  HERMES_OPCODE_SOURCES,
  HERMES_OPCODE_TABLES,
  type HermesOpcodeSource,
} from "./hermesOpcodeTables.js";

/** Operand storage codes emitted by scripts/generate-hermes-opcode-tables.mjs. */
export type HermesOperandCode =
  | "r"
  | "R"
  | "b"
  | "h"
  | "w"
  | "a"
  | "A"
  | "i"
  | "d";

/** Table an unsigned operand indexes, when upstream declares one. */
export type HermesOperandTarget = "string" | "function" | "bigint" | null;

/** One operand slot at a fixed byte offset from the opcode byte. */
export interface HermesOperand {
  readonly code: HermesOperandCode;
  readonly target: HermesOperandTarget;
  readonly offset: number;
  readonly width: 1 | 2 | 4 | 8;
}

/** One fixed-width instruction layout. */
export interface HermesOpcode {
  readonly name: string;
  readonly operands: readonly HermesOperand[];
  readonly size: number;
}

/** Decoded layouts for one released bytecode version. */
export interface HermesOpcodeTable {
  readonly source: HermesOpcodeSource;
  readonly opcodes: readonly HermesOpcode[];
  /** Instruction byte size by opcode byte; zero marks an undefined opcode. */
  readonly sizes: Uint8Array;
}

const WIDTHS: Readonly<Record<HermesOperandCode, 1 | 2 | 4 | 8>> = {
  r: 1,
  R: 4,
  b: 1,
  h: 2,
  w: 4,
  a: 1,
  A: 4,
  i: 4,
  d: 8,
};
const TARGETS: Readonly<Record<string, HermesOperandTarget>> = {
  "": null,
  s: "string",
  f: "function",
  n: "bigint",
};

const isOperandCode = (value: string): value is HermesOperandCode =>
  value in WIDTHS;

const parseOpcode = (entry: string): HermesOpcode => {
  const separator = entry.indexOf(":");
  const name = entry.slice(0, separator);
  const encoded = entry.slice(separator + 1);
  const operands: HermesOperand[] = [];
  let offset = 1;
  for (const part of encoded === "" ? [] : encoded.split(",")) {
    const code = part.slice(0, 1);
    const target = TARGETS[part.slice(1)];
    if (!isOperandCode(code) || target === undefined)
      throw new Error(`Invalid generated Hermes operand: ${entry}`);
    const width = WIDTHS[code];
    operands.push({ code, target, offset, width });
    offset += width;
  }
  return { name, operands, size: offset };
};

const parsedTables = new Map<number, readonly HermesOpcode[]>();

const opcodesFor = (table: number): readonly HermesOpcode[] => {
  const cached = parsedTables.get(table);
  if (cached !== undefined) return cached;
  const encoded = HERMES_OPCODE_TABLES[table];
  if (encoded === undefined)
    throw new Error(`Missing generated Hermes opcode table ${String(table)}`);
  const opcodes = encoded.split(" ").map(parseOpcode);
  parsedTables.set(table, opcodes);
  return opcodes;
};

/** Released bytecode versions with a generated opcode table, ascending. */
export const HERMES_SUPPORTED_VERSIONS: readonly number[] =
  HERMES_OPCODE_SOURCES.map((source) => source.version);

/** Opcode layouts for one released version; undefined for any other version. */
export const hermesOpcodeTable = (
  version: number,
): HermesOpcodeTable | undefined => {
  const source = HERMES_OPCODE_SOURCES.find(
    (candidate) => candidate.version === version,
  );
  if (source === undefined) return undefined;
  const opcodes = opcodesFor(source.table);
  const sizes = new Uint8Array(256);
  for (const [index, opcode] of opcodes.entries()) sizes[index] = opcode.size;
  return { source, opcodes, sizes };
};
