import type { EvidenceMcpServer } from "./EvidenceMcpServer.js";
import type { HermesBytecodeService } from "../application/hermes/HermesBytecodeService.js";
import type { EvidenceWriter } from "../application/investigation/InvestigationRecordPort.js";
import { toolContract } from "../contracts/toolContracts.js";
import type { Logger } from "pino";
import type { WithAdmittedAnalysis } from "./analysisAdmission.js";
import { createEvidenceToolHandler } from "./contractToolHandler.js";
import { toolRegistrationOptions } from "./toolRegistrationOptions.js";

/** Bind the exact Hermes contracts to their shared workflows and owning session Evidence ledger. */
export const registerHermesTools = (
  server: EvidenceMcpServer,
  service: HermesBytecodeService,
  logger: Logger,
  recordEvidence?: EvidenceWriter["recordEvidence"],
  withAdmittedAnalysis?: WithAdmittedAnalysis,
): void => {
  const inspect = toolContract("inspect_hermes_bytecode");
  server.registerTool(
    inspect.name,
    toolRegistrationOptions(inspect),
    createEvidenceToolHandler(
      server,
      (input, options) => service.inspect(input, options),
      logger,
      recordEvidence,
      withAdmittedAnalysis,
    )(inspect),
  );
  const search = toolContract("search_hermes_bytecode");
  server.registerTool(
    search.name,
    toolRegistrationOptions(search),
    createEvidenceToolHandler(
      server,
      (input, options) => service.search(input, options),
      logger,
      recordEvidence,
      withAdmittedAnalysis,
    )(search),
  );
};
