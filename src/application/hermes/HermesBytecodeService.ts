import { isAbsolute } from "node:path";
import type { z } from "zod";
import type { ExecutionOptions } from "../AnalysisProvider.js";
import type { HermesBytecodePort } from "./HermesBytecodePort.js";
import type { AnalysisError } from "../../domain/analysisErrorBase.js";
import {
  AnalysisCancelledError,
  AnalysisInputError,
  AnalysisOutputError,
} from "../../domain/analysisErrorCore.js";
import { createEvidence, type Evidence } from "../../domain/evidence.js";
import {
  hermesBytecodeSchema,
  hermesSearchSchema,
  inspectHermesBytecodeInputSchema,
  searchHermesBytecodeInputSchema,
} from "../../domain/hermes/hermesBytecode.js";
import type { JsonValue } from "../../domain/jsonValue.js";
import { analysisInputErrorFromIssues } from "../../domain/inputIssueProjection.js";
import { err, type Result } from "../../domain/result.js";
import { completeProviderEvidence } from "../ProviderOperationExecution.js";

type Operation = "inspect_hermes_bytecode" | "search_hermes_bytecode";

type OperationInput = { path: string } & Readonly<
  Record<string, string | number | boolean | undefined>
>;

/** Evidence parameters are the caller's explicit selections only. */
const parametersOf = (input: OperationInput): Record<string, JsonValue> => {
  const parameters: Record<string, JsonValue> = {};
  for (const [name, value] of Object.entries(input))
    if (value !== undefined) parameters[name] = value;
  return parameters;
};

interface OperationContract<Input extends OperationInput, Output> {
  readonly operation: Operation;
  readonly input: z.ZodType<Input>;
  readonly output: z.ZodType<Output>;
}

type ArtifactReport = JsonValue & {
  readonly artifact: { readonly path: string; readonly sha256: string };
  readonly limitations: readonly string[];
};

/** Shared exact input validation and artifact-bound Evidence for CLI and MCP. */
export class HermesBytecodeService {
  constructor(readonly provider: HermesBytecodePort) {}

  close(): Promise<void> {
    return this.provider.close?.() ?? Promise.resolve();
  }

  /** Report file identity, or one function's disassembly page. */
  inspect(
    rawInput: unknown,
    options?: ExecutionOptions,
  ): Promise<Result<Evidence, AnalysisError>> {
    return this.#run(
      {
        operation: "inspect_hermes_bytecode",
        input: inspectHermesBytecodeInputSchema,
        output: hermesBytecodeSchema,
      },
      rawInput,
      (input) => this.provider.inspect(input, options),
      options,
    );
  }

  /** Page through string or function matches of one explicitly selected file. */
  search(
    rawInput: unknown,
    options?: ExecutionOptions,
  ): Promise<Result<Evidence, AnalysisError>> {
    return this.#run(
      {
        operation: "search_hermes_bytecode",
        input: searchHermesBytecodeInputSchema,
        output: hermesSearchSchema,
      },
      rawInput,
      (input) => this.provider.search(input, options),
      options,
    );
  }

  async #run<Input extends OperationInput, Output extends ArtifactReport>(
    contract: OperationContract<Input, Output>,
    rawInput: unknown,
    read: (input: Input) => Promise<Result<Output, AnalysisError>>,
    options?: ExecutionOptions,
  ): Promise<Result<Evidence, AnalysisError>> {
    const { operation } = contract;
    if (options?.signal?.aborted)
      return err(
        new AnalysisCancelledError(operation, { cause: options.signal.reason }),
      );
    const input = contract.input.safeParse(rawInput);
    if (!input.success)
      return err(
        analysisInputErrorFromIssues(operation, input.error.issues, rawInput, {
          cause: input.error,
        }),
      );
    if (!isAbsolute(input.data.path))
      return err(
        new AnalysisInputError(operation, undefined, [
          {
            path: ["path"],
            reason: "invalid_format",
            message: "Expected an absolute filesystem path on this host.",
          },
        ]),
      );
    const produced = await read(input.data);
    if (!produced.ok) return produced;
    const report = contract.output.safeParse(produced.value);
    if (!report.success || report.data.artifact.path !== input.data.path)
      return err(
        new AnalysisOutputError(
          operation,
          "Hermes bytecode reader returned a malformed report or changed the selected artifact identity.",
        ),
      );
    return completeProviderEvidence(
      createEvidence(
        {
          path: report.data.artifact.path,
          sha256: report.data.artifact.sha256,
          format: "file",
        },
        this.provider.identity,
        {
          operation,
          parameters: parametersOf(input.data),
          result: report.data,
          confidence: "observed",
          limitations: [...report.data.limitations],
          locations: [
            { kind: "artifact-path", path: report.data.artifact.path },
          ],
        },
      ),
      options?.signal,
    );
  }
}
