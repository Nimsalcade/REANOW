import {
  HERMES_REAL_BUNDLE_VERSIONS,
  HERMES_REFERRER_LIMIT,
  type HermesBytecode,
  type HermesDisassembly,
  type HermesFileSummary,
  type HermesFunction,
  type HermesSearch,
  type InspectHermesBytecodeInput,
  type SearchHermesBytecodeInput,
} from "../domain/hermes/hermesBytecode.js";
import { HermesSelectionFailure } from "./HermesBytecodeFailures.js";
import type {
  HermesBytecodeFile,
  HermesFunctionHeader,
} from "./HermesBytecodeFile.js";
import { forEachInstruction, renderInstruction } from "./HermesInstructions.js";
import { HERMES_SUPPORTED_VERSIONS } from "./HermesOpcodes.js";
import { scanReferences, type HermesReferrers } from "./HermesReferences.js";
import { HermesWorkBudget } from "./HermesWorkBudget.js";

const DEFAULT_INSTRUCTION_LIMIT = 400;
const DEFAULT_MATCH_LIMIT = 50;
const DEFAULT_VALUE_UNITS = 240;
const PROHIBITED_INVOCATIONS = ["call", "construct", "none"] as const;

type Artifact = HermesBytecode["artifact"];
type Identity = HermesBytecode["bytecode"];

const isRealBundleVersion = (version: number): boolean =>
  HERMES_REAL_BUNDLE_VERSIONS.some((verified) => verified === version);

const identityOf = (file: HermesBytecodeFile): Identity => ({
  version: file.header.version,
  opcode_source: {
    repository: "https://github.com/facebook/hermes",
    commit: file.opcodeTable.source.commit,
    ref: file.opcodeTable.source.ref,
  },
  decoder_verification: isRealBundleVersion(file.header.version)
    ? "real-bundle"
    : "source-derived",
});

const summaryOf = (file: HermesBytecodeFile): HermesFileSummary => {
  const header = file.header;
  const footer = file.footerHash;
  const segment = header.segmentField === "segment_id";
  return {
    source_hash: header.sourceHash,
    file_length: header.fileLength,
    trailing_bytes: file.trailingBytes,
    footer_hash: footer,
    footer_hash_matches:
      footer === null ? null : footer === file.computeContentHash(),
    global_function_id: header.globalCodeIndex,
    segment_id: segment ? header.segmentValue : null,
    cjs_module_offset: segment ? null : header.segmentValue,
    debug_info_offset: header.debugInfoOffset,
    options: {
      static_builtins: header.staticBuiltins,
      cjs_modules_statically_resolved: header.cjsModulesStaticallyResolved,
      has_async: header.hasAsync,
    },
    counts: {
      functions: header.functionCount,
      strings: header.stringCount,
      identifiers: header.identifierCount,
      overflow_strings: header.overflowStringCount,
      big_ints: header.bigIntCount,
      reg_exps: header.regExpCount,
      cjs_modules: header.cjsModuleCount,
      function_sources: header.functionSourceCount,
    },
    sections: file.sections.map((section) => ({ ...section })),
  };
};

const functionRow = (
  file: HermesBytecodeFile,
  header: HermesFunctionHeader,
): HermesFunction => ({
  function_id: header.id,
  name: file.stringAt(header.functionName),
  param_count: header.paramCount,
  bytecode_offset: header.offset,
  bytecode_bytes: header.bytecodeSizeInBytes,
  frame_size: header.frameSize,
  environment_size: header.environmentSize,
  strict_mode: header.strictMode,
  prohibited_invocation: PROHIBITED_INVOCATIONS[header.prohibitInvoke] ?? null,
  has_exception_handler: header.hasExceptionHandler,
  has_debug_info: header.hasDebugInfo,
  header: header.overflowed ? "large" : "small",
});

const referrersOf = (referrers: HermesReferrers | undefined) => ({
  function_ids: [...(referrers?.functions ?? [])],
  function_count: referrers?.count ?? 0,
});

const pageOf = (offset: number, returned: number, total: number) => ({
  offset,
  returned,
  total,
  next_offset: offset + returned < total ? offset + returned : null,
});

const versionLimitations = (file: HermesBytecodeFile): string[] => [
  "Bytecode is file content. Strings, names and hashes are untrusted data from the selected file and do not authenticate the application or its build.",
  `Decoded released bytecode versions are ${HERMES_SUPPORTED_VERSIONS.join(", ")}. Versions 98 and later use a different file layout and are not decoded.`,
  ...(isRealBundleVersion(file.header.version)
    ? []
    : [
        `Decoding of bytecode version ${String(file.header.version)} is generated from the upstream definitions and was not checked against output of a real compiler for that version.`,
      ]),
  "Opcodes that exist only in compilers built with HERMES_RUN_WASM are not in the tables; a file that uses them is reported as malformed.",
];

const footerLimitation = (summary: HermesFileSummary): string[] =>
  summary.footer_hash_matches === false
    ? [
        "The footer hash does not equal the SHA-1 of the preceding bytes, so the file changed after the compiler wrote it or the footer was not filled in.",
      ]
    : [];

const disassemble = (
  file: HermesBytecodeFile,
  input: InspectHermesBytecodeInput & { readonly function_id: number },
  signal?: AbortSignal,
): HermesDisassembly => {
  if (input.function_id >= file.header.functionCount)
    throw new HermesSelectionFailure(
      "function_id",
      `function_id ${String(input.function_id)} is outside the function table of ${String(file.header.functionCount)} entries.`,
    );
  const header = file.functionHeader(input.function_id);
  const offset = input.offset ?? 0;
  const limit = input.limit ?? DEFAULT_INSTRUCTION_LIMIT;
  const budget = new HermesWorkBudget(file.header.fileLength);
  const lines: string[] = [];
  let total = 0;
  forEachInstruction(
    file,
    header,
    (instruction) => {
      if (total >= offset && lines.length < limit)
        lines.push(
          `${String(instruction.offset)}\t${renderInstruction(file, instruction)}`,
        );
      total += 1;
    },
    budget,
  );
  if (offset > total)
    throw new HermesSelectionFailure(
      "offset",
      `offset ${String(offset)} is past the ${String(total)} instructions of function ${String(header.id)}.`,
    );
  const scan = scanReferences(
    file,
    {
      strings: new Set(),
      functionId: header.id,
      retained: HERMES_REFERRER_LIMIT,
    },
    { budget, signal },
  );
  return {
    function: functionRow(file, header),
    referenced_by: referrersOf(scan.functionReferrers),
    exception_handlers: file.exceptionHandlers(header),
    instructions: { ...pageOf(offset, lines.length, total), lines },
  };
};

/** File identity and layout, plus one disassembly page when selected. */
export const inspectHermesFile = (
  file: HermesBytecodeFile,
  artifact: Artifact,
  input: InspectHermesBytecodeInput,
  signal?: AbortSignal,
): HermesBytecode => {
  const summary = summaryOf(file);
  const { function_id: functionId } = input;
  return {
    artifact,
    bytecode: identityOf(file),
    file: summary,
    disassembly:
      functionId === undefined
        ? null
        : disassemble(file, { ...input, function_id: functionId }, signal),
    limitations: [
      ...versionLimitations(file),
      ...footerLimitation(summary),
      "Disassembly lists register-machine instructions in address order. It does not reconstruct JavaScript source, control-flow structure, variable names or types.",
      "Jump targets are rendered as L<byte offset> within the function. A referenced_by list names functions with a closure-creating or direct-call operand for the selected function; functions reached only through calls on runtime values, such as the global function, have none.",
      "Debug information, regular-expression bytecode, bigint values and the CommonJS module table are located in sections but are not decoded.",
    ],
  };
};

const matcher = (
  input: SearchHermesBytecodeInput,
): ((value: string) => boolean) => {
  const { contains } = input;
  if (contains === undefined) return () => true;
  if (input.match_case === true) return (value) => value.includes(contains);
  const needle = contains.toLowerCase();
  return (value) => value.toLowerCase().includes(needle);
};

/** Collect one page of matching ids and the total match count. */
const pageIds = (
  entries: number,
  input: SearchHermesBytecodeInput,
  matches: (id: number) => boolean,
  signal?: AbortSignal,
): { readonly ids: number[]; readonly total: number } => {
  const offset = input.offset ?? 0;
  const limit = input.limit ?? DEFAULT_MATCH_LIMIT;
  const ids: number[] = [];
  let total = 0;
  for (let id = 0; id < entries; id += 1) {
    if ((id & 0xfff) === 0) signal?.throwIfAborted();
    if (!matches(id)) continue;
    if (total >= offset && ids.length < limit) ids.push(id);
    total += 1;
  }
  return { ids, total };
};

/** Functions whose recorded name is one of the selected strings. */
const namedFunctions = (
  file: HermesBytecodeFile,
  ids: readonly number[],
): Map<number, { functions: number[]; count: number }> => {
  const named = new Map(
    ids.map((id) => [id, { functions: [] as number[], count: 0 }]),
  );
  if (named.size === 0) return named;
  for (let id = 0; id < file.header.functionCount; id += 1) {
    const entry = named.get(file.functionHeader(id).functionName);
    if (entry === undefined) continue;
    entry.count += 1;
    if (entry.functions.length < HERMES_REFERRER_LIMIT)
      entry.functions.push(id);
  }
  return named;
};

const searchStrings = (
  file: HermesBytecodeFile,
  input: SearchHermesBytecodeInput,
  signal?: AbortSignal,
) => {
  const matches = matcher(input);
  const { ids, total } = pageIds(
    file.header.stringCount,
    input,
    (id) =>
      (input.string_kind === undefined ||
        file.stringKind(id) === input.string_kind) &&
      matches(file.stringAt(id)),
    signal,
  );
  const scan = scanReferences(
    file,
    {
      strings: new Set(ids),
      functionId: null,
      retained: HERMES_REFERRER_LIMIT,
    },
    { budget: new HermesWorkBudget(file.header.fileLength), signal },
  );
  const named = namedFunctions(file, ids);
  const units = input.max_value_units ?? DEFAULT_VALUE_UNITS;
  const rows = ids.map((id) => {
    const length = file.stringLength(id);
    return {
      string_id: id,
      kind: file.stringKind(id),
      utf16_length: length,
      value: file.stringAt(id, units),
      value_truncated: length > units,
      referenced_by: referrersOf(scan.strings.get(id)),
      names_functions: referrersOf(named.get(id)),
    };
  });
  return { rows, total, unreadable: scan.unreadableLiteralBuffers };
};

const searchFunctions = (
  file: HermesBytecodeFile,
  input: SearchHermesBytecodeInput,
  signal?: AbortSignal,
) => {
  const matches = matcher(input);
  const { ids, total } = pageIds(
    file.header.functionCount,
    input,
    (id) => matches(file.stringAt(file.functionHeader(id).functionName)),
    signal,
  );
  return {
    rows: ids.map((id) => functionRow(file, file.functionHeader(id))),
    total,
  };
};

/** One page of string or function matches in ascending table order. */
export const searchHermesFile = (
  file: HermesBytecodeFile,
  artifact: Artifact,
  input: SearchHermesBytecodeInput,
  signal?: AbortSignal,
): HermesSearch => {
  const offset = input.offset ?? 0;
  const common = { artifact, bytecode: identityOf(file), target: input.target };
  if (input.target === "functions") {
    const { rows, total } = searchFunctions(file, input, signal);
    return {
      ...common,
      page: pageOf(offset, rows.length, total),
      strings: null,
      functions: rows,
      limitations: [
        ...versionLimitations(file),
        "Function names are the names recorded by the compiler. Minified bundles leave most functions unnamed, so an empty name is common and does not mean the function is unused.",
      ],
    };
  }
  const { rows, total, unreadable } = searchStrings(file, input, signal);
  return {
    ...common,
    page: pageOf(offset, rows.length, total),
    strings: rows,
    functions: null,
    limitations: [
      ...versionLimitations(file),
      "referenced_by covers string operands of instructions and the literals read by NewArrayWithBuffer and NewObjectWithBuffer; names_functions lists functions whose recorded name is the string. A string used only inside a regular expression or built at runtime has neither, so empty lists are not proof that the string is unused.",
      `Each function list holds at most ${String(HERMES_REFERRER_LIMIT)} function ids in ascending order; function_count is the exact number of distinct referencing functions.`,
      ...(unreadable > 0
        ? [
            `${String(unreadable)} literal-buffer instructions named literals outside their buffer; strings reached only through them have no referrer.`,
          ]
        : []),
    ],
  };
};
