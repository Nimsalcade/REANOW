import { malformed } from "./HermesBytecodeFailures.js";
import type {
  HermesBytecodeFile,
  HermesFunctionHeader,
} from "./HermesBytecodeFile.js";
import type { HermesOpcode, HermesOperand } from "./HermesOpcodes.js";
import type { HermesWorkBudget } from "./HermesWorkBudget.js";

/** Longest string operand rendered inline before it is elided. */
const INLINE_STRING_UNITS = 96;
/** Jump-table targets rendered inline for one SwitchImm. */
const INLINE_SWITCH_TARGETS = 64;

/** One decoded instruction at a byte offset from the function's first opcode. */
export interface HermesInstruction {
  readonly offset: number;
  readonly opcode: HermesOpcode;
  /** Absolute file offset of the opcode byte. */
  readonly address: number;
}

/**
 * Visit every instruction of one function in address order.
 *
 * The recorded bytecode size covers opcodes only; SwitchImm jump tables follow
 * it, so a linear sweep must end exactly at that size.
 */
export const forEachInstruction = (
  file: HermesBytecodeFile,
  header: HermesFunctionHeader,
  visit: (instruction: HermesInstruction) => void,
  budget?: HermesWorkBudget,
): void => {
  budget?.spend(header.bytecodeSizeInBytes);
  const { opcodes, sizes } = file.opcodeTable;
  const bytes = file.bytes;
  const end = header.offset + header.bytecodeSizeInBytes;
  let address = header.offset;
  while (address < end) {
    const code = bytes[address] ?? 0;
    const size = sizes[code] ?? 0;
    const opcode = opcodes[code];
    if (opcode === undefined || size === 0)
      return malformed(
        `Function ${String(header.id)} has undefined opcode ${String(code)} at offset ${String(address - header.offset)}.`,
      );
    if (size > end - address)
      return malformed(
        `Function ${String(header.id)} instruction at offset ${String(address - header.offset)} crosses the end of its bytecode.`,
      );
    visit({ offset: address - header.offset, opcode, address });
    address += size;
  }
};

/** Raw operand value; registers, indices and immediates are all numbers. */
export const operandValue = (
  bytes: Buffer,
  address: number,
  operand: HermesOperand,
): number => {
  const at = address + operand.offset;
  switch (operand.code) {
    case "r":
    case "b":
      return bytes.readUInt8(at);
    case "h":
      return bytes.readUInt16LE(at);
    case "R":
    case "w":
      return bytes.readUInt32LE(at);
    case "a":
      return bytes.readInt8(at);
    case "A":
    case "i":
      return bytes.readInt32LE(at);
    case "d":
      return bytes.readDoubleLE(at);
  }
};

const quoted = (file: HermesBytecodeFile, id: number): string => {
  if (id >= file.header.stringCount) return `string#${String(id)}?`;
  const length = file.stringLength(id);
  const text = JSON.stringify(file.stringAt(id, INLINE_STRING_UNITS));
  return length > INLINE_STRING_UNITS
    ? `${text}…+${String(length - INLINE_STRING_UNITS)}`
    : text;
};

const functionLabel = (file: HermesBytecodeFile, id: number): string => {
  if (id >= file.header.functionCount) return `fn#${String(id)}?`;
  const name = file.functionHeader(id).functionName;
  return file.stringLength(name) === 0
    ? `fn#${String(id)}`
    : `fn#${String(id)} ${quoted(file, name)}`;
};

const renderOperand = (
  file: HermesBytecodeFile,
  instruction: HermesInstruction,
  operand: HermesOperand,
): string => {
  const value = operandValue(file.bytes, instruction.address, operand);
  if (operand.target === "string") return quoted(file, value);
  if (operand.target === "function") return functionLabel(file, value);
  if (operand.target === "bigint") return `bigint#${String(value)}`;
  switch (operand.code) {
    case "r":
    case "R":
      return `r${String(value)}`;
    case "a":
    case "A":
      return `L${String(instruction.offset + value)}`;
    default:
      return String(value);
  }
};

/** Jump-table size of one SwitchImm and its leading targets. */
export interface HermesSwitchTargets {
  readonly total: number;
  /** Leading targets as offsets from the first opcode. */
  readonly leading: readonly number[];
}

/** Read at most `retained` jump-table targets; null when the table leaves the file. */
export const switchTargets = (
  file: HermesBytecodeFile,
  instruction: HermesInstruction,
  retained: number,
): HermesSwitchTargets | null => {
  const [, table, , minimum, maximum] = instruction.opcode.operands;
  if (table === undefined || minimum === undefined || maximum === undefined)
    return null;
  const bytes = file.bytes;
  const low = operandValue(bytes, instruction.address, minimum);
  const high = operandValue(bytes, instruction.address, maximum);
  const relative = operandValue(bytes, instruction.address, table);
  const start = (instruction.address + relative + 3) & ~3;
  const total = high - low + 1;
  if (total <= 0 || total > (file.header.fileLength - start) / 4) return null;
  const leading: number[] = [];
  for (let index = 0; index < Math.min(total, retained); index += 1)
    leading.push(instruction.offset + bytes.readInt32LE(start + index * 4));
  return { total, leading };
};

/** `Opcode operand, operand` with strings, functions and jump targets resolved. */
export const renderInstruction = (
  file: HermesBytecodeFile,
  instruction: HermesInstruction,
): string => {
  const { opcode } = instruction;
  const operands = opcode.operands.map((operand) =>
    renderOperand(file, instruction, operand),
  );
  const text =
    operands.length === 0
      ? opcode.name
      : `${opcode.name} ${operands.join(", ")}`;
  if (opcode.name !== "SwitchImm") return text;
  const targets = switchTargets(file, instruction, INLINE_SWITCH_TARGETS);
  if (targets === null) return `${text} [jump table outside the file]`;
  const shown = targets.leading.map((target) => `L${String(target)}`);
  const hidden = targets.total - shown.length;
  return `${text} [${shown.join(", ")}${hidden > 0 ? `, …+${String(hidden)}` : ""}]`;
};
