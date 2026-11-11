import { expect, it } from "vitest";
import {
  AnalysisCancelledError,
  AnalysisInputError,
  AnalysisOutputError,
} from "../../domain/analysisErrorCore.js";
import type {
  HermesBytecode,
  HermesSearch,
} from "../../domain/hermes/hermesBytecode.js";
import { ok } from "../../domain/result.js";
import { HermesBytecodeService } from "./HermesBytecodeService.js";

const identity = {
  id: "fixture-hermes",
  name: "Source-owned Hermes reader seam",
  version: "1",
};
const artifact = {
  path: "/artifacts/index.android.bundle",
  sha256: "a".repeat(64),
  bytes: 512,
};
const bytecode = {
  version: 96,
  opcode_source: {
    repository: "https://github.com/facebook/hermes",
    commit: "b".repeat(40),
    ref: "rn/0.76-stable",
  },
  decoder_verification: "real-bundle",
} as const;
const report: HermesBytecode = {
  artifact,
  bytecode,
  file: {
    source_hash: "c".repeat(40),
    file_length: 512,
    trailing_bytes: 0,
    footer_hash: "d".repeat(40),
    footer_hash_matches: true,
    global_function_id: 0,
    segment_id: 0,
    cjs_module_offset: null,
    debug_info_offset: 400,
    options: {
      static_builtins: false,
      cjs_modules_statically_resolved: false,
      has_async: false,
    },
    counts: {
      functions: 1,
      strings: 1,
      identifiers: 0,
      overflow_strings: 0,
      big_ints: 0,
      reg_exps: 0,
      cjs_modules: 0,
      function_sources: 0,
    },
    sections: [{ name: "function_headers", offset: 128, bytes: 16 }],
  },
  disassembly: null,
  limitations: ["Bytecode is file content."],
};
const search: HermesSearch = {
  artifact,
  bytecode,
  target: "strings",
  page: { offset: 0, returned: 0, total: 0, next_offset: null },
  strings: [],
  functions: null,
  limitations: [],
};

const serviceWith = (
  overrides: Partial<{
    inspect: HermesBytecode;
    search: HermesSearch;
    onCall: () => void;
  }> = {},
) =>
  new HermesBytecodeService({
    identity,
    inspect: () => {
      overrides.onCall?.();
      return Promise.resolve(ok(overrides.inspect ?? report));
    },
    search: () => {
      overrides.onCall?.();
      return Promise.resolve(ok(overrides.search ?? search));
    },
  });

it("rejects invalid selections before reading the file", async () => {
  let calls = 0;
  const service = serviceWith({ onCall: () => (calls += 1) });
  const rejected = await Promise.all([
    service.inspect({ path: "index.android.bundle" }),
    service.inspect({ path: artifact.path, offset: 4 }),
    service.inspect({ path: artifact.path, function_id: 0, limit: 2001 }),
    service.inspect({ path: artifact.path, detail: "complete" }),
    service.search({ path: artifact.path }),
    service.search({ path: artifact.path, target: "strings", contains: "" }),
    service.search({
      path: artifact.path,
      target: "functions",
      string_kind: "identifier",
    }),
  ]);
  for (const result of rejected) {
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBeInstanceOf(AnalysisInputError);
  }
  expect(calls).toBe(0);
});

it("binds Evidence to the selected artifact and explicit parameters", async () => {
  const result = await serviceWith().inspect({ path: artifact.path });
  expect(result.ok).toBe(true);
  if (!result.ok) throw result.error;
  expect(result.value).toMatchObject({
    operation: "inspect_hermes_bytecode",
    confidence: "observed",
    subject: { format: "file", digest: { sha256: artifact.sha256 } },
    parameters: { path: artifact.path },
    normalized_result: report,
    limitations: report.limitations,
    locations: [{ kind: "artifact-path", path: artifact.path }],
  });
  const searched = await serviceWith().search({
    path: artifact.path,
    target: "strings",
    contains: "login",
  });
  expect(searched.ok).toBe(true);
  if (!searched.ok) throw searched.error;
  expect(searched.value).toMatchObject({
    operation: "search_hermes_bytecode",
    parameters: { path: artifact.path, target: "strings", contains: "login" },
  });
});

it("rejects a report for another artifact or with inconsistent rows", async () => {
  const moved = await serviceWith({
    inspect: { ...report, artifact: { ...artifact, path: "/artifacts/other" } },
  }).inspect({ path: artifact.path });
  expect(moved.ok).toBe(false);
  if (!moved.ok) expect(moved.error).toBeInstanceOf(AnalysisOutputError);
  const inconsistent = await serviceWith({
    search: { ...search, page: { ...search.page, returned: 1, total: 1 } },
  }).search({ path: artifact.path, target: "strings" });
  expect(inconsistent.ok).toBe(false);
  if (!inconsistent.ok)
    expect(inconsistent.error).toBeInstanceOf(AnalysisOutputError);
});

it("reports cancellation observed before the read starts", async () => {
  const controller = new AbortController();
  controller.abort(new Error("caller left"));
  const result = await serviceWith().inspect(
    { path: artifact.path },
    { signal: controller.signal },
  );
  expect(result.ok).toBe(false);
  if (!result.ok) expect(result.error).toBeInstanceOf(AnalysisCancelledError);
});
