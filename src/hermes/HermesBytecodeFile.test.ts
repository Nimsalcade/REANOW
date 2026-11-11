import { describe, expect, it } from "vitest";
import {
  createHermesBytecode,
  encodeHermesCode,
  sampleHermesFixture,
} from "../../tests/fixtures/hermes/bytecode.js";
import { HermesFormatFailure } from "./HermesBytecodeFailures.js";
import { HermesBytecodeFile } from "./HermesBytecodeFile.js";
import { forEachInstruction, renderInstruction } from "./HermesInstructions.js";
import {
  HERMES_SUPPORTED_VERSIONS,
  hermesOpcodeTable,
} from "./HermesOpcodes.js";

const linesOf = (file: HermesBytecodeFile, id: number): string[] => {
  const header = file.functionHeader(id);
  const lines: string[] = [];
  forEachInstruction(file, header, (instruction) =>
    lines.push(`${instruction.offset} ${renderInstruction(file, instruction)}`),
  );
  return lines;
};

const failure = (bytes: Buffer): HermesFormatFailure => {
  try {
    const file = new HermesBytecodeFile(bytes);
    for (let id = 0; id < file.header.functionCount; id += 1) linesOf(file, id);
  } catch (cause) {
    if (cause instanceof HermesFormatFailure) return cause;
    throw cause;
  }
  throw new Error("Expected a Hermes format failure");
};

describe("Hermes opcode tables", () => {
  // Opcode bytes observed while decoding real bundles of these versions.
  it.each([
    [59, { Unreachable: 93, CreateEnvironment: 47, Ret: 85, Jmp: 127 }],
    [74, { Unreachable: 93, CreateEnvironment: 47, Ret: 85, Jmp: 130 }],
    [84, { Unreachable: 0, CreateEnvironment: 48, Ret: 88, Jmp: 135 }],
    [96, { Unreachable: 0, CreateEnvironment: 50, Ret: 92, Jmp: 142 }],
  ])("keeps version %i opcode numbering", (version, expected) => {
    const table = hermesOpcodeTable(version);
    const actual = Object.fromEntries(
      Object.keys(expected).map((name) => [
        name,
        table?.opcodes.findIndex((opcode) => opcode.name === name),
      ]),
    );
    expect(actual).toEqual(expected);
  });

  it("derives instruction sizes from operand widths", () => {
    const table = hermesOpcodeTable(96);
    const byName = (name: string) =>
      table?.opcodes.find((opcode) => opcode.name === name);
    expect(byName("Ret")?.size).toBe(2);
    expect(byName("JmpLong")?.size).toBe(5);
    expect(byName("LoadConstDouble")?.size).toBe(10);
    expect(byName("GetById")?.operands.at(-1)?.target).toBe("string");
    expect(byName("CallDirectLongIndex")?.operands.at(-1)?.target).toBe(
      "function",
    );
    expect(hermesOpcodeTable(97)).toBeUndefined();
  });
});

describe("Hermes bytecode file", () => {
  it.each(HERMES_SUPPORTED_VERSIONS)(
    "reads the version %i layout",
    (version) => {
      const file = new HermesBytecodeFile(
        createHermesBytecode(sampleHermesFixture(version)),
      );
      expect(file.header).toMatchObject({
        version,
        functionCount: 3,
        stringCount: 10,
        identifierCount: 4,
        overflowStringCount: 1,
        bigIntCount: version >= 89 ? 0 : null,
        functionSourceCount: version >= 84 ? 0 : null,
        segmentField: version >= 83 ? "segment_id" : "cjs_module_offset",
      });
      expect(file.trailingBytes).toBe(0);
      expect(file.footerHash === null).toBe(version < 76);
      expect(file.computeContentHash()).toBe(file.footerHash);
      expect(file.stringAt(2)).toBe("https://api.example.test/login");
      expect(file.stringAt(4)).toBe("ünïcode ✓");
      expect(file.stringLength(5)).toBe(300);
      expect(file.stringAt(5, 3)).toBe("xxx");
      expect(file.stringKind(3)).toBe("string");
      expect(file.stringKind(6)).toBe("identifier");
      expect(linesOf(file, 1)).toEqual([
        '0 LoadConstString r0, "https://api.example.test/login"',
        '4 GetById r1, r0, 1, "fetch"',
        "10 Jmp L12",
        "12 NewObjectWithBuffer r2, 1, 1, 0, 0",
        "22 Ret r0",
      ]);
    },
  );

  it("reads small and large function headers and exception handlers", () => {
    const file = new HermesBytecodeFile(
      createHermesBytecode(sampleHermesFixture()),
    );
    const small = file.functionHeader(1);
    expect(small).toMatchObject({
      paramCount: 2,
      bytecodeSizeInBytes: 24,
      functionName: 7,
      overflowed: false,
      hasExceptionHandler: true,
      prohibitInvoke: 2,
    });
    expect(file.exceptionHandlers(small)).toEqual([
      { start: 0, end: 10, target: 22 },
    ]);
    const large = file.functionHeader(2);
    expect(large).toMatchObject({ overflowed: true, functionName: 0 });
    expect(linesOf(file, 2)).toEqual([
      "0 NewArrayWithBuffer r0, 2, 2, 0",
      '8 CallDirectLongIndex r0, 1, fn#1 "login"',
      "15 Ret r0",
    ]);
    expect(linesOf(file, 0)[1]).toBe('2 CreateClosure r1, r0, fn#1 "login"');
  });

  it("renders SwitchImm jump tables that follow the opcodes", () => {
    const code = encodeHermesCode(96, [
      ["SwitchImm", 0, 20, 18, 3, 4],
      ["Ret", 0],
    ]);
    const trailer = Buffer.alloc(8);
    trailer.writeInt32LE(18, 0);
    trailer.writeInt32LE(18, 4);
    const file = new HermesBytecodeFile(
      createHermesBytecode({
        strings: [""],
        functions: [{ name: 0, code: [], rawCode: code, trailer }],
      }),
    );
    expect(linesOf(file, 0)).toEqual([
      "0 SwitchImm r0, 20, L18, 3, 4 [L18, L18]",
      "18 Ret r0",
    ]);
  });

  it("renders only the leading targets of a large jump table", () => {
    const code = encodeHermesCode(96, [
      ["SwitchImm", 0, 20, 18, 0, 99],
      ["Ret", 0],
    ]);
    const trailer = Buffer.alloc(400);
    for (let index = 0; index < 100; index += 1)
      trailer.writeInt32LE(18, index * 4);
    const file = new HermesBytecodeFile(
      createHermesBytecode({
        strings: [""],
        functions: [{ name: 0, code: [], rawCode: code, trailer }],
      }),
    );
    const [line] = linesOf(file, 0);
    expect(line?.match(/L18/gu)).toHaveLength(65);
    expect(line).toMatch(/, …\+36\]$/u);
  });

  it("keeps trailing bytes outside the recorded file length", () => {
    const file = new HermesBytecodeFile(
      createHermesBytecode({
        ...sampleHermesFixture(),
        trailing: Buffer.from("epilogue"),
        corruptFooter: true,
      }),
    );
    expect(file.trailingBytes).toBe(8);
    expect(file.computeContentHash()).not.toBe(file.footerHash);
  });
});

describe("Hermes bytecode failures", () => {
  it("classifies foreign, delta-form and undecoded versions as unsupported", () => {
    const bytes = createHermesBytecode(sampleHermesFixture());
    expect(failure(Buffer.from("var a = 1;")).kind).toBe("unsupported");
    const delta = Buffer.from(bytes);
    for (let index = 0; index < 8; index += 1)
      delta.writeUInt8(delta.readUInt8(index) ^ 0xff, index);
    expect(failure(delta)).toMatchObject({
      kind: "unsupported",
      message: expect.stringContaining("delta-form"),
    });
    const future = Buffer.from(bytes);
    future.writeUInt32LE(99, 8);
    expect(failure(future)).toMatchObject({
      kind: "unsupported",
      message: expect.stringContaining("version 99"),
    });
  });

  it("rejects truncated and inconsistent tables as malformed", () => {
    const bytes = createHermesBytecode(sampleHermesFixture());
    expect(failure(bytes.subarray(0, 100)).kind).toBe("malformed");
    expect(failure(bytes.subarray(0, bytes.length - 1)).message).toContain(
      "header records",
    );
    const noFunctions = Buffer.from(bytes);
    noFunctions.writeUInt32LE(0, 40);
    expect(failure(noFunctions).message).toContain("no functions");
    const kinds = Buffer.from(bytes);
    kinds.writeUInt32LE(11, 52);
    expect(failure(kinds).message).toMatch(/String-kind runs|extends past/u);
    const storage = Buffer.from(bytes);
    storage.writeUInt32LE(4, 60);
    expect(() => new HermesBytecodeFile(storage).stringAt(2)).toThrow(
      /outside string storage/u,
    );
  });

  it("rejects undefined opcodes and instructions that cross the function end", () => {
    const undefinedOpcode = createHermesBytecode({
      strings: [""],
      functions: [{ name: 0, code: [], rawCode: Buffer.from([0xff, 0]) }],
    });
    expect(failure(undefinedOpcode).message).toContain("undefined opcode 255");
    const crossing = createHermesBytecode({
      strings: [""],
      functions: [
        {
          name: 0,
          code: [],
          rawCode: encodeHermesCode(96, [["JmpLong", 0]]).subarray(0, 3),
        },
      ],
    });
    expect(failure(crossing).message).toContain("crosses the end");
  });
});
