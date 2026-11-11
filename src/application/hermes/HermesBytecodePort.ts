import type {
  ExecutionOptions,
  ProviderIdentity,
} from "../AnalysisProvider.js";
import type { AnalysisError } from "../../domain/analysisErrorBase.js";
import type {
  HermesBytecode,
  HermesSearch,
  InspectHermesBytecodeInput,
  SearchHermesBytecodeInput,
} from "../../domain/hermes/hermesBytecode.js";
import type { Result } from "../../domain/result.js";

/** Replaceable offline bytecode reader with no target-execution authority. */
export interface HermesBytecodePort {
  readonly identity: ProviderIdentity;
  close?(): Promise<void>;
  inspect(
    input: InspectHermesBytecodeInput,
    options?: ExecutionOptions,
  ): Promise<Result<HermesBytecode, AnalysisError>>;
  search(
    input: SearchHermesBytecodeInput,
    options?: ExecutionOptions,
  ): Promise<Result<HermesSearch, AnalysisError>>;
}
