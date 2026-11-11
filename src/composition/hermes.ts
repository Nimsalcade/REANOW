import { HermesBytecodeService } from "../application/hermes/HermesBytecodeService.js";
import { HermesBytecodeProvider } from "../hermes/HermesBytecodeProvider.js";

/** Compose bundled offline bytecode reading without a Hermes toolchain or any child process. */
export const createHermesBytecodeService = (): HermesBytecodeService =>
  new HermesBytecodeService(new HermesBytecodeProvider());
