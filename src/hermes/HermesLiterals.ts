/** Serialized literal tag kinds stored in bits 4 through 6 of a tag byte. */
const NUMBER_TAG = 0x30;
const LONG_STRING_TAG = 0x40;
const SHORT_STRING_TAG = 0x50;
const BYTE_STRING_TAG = 0x60;
const INTEGER_TAG = 0x70;
const TAG_MASK = 0x70;

/** Bytes per value for each tag; null, true and false store no value bytes. */
const valueBytes = (tag: number): number => {
  switch (tag) {
    case NUMBER_TAG:
      return 8;
    case LONG_STRING_TAG:
    case INTEGER_TAG:
      return 4;
    case SHORT_STRING_TAG:
      return 2;
    case BYTE_STRING_TAG:
      return 1;
    default:
      return 0;
  }
};

const isStringTag = (tag: number): boolean =>
  tag === LONG_STRING_TAG ||
  tag === SHORT_STRING_TAG ||
  tag === BYTE_STRING_TAG;

const stringId = (bytes: Buffer, at: number, tag: number): number | null => {
  switch (tag) {
    case LONG_STRING_TAG:
      return bytes.readUInt32LE(at);
    case SHORT_STRING_TAG:
      return bytes.readUInt16LE(at);
    case BYTE_STRING_TAG:
      return bytes.readUInt8(at);
    default:
      return null;
  }
};

/**
 * Visit string ids among `literals.count` literals that start at byte
 * `literals.offset` of a serialized literal buffer.
 *
 * A buffer is a run of tagged sequences: a tag byte carries the value kind and
 * a 4-bit length, or a 12-bit length across two bytes when bit 7 is set.
 * Returns the bytes read, or -1 when the literals leave `[start, end)` or a
 * sequence is empty.
 */
export const visitLiteralStringIds = (
  bytes: Buffer,
  range: { readonly start: number; readonly end: number },
  literals: { readonly offset: number; readonly count: number },
  visit: (id: number) => void,
): number => {
  const first = range.start + literals.offset;
  let cursor = first;
  let remaining = literals.count;
  while (remaining > 0) {
    if (cursor >= range.end) return -1;
    const tag = bytes.readUInt8(cursor);
    let length = tag & 0x0f;
    cursor += 1;
    if ((tag & 0x80) !== 0) {
      if (cursor >= range.end) return -1;
      length = (length << 8) | bytes.readUInt8(cursor);
      cursor += 1;
    }
    if (length === 0) return -1;
    const kind = tag & TAG_MASK;
    const width = valueBytes(kind);
    const taken = Math.min(length, remaining);
    if (width * taken > range.end - cursor) return -1;
    if (isStringTag(kind))
      for (let index = 0; index < taken; index += 1) {
        const id = stringId(bytes, cursor + index * width, kind);
        if (id !== null) visit(id);
      }
    cursor += width * taken;
    remaining -= taken;
  }
  return cursor - first;
};
