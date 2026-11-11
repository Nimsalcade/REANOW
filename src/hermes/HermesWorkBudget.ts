import { HermesWorkFailure } from "./HermesBytecodeFailures.js";

/** Smallest allowance, so small files are never limited by their own size. */
const MINIMUM_DECODED_BYTES = 16 * 1024 * 1024;
/**
 * Compiler output decodes well under one file length of instructions and
 * literals in a full sweep. Function headers may share bodies, so a crafted
 * file can ask for far more; four file lengths bounds that amplification.
 */
const FILE_LENGTH_MULTIPLE = 4;

/** Decode-work allowance for one operation over one file. */
export class HermesWorkBudget {
  readonly maximumDecodedBytes: number;
  #remaining: number;

  constructor(fileLength: number) {
    this.maximumDecodedBytes = Math.max(
      MINIMUM_DECODED_BYTES,
      fileLength * FILE_LENGTH_MULTIPLE,
    );
    this.#remaining = this.maximumDecodedBytes;
  }

  /** Bytes decoded so far by this operation. */
  get decodedBytes(): number {
    return this.maximumDecodedBytes - this.#remaining;
  }

  /** Account for instruction or literal bytes about to be decoded. */
  spend(bytes: number): void {
    this.#remaining -= bytes;
    if (this.#remaining < 0)
      throw new HermesWorkFailure(
        this.maximumDecodedBytes,
        `Decoding the selected file needs more than ${String(this.maximumDecodedBytes)} bytes of instruction and literal work, which exceeds the allowance of four file lengths. Function headers in this file share bytecode far more than compiler output does.`,
      );
  }
}
