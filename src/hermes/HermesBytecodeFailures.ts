/** Malformed or unsupported Hermes bytecode bytes. */
export class HermesFormatFailure extends Error {
  constructor(
    readonly kind: "malformed" | "unsupported",
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "HermesFormatFailure";
  }
}

/** A selection that names an object the selected file does not contain. */
export class HermesSelectionFailure extends Error {
  constructor(
    readonly field: "function_id" | "offset",
    message: string,
  ) {
    super(message);
    this.name = "HermesSelectionFailure";
  }
}

/** The complete selected report exceeds the fixed inline allowance. */
export class HermesOutputFailure extends Error {
  constructor(
    readonly maximumOutputBytes: number,
    message: string,
  ) {
    super(message);
    this.name = "HermesOutputFailure";
  }
}

/** One operation would decode more bytes than the fixed work allowance. */
export class HermesWorkFailure extends Error {
  constructor(
    readonly maximumDecodedBytes: number,
    message: string,
  ) {
    super(message);
    this.name = "HermesWorkFailure";
  }
}

/** Raise a malformed-input failure; typed as never for control-flow narrowing. */
export const malformed = (message: string): never => {
  throw new HermesFormatFailure("malformed", message);
};
