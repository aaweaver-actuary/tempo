/** An explicit worker failure. Codes, rather than exception text, cross the wire. */
export class MaiaError extends Error {
  constructor(code, message, { retryable = false, cause } = {}) {
    super(message, { cause });
    this.name = "MaiaError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function maiaError(code, message, cause) {
  return new MaiaError(code, message, { cause });
}
