import { symlink, truncate, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, it } from "vitest";
import {
  AnalysisCancelledError,
  AnalysisInputError,
  AnalysisResourceConstraintError,
  AnalysisUnsupportedTargetError,
} from "../../../src/domain/analysisErrorCore.js";
import { projectAnalysisError } from "../../../src/domain/analysisErrorProjection.js";
import {
  hermesBytecodeSchema,
  hermesSearchSchema,
} from "../../../src/domain/hermes/hermesBytecode.js";
import {
  HERMES_INPUT_BYTES,
  HermesBytecodeProvider,
} from "../../../src/hermes/HermesBytecodeProvider.js";
import {
  createHermesBytecode,
  sampleHermesFixture,
} from "../../fixtures/hermes/bytecode.js";
import { createTestTempDirectory } from "../../fixtures/temporaryDirectory.js";

const writeSample = async (
  bytes = createHermesBytecode(sampleHermesFixture()),
): Promise<string> => {
  const root = await createTestTempDirectory("rea-hermes-provider-");
  const path = join(root, "index.android.bundle");
  await writeFile(path, bytes);
  return path;
};

it("reports file identity and one disassembly page", async () => {
  const path = await writeSample();
  const provider = new HermesBytecodeProvider();
  const summary = await provider.inspect({ path });
  expect(summary.ok).toBe(true);
  if (!summary.ok) throw summary.error;
  expect(hermesBytecodeSchema.parse(summary.value)).toMatchObject({
    artifact: { path },
    bytecode: { version: 96, decoder_verification: "real-bundle" },
    file: {
      footer_hash_matches: true,
      trailing_bytes: 0,
      counts: { functions: 3, strings: 10, identifiers: 4 },
    },
    disassembly: null,
  });
  const paged = await provider.inspect({
    path,
    function_id: 1,
    offset: 1,
    limit: 2,
  });
  expect(paged.ok).toBe(true);
  if (!paged.ok) throw paged.error;
  expect(paged.value.disassembly).toMatchObject({
    function: {
      function_id: 1,
      name: "login",
      param_count: 2,
      bytecode_bytes: 24,
      prohibited_invocation: "none",
      has_exception_handler: true,
      header: "small",
    },
    referenced_by: { function_ids: [0, 2], function_count: 2 },
    exception_handlers: [{ start: 0, end: 10, target: 22 }],
    instructions: {
      offset: 1,
      returned: 2,
      total: 5,
      next_offset: 3,
      lines: ['4\tGetById r1, r0, 1, "fetch"', "10\tJmp L12"],
    },
  });
  await provider.close();
});

it("marks source-derived versions and footer mismatches as limitations", async () => {
  const path = await writeSample(
    createHermesBytecode({ ...sampleHermesFixture(85), corruptFooter: true }),
  );
  const result = await new HermesBytecodeProvider().inspect({ path });
  expect(result.ok).toBe(true);
  if (!result.ok) throw result.error;
  expect(result.value.bytecode.decoder_verification).toBe("source-derived");
  expect(result.value.file.footer_hash_matches).toBe(false);
  expect(result.value.limitations.join("\n")).toMatch(
    /version 85 is generated from the upstream definitions[\s\S]*footer hash does not equal/u,
  );
});

it("searches strings with referrers and functions by name", async () => {
  const path = await writeSample();
  const provider = new HermesBytecodeProvider();
  const strings = await provider.search({
    path,
    target: "strings",
    contains: "LOGIN",
  });
  expect(strings.ok).toBe(true);
  if (!strings.ok) throw strings.error;
  expect(hermesSearchSchema.parse(strings.value)).toMatchObject({
    page: { offset: 0, returned: 2, total: 2, next_offset: null },
    strings: [
      {
        string_id: 2,
        kind: "string",
        value: "https://api.example.test/login",
        referenced_by: { function_ids: [1], function_count: 1 },
        names_functions: { function_ids: [], function_count: 0 },
      },
      {
        string_id: 7,
        kind: "identifier",
        value: "login",
        referenced_by: { function_ids: [], function_count: 0 },
        names_functions: { function_ids: [1], function_count: 1 },
      },
    ],
    functions: null,
  });
  const exact = await provider.search({
    path,
    target: "strings",
    contains: "LOGIN",
    match_case: true,
  });
  expect(exact.ok && exact.value.page.total).toBe(0);
  const truncated = await provider.search({
    path,
    target: "strings",
    string_kind: "string",
    offset: 5,
    limit: 1,
    max_value_units: 4,
  });
  expect(truncated.ok).toBe(true);
  if (!truncated.ok) throw truncated.error;
  expect(truncated.value).toMatchObject({
    page: { offset: 5, returned: 1, total: 6, next_offset: null },
    strings: [
      {
        string_id: 5,
        utf16_length: 300,
        value: "xxxx",
        value_truncated: true,
        referenced_by: { function_ids: [2], function_count: 1 },
      },
    ],
  });
  const functions = await provider.search({
    path,
    target: "functions",
    limit: 2,
  });
  expect(functions.ok).toBe(true);
  if (!functions.ok) throw functions.error;
  expect(functions.value).toMatchObject({
    page: { offset: 0, returned: 2, total: 3, next_offset: 2 },
    strings: null,
    functions: [
      { function_id: 0, name: "global" },
      { function_id: 1, name: "login" },
    ],
  });
});

it("rejects selections the file does not contain", async () => {
  const path = await writeSample();
  const provider = new HermesBytecodeProvider();
  for (const [input, field] of [
    [{ path, function_id: 3 }, "function_id"],
    [{ path, function_id: 1, offset: 6 }, "offset"],
  ] as const) {
    const result = await provider.inspect(input);
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("Expected a selection failure");
    expect(result.error).toBeInstanceOf(AnalysisInputError);
    expect(projectAnalysisError(result.error).details).toMatchObject({
      issues: [{ path: [field], reason: "invalid_value" }],
    });
  }
  const end = await provider.inspect({ path, function_id: 1, offset: 5 });
  expect(end.ok && end.value.disassembly?.instructions).toMatchObject({
    returned: 0,
    total: 5,
    next_offset: null,
  });
});

it("classifies foreign, malformed, missing and linked inputs", async () => {
  const provider = new HermesBytecodeProvider();
  const plain = await writeSample(Buffer.from("var __BUNDLE_START_TIME__;"));
  const unsupported = await provider.inspect({ path: plain });
  expect(unsupported.ok).toBe(false);
  if (!unsupported.ok)
    expect(unsupported.error).toBeInstanceOf(AnalysisUnsupportedTargetError);
  const bytes = createHermesBytecode(sampleHermesFixture());
  const cut = await writeSample(bytes.subarray(0, bytes.length - 40));
  const malformed = await provider.search({ path: cut, target: "strings" });
  expect(malformed.ok).toBe(false);
  if (malformed.ok) throw new Error("Expected a malformed-input failure");
  expect(projectAnalysisError(malformed.error).details).toMatchObject({
    operation: "search_hermes_bytecode",
    issues: [{ path: ["path"], reason: "invalid_format" }],
  });
  const missing = await provider.inspect({ path: `${plain}.missing` });
  expect(missing.ok).toBe(false);
  if (!missing.ok) expect(missing.error).toBeInstanceOf(AnalysisInputError);
  const link = `${plain}.link`;
  await symlink(await writeSample(), link);
  const linked = await provider.inspect({ path: link });
  expect(linked.ok).toBe(false);
  if (!linked.ok) expect(linked.error).toBeInstanceOf(AnalysisInputError);
});

it("refuses oversized inputs and oversized pages with a usable remedy", async () => {
  const provider = new HermesBytecodeProvider();
  const large = await writeSample();
  await truncate(large, HERMES_INPUT_BYTES + 1);
  const input = await provider.inspect({ path: large });
  expect(input.ok).toBe(false);
  if (input.ok) throw new Error("Expected an input-size refusal");
  expect(projectAnalysisError(input.error)).toMatchObject({
    retryable: false,
    details: {
      resource: "file-size",
      reported_limits: { maximum_input_bytes: HERMES_INPUT_BYTES },
    },
  });
  const fixture = sampleHermesFixture();
  const wide = await writeSample(
    createHermesBytecode({
      ...fixture,
      strings: [
        ...fixture.strings,
        ...Array.from({ length: 200 }, () => "y".repeat(60_000)),
      ],
    }),
  );
  const page = await provider.search({
    path: wide,
    target: "strings",
    contains: "yyyy",
    limit: 200,
    max_value_units: 65_536,
  });
  expect(page.ok).toBe(false);
  if (page.ok) throw new Error("Expected an output-size refusal");
  expect(page.error).toBeInstanceOf(AnalysisResourceConstraintError);
  const projected = projectAnalysisError(page.error);
  expect(projected).toMatchObject({
    retryable: false,
    details: {
      resource: "transport",
      reported_limits: { maximum_output_bytes: 8 * 1024 * 1024 },
    },
  });
  expect(projected.remediation.action).toContain("max_value_units");
});

it("reports cancellation during a search as cancellation", async () => {
  const path = await writeSample();
  const controller = new AbortController();
  controller.abort(new Error("caller left"));
  const result = await new HermesBytecodeProvider().search(
    { path, target: "strings" },
    { signal: controller.signal },
  );
  expect(result.ok).toBe(false);
  if (!result.ok) expect(result.error).toBeInstanceOf(AnalysisCancelledError);
});

it("refuses shared-body amplification as a fixed work allowance", async () => {
  const path = await writeSample(
    createHermesBytecode({
      strings: ["", "value"],
      functions: [
        {
          name: 0,
          code: Array.from({ length: 16_000 }, () => ["Ret", 0] as const),
        },
        ...Array.from({ length: 600 }, () => ({
          name: 0,
          code: [],
          sameBodyAs: 0,
        })),
      ],
    }),
  );
  const provider = new HermesBytecodeProvider();
  const identity = await provider.inspect({ path });
  expect(identity.ok && identity.value.file.counts.functions).toBe(601);
  const result = await provider.search({
    path,
    target: "strings",
    contains: "value",
  });
  expect(result.ok).toBe(false);
  if (result.ok) throw new Error("Expected a work-allowance refusal");
  expect(result.error).toBeInstanceOf(AnalysisResourceConstraintError);
  const projected = projectAnalysisError(result.error);
  expect(projected).toMatchObject({
    retryable: false,
    details: {
      resource: "cpu",
      reported_limits: { maximum_decoded_bytes: 16 * 1024 * 1024 },
    },
  });
  expect(projected.remediation.action).toContain("fixed work allowance");
});
