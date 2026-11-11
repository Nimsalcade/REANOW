import { z } from "zod";
import { digestSchema } from "../digests.js";
import { localPathStringSchema } from "../localPath.js";

/**
 * Released bytecode versions whose decoding was checked against bundles
 * produced by a real Hermes compiler. Other decoded versions are derived from
 * the same upstream definitions and have not been checked that way.
 */
export const HERMES_REAL_BUNDLE_VERSIONS = [
  59, 62, 74, 76, 84, 89, 90, 94, 96,
] as const;

/** Largest instruction page of one function disassembly. */
export const HERMES_INSTRUCTION_PAGE_LIMIT = 2000;
/** Largest page of string or function matches. */
export const HERMES_MATCH_PAGE_LIMIT = 500;
/** Largest number of leading UTF-16 code units returned for one string. */
export const HERMES_VALUE_UNIT_LIMIT = 65_536;
/** Referencing functions listed per string or function; the count is exact. */
export const HERMES_REFERRER_LIMIT = 32;

const count = z.number().int().nonnegative();
const sha1 = z.string().regex(/^[0-9a-f]{40}$/u);
const gitCommit = z.string().regex(/^[0-9a-f]{40}$/u);
const pathInput = localPathStringSchema.describe(
  "Absolute filesystem path to a Hermes bytecode file, such as an extracted index.android.bundle or main.jsbundle",
);

/** One explicit file; a function id selects its disassembly page. */
export const inspectHermesBytecodeInputSchema = z
  .strictObject({
    path: pathInput,
    function_id: count
      .optional()
      .describe(
        "Function table index to disassemble; omit for file identity, counts and table layout only",
      ),
    offset: count
      .optional()
      .describe(
        "Zero-based index of the first instruction to return, default 0; requires function_id",
      ),
    limit: z
      .number()
      .int()
      .min(1)
      .max(HERMES_INSTRUCTION_PAGE_LIMIT)
      .optional()
      .describe(
        `Instructions to return, default 400, at most ${String(HERMES_INSTRUCTION_PAGE_LIMIT)}; requires function_id`,
      ),
  })
  .superRefine((input, context) => {
    for (const field of ["offset", "limit"] as const)
      if (input[field] !== undefined && input.function_id === undefined)
        context.addIssue({
          code: "custom",
          path: [field],
          message: `${field} requires an explicit function_id selection`,
        });
  })
  .meta({
    dependentRequired: { offset: ["function_id"], limit: ["function_id"] },
  });

/** Candidate discovery over the string table or the function table. */
export const searchHermesBytecodeInputSchema = z
  .strictObject({
    path: pathInput,
    target: z
      .enum(["strings", "functions"])
      .describe(
        "strings searches string-table values and lists the functions that reference each match; functions searches function names",
      ),
    contains: z
      .string()
      .min(1)
      .optional()
      .describe(
        "Substring a string value or function name must contain; omit to page through every entry",
      ),
    match_case: z
      .boolean()
      .optional()
      .describe("Compare contains case-sensitively; default false"),
    string_kind: z
      .enum(["string", "identifier", "predefined"])
      .optional()
      .describe(
        "Restrict a strings search to one recorded string kind; identifier marks property and variable names",
      ),
    offset: count
      .optional()
      .describe("Zero-based index of the first match to return, default 0"),
    limit: z
      .number()
      .int()
      .min(1)
      .max(HERMES_MATCH_PAGE_LIMIT)
      .optional()
      .describe(
        `Matches to return, default 50, at most ${String(HERMES_MATCH_PAGE_LIMIT)}`,
      ),
    max_value_units: z
      .number()
      .int()
      .min(1)
      .max(HERMES_VALUE_UNIT_LIMIT)
      .optional()
      .describe(
        `Leading UTF-16 code units returned per string value, default 240, at most ${String(HERMES_VALUE_UNIT_LIMIT)}`,
      ),
  })
  .superRefine((input, context) => {
    if (input.string_kind !== undefined && input.target !== "strings")
      context.addIssue({
        code: "custom",
        path: ["string_kind"],
        message: "string_kind applies only to target strings",
      });
  });

const artifactSchema = z.strictObject({
  path: localPathStringSchema,
  sha256: digestSchema,
  bytes: count,
});

/** Version identity and how its decoding tables were established. */
const bytecodeIdentitySchema = z.strictObject({
  version: count,
  /** Upstream source the opcode layouts of this version were generated from. */
  opcode_source: z.strictObject({
    repository: z.literal("https://github.com/facebook/hermes"),
    commit: gitCommit,
    ref: z.string().min(1),
  }),
  /**
   * real-bundle: this version's decoding was checked against compiler output.
   * source-derived: generated from upstream definitions only.
   */
  decoder_verification: z.enum(["real-bundle", "source-derived"]),
});

const sectionSchema = z.strictObject({
  name: z.string().min(1),
  offset: count,
  bytes: count,
});

/** Header facts of one file; null marks a field the version does not store. */
export const hermesFileSummarySchema = z.strictObject({
  /** SHA-1 of the JavaScript source, as recorded by the compiler. */
  source_hash: sha1,
  file_length: count,
  /** Bytes after the recorded file length, such as an appended epilogue. */
  trailing_bytes: count,
  footer_hash: sha1.nullable(),
  /** Whether the footer equals the SHA-1 of the preceding bytes. */
  footer_hash_matches: z.boolean().nullable(),
  global_function_id: count,
  segment_id: count.nullable(),
  cjs_module_offset: count.nullable(),
  debug_info_offset: count,
  options: z.strictObject({
    static_builtins: z.boolean(),
    cjs_modules_statically_resolved: z.boolean(),
    has_async: z.boolean().nullable(),
  }),
  counts: z.strictObject({
    functions: count,
    strings: count,
    identifiers: count,
    overflow_strings: count,
    big_ints: count.nullable(),
    reg_exps: count,
    cjs_modules: count,
    function_sources: count.nullable(),
  }),
  sections: z.array(sectionSchema),
});

/** Function-table row shared by search results and disassembly. */
export const hermesFunctionSchema = z.strictObject({
  function_id: count,
  name: z.string(),
  param_count: count,
  bytecode_offset: count,
  bytecode_bytes: count,
  frame_size: count,
  environment_size: count,
  strict_mode: z.boolean(),
  /** Invocation kinds the header prohibits; null for an unknown encoding. */
  prohibited_invocation: z.enum(["call", "construct", "none"]).nullable(),
  has_exception_handler: z.boolean(),
  has_debug_info: z.boolean(),
  header: z.enum(["small", "large"]),
});

const referrersSchema = z.strictObject({
  /** Distinct functions in ascending id order, capped at the referrer limit. */
  function_ids: z.array(count),
  /** Exact number of distinct referencing functions. */
  function_count: count,
});

const pageSchema = z.strictObject({
  offset: count,
  returned: count,
  total: count,
  next_offset: count.nullable(),
});

/** One function's instructions as `byte_offset<TAB>Opcode operands` lines. */
export const hermesDisassemblySchema = z.strictObject({
  function: hermesFunctionSchema,
  /** Functions with a closure-creating or direct-call operand naming this one. */
  referenced_by: referrersSchema,
  exception_handlers: z.array(
    z.strictObject({ start: count, end: count, target: count }),
  ),
  instructions: pageSchema.extend({ lines: z.array(z.string()) }),
});

/** File identity, with one disassembly page when a function was selected. */
export const hermesBytecodeSchema = z.strictObject({
  artifact: artifactSchema,
  bytecode: bytecodeIdentitySchema,
  file: hermesFileSummarySchema,
  disassembly: hermesDisassemblySchema.nullable(),
  limitations: z.array(z.string()),
});

const stringMatchSchema = z.strictObject({
  string_id: count,
  kind: z.enum(["string", "identifier", "predefined"]),
  /** Complete length in UTF-16 code units. */
  utf16_length: count,
  value: z.string(),
  value_truncated: z.boolean(),
  /** Functions with an instruction or literal that uses this string. */
  referenced_by: referrersSchema,
  /** Functions whose compiler-recorded name is this string. */
  names_functions: referrersSchema,
});

/** One page of matches in ascending table order. */
export const hermesSearchSchema = z
  .strictObject({
    artifact: artifactSchema,
    bytecode: bytecodeIdentitySchema,
    target: z.enum(["strings", "functions"]),
    page: pageSchema,
    strings: z.array(stringMatchSchema).nullable(),
    functions: z.array(hermesFunctionSchema).nullable(),
    limitations: z.array(z.string()),
  })
  .superRefine((result, context) => {
    const rows =
      result.target === "strings" ? result.strings : result.functions;
    const other =
      result.target === "strings" ? result.functions : result.strings;
    if (rows === null || other !== null || rows.length !== result.page.returned)
      context.addIssue({
        code: "custom",
        path: ["page", "returned"],
        message: "Returned rows do not match the selected target and page.",
      });
  });

export type InspectHermesBytecodeInput = z.infer<
  typeof inspectHermesBytecodeInputSchema
>;
export type SearchHermesBytecodeInput = z.infer<
  typeof searchHermesBytecodeInputSchema
>;
export type HermesBytecode = z.infer<typeof hermesBytecodeSchema>;
export type HermesFileSummary = z.infer<typeof hermesFileSummarySchema>;
export type HermesFunction = z.infer<typeof hermesFunctionSchema>;
export type HermesDisassembly = z.infer<typeof hermesDisassemblySchema>;
export type HermesSearch = z.infer<typeof hermesSearchSchema>;
