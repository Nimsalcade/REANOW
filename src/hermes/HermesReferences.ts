import type { HermesBytecodeFile } from "./HermesBytecodeFile.js";
import { forEachInstruction, operandValue } from "./HermesInstructions.js";
import { visitLiteralStringIds } from "./HermesLiterals.js";
import type { HermesOpcode, HermesOperand } from "./HermesOpcodes.js";
import type { HermesWorkBudget } from "./HermesWorkBudget.js";

/** Distinct referencing functions in ascending id order, with the full count. */
export interface HermesReferrers {
  readonly functions: number[];
  readonly count: number;
}

/** Referrers of the requested strings and of one requested function. */
export interface HermesReferenceScan {
  readonly strings: ReadonlyMap<number, HermesReferrers>;
  readonly functionReferrers: HermesReferrers;
  /** Literal-buffer instructions whose literals left their buffer. */
  readonly unreadableLiteralBuffers: number;
}

interface LiteralUse {
  readonly section:
    | "array_buffer"
    | "object_key_buffer"
    | "object_value_buffer";
  readonly count: HermesOperand;
  readonly index: HermesOperand;
}

interface OpcodePlan {
  readonly strings: readonly HermesOperand[];
  readonly functions: readonly HermesOperand[];
  readonly literals: readonly LiteralUse[];
}

/** Operands 2 and 3 are the literal count and the first buffer index. */
const literalUses = (opcode: HermesOpcode): LiteralUse[] => {
  const [, , count, first, second] = opcode.operands;
  if (count === undefined || first === undefined) return [];
  switch (opcode.name) {
    case "NewArrayWithBuffer":
    case "NewArrayWithBufferLong":
      return [{ section: "array_buffer", count, index: first }];
    case "NewObjectWithBuffer":
    case "NewObjectWithBufferLong":
      return second === undefined
        ? []
        : [
            { section: "object_key_buffer", count, index: first },
            { section: "object_value_buffer", count, index: second },
          ];
    default:
      return [];
  }
};

const planFor = (opcode: HermesOpcode): OpcodePlan | null => {
  const strings = opcode.operands.filter(
    (operand) => operand.target === "string",
  );
  const functions = opcode.operands.filter(
    (operand) => operand.target === "function",
  );
  const literals = literalUses(opcode);
  return strings.length + functions.length + literals.length === 0
    ? null
    : { strings, functions, literals };
};

/** Functions are swept in ascending id order, so one cursor deduplicates. */
interface CountingReferrers {
  readonly functions: number[];
  count: number;
  last: number;
}

const record = (
  referrers: CountingReferrers,
  functionId: number,
  retained: number,
): void => {
  if (referrers.last === functionId) return;
  referrers.last = functionId;
  referrers.count += 1;
  if (referrers.functions.length < retained)
    referrers.functions.push(functionId);
};

/**
 * Sweep every function once and attribute string and function operands.
 *
 * String references cover instruction operands and the literals that
 * NewArrayWithBuffer and NewObjectWithBuffer read from the literal buffers.
 */
export const scanReferences = (
  file: HermesBytecodeFile,
  wanted: {
    readonly strings: ReadonlySet<number>;
    readonly functionId: number | null;
    readonly retained: number;
  },
  control: {
    readonly budget: HermesWorkBudget;
    readonly signal?: AbortSignal | undefined;
  },
): HermesReferenceScan => {
  const { budget, signal } = control;
  const bytes = file.bytes;
  const plans = file.opcodeTable.opcodes.map(planFor);
  const strings = new Map<number, CountingReferrers>();
  for (const id of wanted.strings)
    strings.set(id, { functions: [], count: 0, last: -1 });
  const functionReferrers: CountingReferrers = {
    functions: [],
    count: 0,
    last: -1,
  };
  let unreadableLiteralBuffers = 0;
  const sectionRange = (name: LiteralUse["section"]) => {
    const section = file.sections.find((entry) => entry.name === name);
    const start = section?.offset ?? 0;
    return { start, end: start + (section?.bytes ?? 0) };
  };
  const ranges = {
    array_buffer: sectionRange("array_buffer"),
    object_key_buffer: sectionRange("object_key_buffer"),
    object_value_buffer: sectionRange("object_value_buffer"),
  };
  for (let id = 0; id < file.header.functionCount; id += 1) {
    if ((id & 0x3ff) === 0) signal?.throwIfAborted();
    const header = file.functionHeader(id);
    const noteString = (stringId: number): void => {
      const referrers = strings.get(stringId);
      if (referrers !== undefined) record(referrers, id, wanted.retained);
    };
    forEachInstruction(
      file,
      header,
      (instruction) => {
        const plan = plans[bytes[instruction.address] ?? 0];
        if (plan === null || plan === undefined) return;
        if (strings.size > 0) {
          for (const operand of plan.strings)
            noteString(operandValue(bytes, instruction.address, operand));
          for (const use of plan.literals) {
            const consumed = visitLiteralStringIds(
              bytes,
              ranges[use.section],
              {
                offset: operandValue(bytes, instruction.address, use.index),
                count: operandValue(bytes, instruction.address, use.count),
              },
              noteString,
            );
            if (consumed < 0) unreadableLiteralBuffers += 1;
            else budget.spend(consumed);
          }
        }
        if (wanted.functionId !== null)
          for (const operand of plan.functions)
            if (
              operandValue(bytes, instruction.address, operand) ===
              wanted.functionId
            )
              record(functionReferrers, id, wanted.retained);
      },
      budget,
    );
  }
  return { strings, functionReferrers, unreadableLiteralBuffers };
};
