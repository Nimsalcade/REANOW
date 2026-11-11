import { expect, it } from "vitest";
import {
  createHermesBytecode,
  hermesLiteralSequence,
  sampleHermesFixture,
} from "../../tests/fixtures/hermes/bytecode.js";
import { HermesBytecodeFile } from "./HermesBytecodeFile.js";
import { visitLiteralStringIds } from "./HermesLiterals.js";
import { scanReferences } from "./HermesReferences.js";
import { HermesWorkBudget } from "./HermesWorkBudget.js";

const control = (file: HermesBytecodeFile, signal?: AbortSignal) => ({
  budget: new HermesWorkBudget(file.header.fileLength),
  signal,
});

const every = (count: number): Set<number> =>
  new Set(Array.from({ length: count }, (_, index) => index));

it("attributes operand and literal-buffer strings to their functions", () => {
  const file = new HermesBytecodeFile(
    createHermesBytecode(sampleHermesFixture()),
  );
  const scan = scanReferences(
    file,
    { strings: every(file.header.stringCount), functionId: 1, retained: 8 },
    control(file),
  );
  const referrers = (id: number) => scan.strings.get(id);
  expect(referrers(2)).toMatchObject({ functions: [1], count: 1 });
  expect(referrers(6)).toMatchObject({ functions: [1], count: 1 });
  // Object literal key and value, then both array literal entries.
  expect(referrers(8)).toMatchObject({ functions: [1], count: 1 });
  expect(referrers(3)).toMatchObject({ functions: [1], count: 1 });
  expect(referrers(4)).toMatchObject({ functions: [2], count: 1 });
  expect(referrers(5)).toMatchObject({ functions: [2], count: 1 });
  expect(referrers(9)).toMatchObject({ functions: [], count: 0 });
  // Created by the global function and called directly by function 2.
  expect(scan.functionReferrers).toMatchObject({ functions: [0, 2], count: 2 });
  expect(scan.unreadableLiteralBuffers).toBe(0);
});

it("counts every distinct referrer while retaining a bounded list", () => {
  const code = [
    ["LoadConstString", 0, 1],
    ["LoadConstString", 0, 1],
    ["Ret", 0],
  ] as const;
  const file = new HermesBytecodeFile(
    createHermesBytecode({
      strings: ["", "shared"],
      functions: Array.from({ length: 5 }, () => ({ name: 0, code })),
    }),
  );
  const scan = scanReferences(
    file,
    { strings: new Set([1]), functionId: null, retained: 2 },
    control(file),
  );
  expect(scan.strings.get(1)).toMatchObject({ functions: [0, 1], count: 5 });
  expect(scan.functionReferrers.count).toBe(0);
});

it("reports literal instructions that leave their buffer", () => {
  const file = new HermesBytecodeFile(
    createHermesBytecode({
      strings: ["", "value"],
      arrayBuffer: hermesLiteralSequence("byte-string", [1]),
      functions: [
        {
          name: 0,
          code: [
            ["NewArrayWithBuffer", 0, 4, 4, 0],
            ["Ret", 0],
          ],
        },
      ],
    }),
  );
  const scan = scanReferences(
    file,
    { strings: new Set([1]), functionId: null, retained: 2 },
    control(file),
  );
  expect(scan.unreadableLiteralBuffers).toBe(1);
  expect(scan.strings.get(1)).toMatchObject({ functions: [0], count: 1 });
});

it("stops between functions when the caller cancels", () => {
  const file = new HermesBytecodeFile(
    createHermesBytecode(sampleHermesFixture()),
  );
  const controller = new AbortController();
  controller.abort(new Error("stop"));
  expect(() =>
    scanReferences(
      file,
      { strings: new Set([2]), functionId: null, retained: 2 },
      control(file, controller.signal),
    ),
  ).toThrow("stop");
});

it("decodes short and long tagged literal sequences", () => {
  const bytes = Buffer.concat([
    hermesLiteralSequence("integer", [7]),
    hermesLiteralSequence("null", [0, 0]),
    hermesLiteralSequence("long-string", [70_000]),
    hermesLiteralSequence(
      "short-string",
      Array.from({ length: 20 }, (_, index) => 300 + index),
    ),
  ]);
  const ids: number[] = [];
  const range = { start: 0, end: bytes.length };
  expect(
    visitLiteralStringIds(bytes, range, { offset: 0, count: 24 }, (id) =>
      ids.push(id),
    ),
  ).toBe(bytes.length);
  expect(ids).toEqual([
    70_000,
    ...Array.from({ length: 20 }, (_, index) => 300 + index),
  ]);
  expect(
    visitLiteralStringIds(
      bytes,
      range,
      { offset: 0, count: 25 },
      () => undefined,
    ),
  ).toBe(-1);
  expect(
    visitLiteralStringIds(
      Buffer.from([0x60]),
      { start: 0, end: 1 },
      { offset: 0, count: 1 },
      () => undefined,
    ),
  ).toBe(-1);
});

it("refuses decode work far beyond what the file length explains", () => {
  const body = Array.from({ length: 16_000 }, () => ["Ret", 0] as const);
  const file = new HermesBytecodeFile(
    createHermesBytecode({
      strings: ["", "value"],
      functions: [
        { name: 0, code: body },
        ...Array.from({ length: 600 }, () => ({
          name: 0,
          code: [],
          sameBodyAs: 0,
        })),
      ],
    }),
  );
  const budget = new HermesWorkBudget(file.header.fileLength);
  expect(budget.maximumDecodedBytes).toBe(16 * 1024 * 1024);
  expect(() =>
    scanReferences(
      file,
      { strings: new Set([1]), functionId: null, retained: 2 },
      { budget },
    ),
  ).toThrow(/exceeds the allowance of four file lengths/u);
  expect(new HermesWorkBudget(64 * 1024 * 1024).maximumDecodedBytes).toBe(
    256 * 1024 * 1024,
  );
});
