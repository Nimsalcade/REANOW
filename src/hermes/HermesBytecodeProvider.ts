import { readStableArtifact } from "../artifacts/readStableArtifact.js";
import {
  ArtifactFileCleanupFailure,
  assertArtifactReadActive,
  bindArtifactFileCleanup,
} from "../artifacts/ArtifactFileOperation.js";
import { ArtifactResourceScope } from "../artifacts/ArtifactResourceScope.js";
import { ArtifactReaderFailure } from "../artifacts/ArtifactReader.js";
import type { ExecutionOptions } from "../application/AnalysisProvider.js";
import type { HermesBytecodePort } from "../application/hermes/HermesBytecodePort.js";
import { AnalysisError } from "../domain/analysisErrorBase.js";
import { analysisErrorWithCleanupFailure } from "../domain/analysisErrorCleanup.js";
import {
  AnalysisAccessDeniedError,
  AnalysisArtifactChangedError,
  AnalysisCancelledError,
  AnalysisInputError,
  AnalysisResourceConstraintError,
  AnalysisUnsupportedTargetError,
} from "../domain/analysisErrorCore.js";
import { fixedAllowanceOptions } from "../domain/fixedAllowanceRemediation.js";
import type {
  HermesBytecode,
  HermesSearch,
  InspectHermesBytecodeInput,
  SearchHermesBytecodeInput,
} from "../domain/hermes/hermesBytecode.js";
import { ProviderAdapterError } from "../domain/providerAdapterError.js";
import { ProviderCleanupError } from "../domain/providerCleanupError.js";
import { err, ok, type Result } from "../domain/result.js";
import {
  HermesFormatFailure,
  HermesOutputFailure,
  HermesSelectionFailure,
  HermesWorkFailure,
} from "./HermesBytecodeFailures.js";
import { HermesBytecodeFile } from "./HermesBytecodeFile.js";
import { inspectHermesFile, searchHermesFile } from "./HermesBytecodeReport.js";

/** Selected-file admission limit shared with the stable artifact reader. */
export const HERMES_INPUT_BYTES = 256 * 1024 * 1024;
/** Complete inline report limit for one inspection or search page. */
export const HERMES_OUTPUT_BYTES = 8 * 1024 * 1024;

/** Bundled reader identity. The file's bytecode version is a separate observed fact. */
export const HERMES_BYTECODE_PROVIDER_IDENTITY = {
  id: "hermes-bytecode",
  name: "REA Hermes bytecode reader",
  version: "1",
} as const;

type Operation = "inspect_hermes_bytecode" | "search_hermes_bytecode";
type Artifact = HermesBytecode["artifact"];

/** Stable in-process bytecode reading without subprocesses, temporary files, or a Hermes toolchain. */
export class HermesBytecodeProvider implements HermesBytecodePort {
  readonly identity = HERMES_BYTECODE_PROVIDER_IDENTITY;
  readonly #resources = new ArtifactResourceScope();

  async close(): Promise<void> {
    try {
      await this.#resources.close();
    } catch (cause: unknown) {
      if (
        !(cause instanceof ArtifactReaderFailure) ||
        cause.cleanup === undefined
      )
        throw cause;
      const error = new ProviderCleanupError(
        HERMES_BYTECODE_PROVIDER_IDENTITY.id,
        cause.cleanup.resources,
        { reason: cause.cleanup.reason },
        { cause, operation: "inspect_hermes_bytecode" },
      );
      bindArtifactFileCleanup(cause, error);
      throw error;
    }
  }

  /** Report header facts and, when selected, one function's instructions. */
  inspect(
    input: InspectHermesBytecodeInput,
    options?: ExecutionOptions,
  ): Promise<Result<HermesBytecode, AnalysisError>> {
    return this.#read(
      "inspect_hermes_bytecode",
      input.path,
      options,
      (file, artifact) =>
        inspectHermesFile(file, artifact, input, options?.signal),
    );
  }

  /** Page through matching strings with their referrers, or matching functions. */
  search(
    input: SearchHermesBytecodeInput,
    options?: ExecutionOptions,
  ): Promise<Result<HermesSearch, AnalysisError>> {
    return this.#read(
      "search_hermes_bytecode",
      input.path,
      options,
      (file, artifact) =>
        searchHermesFile(file, artifact, input, options?.signal),
    );
  }

  async #read<Report>(
    operation: Operation,
    path: string,
    options: ExecutionOptions | undefined,
    report: (file: HermesBytecodeFile, artifact: Artifact) => Report,
  ): Promise<Result<Report, AnalysisError>> {
    const signal = options?.signal;
    try {
      return await this.#resources.run(async () => {
        const snapshot = await readStableArtifact(
          path,
          HERMES_INPUT_BYTES,
          signal,
          undefined,
          this.#resources,
        );
        assertArtifactReadActive(signal);
        const value = report(new HermesBytecodeFile(snapshot.bytes), {
          path,
          sha256: snapshot.sha256,
          bytes: snapshot.bytes.length,
        });
        if (
          Buffer.byteLength(JSON.stringify(value), "utf8") > HERMES_OUTPUT_BYTES
        )
          throw new HermesOutputFailure(
            HERMES_OUTPUT_BYTES,
            `The selected Hermes bytecode report for ${path} exceeds the inline output budget; no partial success is returned.`,
          );
        assertArtifactReadActive(signal);
        return ok(value);
      });
    } catch (cause: unknown) {
      return err(failureFor(operation, cause, path, signal));
    }
  }
}

const selectionFailure = (
  operation: Operation,
  cause: HermesSelectionFailure,
): AnalysisError =>
  new AnalysisInputError(operation, { cause }, [
    { path: [cause.field], reason: "invalid_value", message: cause.message },
  ]);

const formatFailure = (
  operation: Operation,
  cause: HermesFormatFailure,
  path: string,
): AnalysisError =>
  cause.kind === "unsupported"
    ? new AnalysisUnsupportedTargetError(operation, path, cause.message, {
        cause,
      })
    : new AnalysisInputError(operation, { cause }, [
        {
          path: ["path"],
          reason: "invalid_format",
          message: `Selected artifact ${path}: ${cause.message}`,
        },
      ]);

const failureForPrimary = (
  operation: Operation,
  cause: unknown,
  path: string,
  signal?: AbortSignal,
): AnalysisError => {
  if (cause instanceof AnalysisError) return cause;
  if (cause instanceof HermesSelectionFailure)
    return selectionFailure(operation, cause);
  if (cause instanceof HermesFormatFailure)
    return formatFailure(operation, cause, path);
  if (cause instanceof HermesOutputFailure)
    return new AnalysisResourceConstraintError(
      operation,
      "transport",
      cause.message,
      { maximum_output_bytes: cause.maximumOutputBytes },
      {
        cause,
        retryable: false,
        remediationAction:
          "Select a smaller page with limit, or a smaller max_value_units, and continue from next_offset. Repeating the unchanged request fails the same way.",
      },
    );
  if (cause instanceof HermesWorkFailure)
    return new AnalysisResourceConstraintError(
      operation,
      "cpu",
      `Selected artifact ${path}: ${cause.message}`,
      { maximum_decoded_bytes: cause.maximumDecodedBytes },
      {
        cause,
        ...fixedAllowanceOptions("The Hermes decode-work allowance", "work"),
      },
    );
  if (signal?.aborted && cause === signal.reason)
    return new AnalysisCancelledError(operation, { cause: signal.reason });
  if (cause instanceof RangeError)
    return formatFailure(
      operation,
      new HermesFormatFailure(
        "malformed",
        "A table entry points outside the file.",
        { cause },
      ),
      path,
    );
  return artifactFailure(operation, cause, path);
};

const failureFor = (
  operation: Operation,
  cause: unknown,
  path: string,
  signal?: AbortSignal,
): AnalysisError => {
  const primary =
    cause instanceof ArtifactFileCleanupFailure &&
    cause.fileCleanupFailure.outcome.kind === "failed"
      ? failureForPrimary(
          operation,
          cause.fileCleanupFailure.outcome.cause,
          path,
          signal,
        )
      : cause instanceof ArtifactFileCleanupFailure && signal?.aborted
        ? new AnalysisCancelledError(operation, { cause: signal.reason })
        : failureForPrimary(operation, cause, path, signal);
  if (!(cause instanceof ArtifactReaderFailure) || cause.cleanup === undefined)
    return primary;
  const cleanup = new ProviderCleanupError(
    HERMES_BYTECODE_PROVIDER_IDENTITY.id,
    cause.cleanup.resources,
    { reason: cause.cleanup.reason },
    { cause, operation },
  );
  const error = analysisErrorWithCleanupFailure(primary, cleanup, operation);
  bindArtifactFileCleanup(cause, error);
  return error;
};

const artifactFailure = (
  operation: Operation,
  cause: unknown,
  path: string,
): AnalysisError => {
  if (cause instanceof ArtifactReaderFailure)
    return artifactReaderFailure(operation, cause, path);
  if (cause instanceof Error && "code" in cause) {
    if (cause.code === "EACCES" || cause.code === "EPERM")
      return new AnalysisAccessDeniedError(operation, path, cause.code, {
        cause,
      });
    if (
      ["ENOENT", "ENOTDIR", "ELOOP", "ENAMETOOLONG"].includes(
        String(cause.code),
      )
    )
      return new AnalysisInputError(operation, { cause }, [
        {
          path: ["path"],
          reason: "invalid_value",
          message: `Selected artifact could not be read (${String(cause.code)}): ${path}.`,
        },
      ]);
  }
  return new ProviderAdapterError(
    HERMES_BYTECODE_PROVIDER_IDENTITY.id,
    operation,
    {
      cause,
      diagnostics: {
        path,
        reason: cause instanceof Error ? cause.message : String(cause),
      },
    },
  );
};

const artifactReaderFailure = (
  operation: Operation,
  cause: ArtifactReaderFailure,
  path: string,
): AnalysisError => {
  if (cause.reason === "integrity")
    return new AnalysisArtifactChangedError(operation, path, cause.message, {
      cause,
    });
  if (cause.reason === "cancelled")
    return new AnalysisCancelledError(operation, { cause: cause.cause });
  if (cause.reason === "limit")
    return new AnalysisResourceConstraintError(
      operation,
      "file-size",
      cause.message,
      { maximum_input_bytes: HERMES_INPUT_BYTES },
      {
        cause,
        ...fixedAllowanceOptions("the fixed Hermes input allowance", "input"),
      },
    );
  if (cause.reason === "path")
    return new AnalysisInputError(operation, { cause }, [
      { path: ["path"], reason: "invalid_format", message: cause.message },
    ]);
  return new ProviderAdapterError(
    HERMES_BYTECODE_PROVIDER_IDENTITY.id,
    operation,
    { cause, diagnostics: { path, reason: cause.message } },
  );
};
