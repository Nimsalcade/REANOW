import { z } from "incur";
import { createHermesBytecodeService } from "../composition/hermes.js";
import { CLI_COMMANDS } from "../cliCommandNames.js";
import { logCliCommand } from "../cliLogging.js";
import { finishAnalysisCommand } from "./finishAnalysisCommand.js";
import type { Logger } from "pino";
import type { CliInstance } from "./types.js";
import { withCommandCancellation } from "./commandCancellation.js";
import { absoluteCallerPath } from "../filesystem/callerPath.js";

const PROVIDER_ID = "hermes-bytecode";
const pathArgument = z.object({
  path: z
    .string()
    .describe("Local Hermes bytecode file, such as index.android.bundle"),
});
const pageOptions = {
  offset: z.number().optional().describe("Zero-based first row to return"),
  limit: z.number().optional().describe("Rows to return"),
};

/** Drop options the caller did not pass so strict schemas see only explicit choices. */
const selected = (
  values: Readonly<Record<string, unknown>>,
): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== undefined),
  );

/** Resolve caller paths and read Hermes bytecode through the shared workflows. */
export const registerHermesCommands = (
  cli: CliInstance,
  logger: Logger,
): void => {
  cli.command(CLI_COMMANDS.inspectHermesBytecode, {
    description:
      "Inspect a React Native Hermes bytecode file, or disassemble one of its functions, without target execution",
    args: pathArgument,
    options: z.object({
      functionId: z
        .number()
        .optional()
        .describe("Function table index to disassemble"),
      ...pageOptions,
    }),
    run: ({ args, options }) =>
      withCommandCancellation((signal) => {
        const service = createHermesBytecodeService();
        return logCliCommand(logger, CLI_COMMANDS.inspectHermesBytecode, () =>
          finishAnalysisCommand(
            () =>
              service.inspect(
                selected({
                  path: absoluteCallerPath(args.path),
                  function_id: options.functionId,
                  offset: options.offset,
                  limit: options.limit,
                }),
                { signal },
              ),
            service,
            PROVIDER_ID,
            "inspect_hermes_bytecode",
            signal,
          ),
        );
      }),
  });
  cli.command(CLI_COMMANDS.searchHermesBytecode, {
    description:
      "Search the strings or function names of a Hermes bytecode file and list referencing functions, without target execution",
    args: pathArgument,
    options: z.object({
      target: z
        .enum(["strings", "functions"])
        .default("strings")
        .describe("Table to search"),
      contains: z.string().optional().describe("Substring to match"),
      matchCase: z.boolean().optional().describe("Match case-sensitively"),
      stringKind: z
        .enum(["string", "identifier", "predefined"])
        .optional()
        .describe("Restrict a strings search to one string kind"),
      ...pageOptions,
      maxValueUnits: z
        .number()
        .optional()
        .describe("Leading UTF-16 code units returned per string value"),
    }),
    run: ({ args, options }) =>
      withCommandCancellation((signal) => {
        const service = createHermesBytecodeService();
        return logCliCommand(logger, CLI_COMMANDS.searchHermesBytecode, () =>
          finishAnalysisCommand(
            () =>
              service.search(
                selected({
                  path: absoluteCallerPath(args.path),
                  target: options.target,
                  contains: options.contains,
                  match_case: options.matchCase,
                  string_kind: options.stringKind,
                  offset: options.offset,
                  limit: options.limit,
                  max_value_units: options.maxValueUnits,
                }),
                { signal },
              ),
            service,
            PROVIDER_ID,
            "search_hermes_bytecode",
            signal,
          ),
        );
      }),
  });
};
