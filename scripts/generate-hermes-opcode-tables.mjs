#!/usr/bin/env node
// Regenerates src/hermes/hermesOpcodeTables.ts from pinned commits of the
// MIT-licensed Hermes source (https://github.com/facebook/hermes).
//
// Usage:
//   git clone --filter=blob:none --no-checkout https://github.com/facebook/hermes <dir>
//   node scripts/generate-hermes-opcode-tables.mjs --hermes-repo <dir> [--check]
//
// The clone must contain the pinned commits below. Nothing is executed from the
// Hermes repository; only BytecodeList.def is read with `git show`.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** Released bytecode versions and the upstream commit that defines each one. */
export const HERMES_BYTECODE_SOURCES = [
  [59, "f22a18f67da3f03db59c1ec715d6ec3776b03fbf", "v0.1.0"],
  [60, "40e9383bf4ca085442cb1edbe713f4e9fd893f6b", "v0.1.1"],
  [62, "5b6c59e7d8006bd14abe962e14717c31e723e4c4", "v0.2.1"],
  [71, "aa46fee5cec632bdd34f90c36c1952286e1ec903", "v0.3.0"],
  [74, "0d9a5a169f8bfe6ae1924e7c0fdadc250a13eab0", "v0.5.0"],
  [76, "508f92cd1ea9d6e412a989a26de28d6de411227d", "v0.7.0"],
  [83, "86d1ee18781444ac5e5571cd43af9ae7b4a0eabb", "v0.8.0"],
  [84, "ef514fb40a470b43ebd63885ac99cdb7fdb464fd", "v0.9.0"],
  [85, "ee8941b8874132b8f83e4486b63ed5c19fc3f111", "rn/0.69-stable"],
  [89, "c9b539bf3d7bfa4143ff1a5751886c7b2dd728a2", "rn/0.70-stable"],
  [90, "b34632e6c603fb375ac4c8f423b2ee9cc45bed97", "rn/0.71-stable"],
  [94, "80afaefd8fde1e57aeeeaf56302740039f59fc27", "rn/0.72-stable"],
  [96, "5b4aa20c719830dcf5684832b89a6edb95ac3d64", "rn/0.76-stable"],
];

const LIST_PATH = "include/hermes/BCGen/HBC/BytecodeList.def";
const VERSION_PATHS = [
  "include/hermes/BCGen/HBC/BytecodeVersion.h",
  "include/hermes/BCGen/HBC/BytecodeFileFormat.h",
];
const OUTPUT = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "src",
  "hermes",
  "hermesOpcodeTables.ts",
);

/** One-letter operand codes consumed by src/hermes/HermesInstructions.ts. */
const OPERAND_CODES = {
  Reg8: "r",
  Reg32: "R",
  UInt8: "b",
  UInt16: "h",
  UInt32: "w",
  Addr8: "a",
  Addr32: "A",
  Imm32: "i",
  Double: "d",
};
const OPERAND_C_TYPES = {
  Reg8: "uint8_t",
  Reg32: "uint32_t",
  UInt8: "uint8_t",
  UInt16: "uint16_t",
  UInt32: "uint32_t",
  Addr8: "int8_t",
  Addr32: "int32_t",
  Imm32: "int32_t",
  Double: "double",
};
/** Jump macros expand to a short and a Long opcode; verified against each file. */
const JUMP_EXPANSIONS = {
  DEFINE_JUMP_1: [["Addr8"], ["Addr32"]],
  DEFINE_JUMP_2: [
    ["Addr8", "Reg8"],
    ["Addr32", "Reg8"],
  ],
  DEFINE_JUMP_3: [
    ["Addr8", "Reg8", "Reg8"],
    ["Addr32", "Reg8", "Reg8"],
  ],
};
const ID_KINDS = {
  OPERAND_STRING_ID: "s",
  OPERAND_FUNCTION_ID: "f",
  OPERAND_BIGINT_ID: "n",
};

/**
 * Upstream documents CallDirectLongIndex as "identical to CallDirect, but the
 * function index is 32-bit" without repeating OPERAND_FUNCTION_ID for it.
 */
const SUPPLEMENTAL_ID_KINDS = [["CallDirectLongIndex", 3, "f"]];

const fail = (message) => {
  throw new Error(message);
};

const gitShow = (repo, commit, path) =>
  execFileSync("git", ["-C", repo, "show", `${commit}:${path}`], {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    stdio: ["ignore", "pipe", "ignore"],
  });

const declaredVersion = (repo, commit) => {
  for (const path of VERSION_PATHS) {
    let text;
    try {
      text = gitShow(repo, commit, path);
    } catch {
      continue;
    }
    const match = /BYTECODE_VERSION = (\d+)/u.exec(text);
    if (match) return Number(match[1]);
  }
  return fail(`No BYTECODE_VERSION at ${commit}`);
};

const withoutComments = (text) =>
  text.replace(/\/\*[\s\S]*?\*\//gu, "").replace(/\/\/[^\n]*/gu, "");

/** Drop sections that default compiler builds do not enable. */
const withoutDisabledSections = (text) => {
  const lines = [];
  let skipping = false;
  for (const line of text.split("\n")) {
    if (/^\s*#\s*ifdef\s+HERMES_RUN_WASM\b/u.test(line)) {
      if (skipping) fail("Nested HERMES_RUN_WASM section");
      skipping = true;
      continue;
    }
    if (skipping) {
      if (/^\s*#\s*(if|ifdef|ifndef)\b/u.test(line))
        fail("Conditional inside HERMES_RUN_WASM section");
      if (/^\s*#\s*endif\b/u.test(line)) skipping = false;
      continue;
    }
    if (/^\s*#\s*(if|ifdef|else|elif)\b/u.test(line))
      fail(`Unhandled conditional: ${line.trim()}`);
    lines.push(line);
  }
  if (skipping) fail("Unterminated HERMES_RUN_WASM section");
  return lines.join("\n");
};

const checkOperandTypes = (text) => {
  const seen = new Map();
  for (const match of text.matchAll(
    /^DEFINE_OPERAND_TYPE\((\w+),\s*(\w+)\)/gmu,
  ))
    seen.set(match[1], match[2]);
  for (const [name, type] of Object.entries(OPERAND_C_TYPES))
    if (seen.get(name) !== type) fail(`Operand ${name} is not ${type}`);
  if (seen.size !== Object.keys(OPERAND_C_TYPES).length)
    fail(`Unexpected operand types: ${[...seen.keys()].join(", ")}`);
};

const checkJumpMacros = (text) => {
  for (const [macro, [short, long]] of Object.entries(JUMP_EXPANSIONS)) {
    const arity = macro.at(-1);
    const expected =
      `#define ${macro}(name) ` +
      `DEFINE_OPCODE_${arity}(name, ${short.join(", ")}) ` +
      `DEFINE_OPCODE_${arity}(name##Long, ${long.join(", ")}) ` +
      "DEFINE_JUMP_LONG_VARIANT(name, name##Long)";
    const start = text.indexOf(`#define ${macro}(name)`);
    if (start < 0) fail(`Missing ${macro}`);
    const end = text.indexOf(
      "DEFINE_JUMP_LONG_VARIANT(name, name##Long)",
      start,
    );
    const actual = text
      .slice(start, end + "DEFINE_JUMP_LONG_VARIANT(name, name##Long)".length)
      .replace(/\\\n/gu, " ")
      .replace(/\s+/gu, " ");
    if (actual !== expected) fail(`${macro} expands differently: ${actual}`);
  }
};

/** Remove preprocessor directives, including backslash-continued bodies. */
const withoutDirectives = (text) => {
  const kept = [];
  let continued = false;
  for (const line of text.split("\n")) {
    const directive = continued || /^\s*#/u.test(line);
    continued = directive && /\\\s*$/u.test(line);
    if (!directive) kept.push(line);
  }
  return kept.join("\n");
};

/** Opcode numbers are the order of DEFINE_OPCODE_N expansions in the file. */
const parseOpcodes = (source) => {
  const text = withoutDisabledSections(withoutComments(source));
  checkOperandTypes(text);
  checkJumpMacros(text);
  const opcodes = [];
  const byName = new Map();
  const add = (name, operands) => {
    if (byName.has(name)) fail(`Duplicate opcode ${name}`);
    for (const operand of operands)
      if (!(operand in OPERAND_CODES)) fail(`Unknown operand ${operand}`);
    const entry = { name, operands, kinds: operands.map(() => "") };
    byName.set(name, entry);
    opcodes.push(entry);
  };
  const body = withoutDirectives(text);
  for (const match of body.matchAll(/\b([A-Z][A-Z_0-9]*)\(([^()]*)\)/gu)) {
    const macro = match[1];
    const args = match[2]
      .split(",")
      .map((part) => part.trim())
      .filter((part) => part !== "");
    const opcode = /^DEFINE_OPCODE_(\d)$/u.exec(macro);
    if (opcode) {
      if (args.length !== Number(opcode[1]) + 1)
        fail(`${macro}(${args.join(", ")}) has the wrong arity`);
      add(args[0], args.slice(1));
    } else if (macro in JUMP_EXPANSIONS) {
      const [short, long] = JUMP_EXPANSIONS[macro];
      add(args[0], short);
      add(`${args[0]}Long`, long);
    } else if (macro in ID_KINDS) {
      const entry = byName.get(args[0]) ?? fail(`${macro} before ${args[0]}`);
      const position = Number(args[1]) - 1;
      if (!(position >= 0 && position < entry.operands.length))
        fail(`${macro}(${args.join(", ")}) is out of range`);
      entry.kinds[position] = ID_KINDS[macro];
    } else if (
      macro !== "DEFINE_RET_TARGET" &&
      macro !== "DEFINE_OPERAND_TYPE" &&
      !macro.startsWith("ASSERT_")
    ) {
      fail(`Unhandled macro ${macro}`);
    }
  }
  for (const [name, operand, kind] of SUPPLEMENTAL_ID_KINDS) {
    const entry = byName.get(name);
    if (entry === undefined) continue;
    if (entry.kinds[operand - 1] !== "")
      fail(`${name} operand ${operand} is now annotated upstream`);
    entry.kinds[operand - 1] = kind;
  }
  if (opcodes.length === 0 || opcodes.length > 256)
    fail(`Implausible opcode count ${opcodes.length}`);
  return opcodes;
};

const encodeTable = (opcodes) =>
  opcodes
    .map(
      ({ name, operands, kinds }) =>
        `${name}:${operands.map((operand, index) => OPERAND_CODES[operand] + kinds[index]).join(",")}`,
    )
    .join(" ");

const render = (repo) => {
  const tables = [];
  const versions = [];
  for (const [version, commit, ref] of HERMES_BYTECODE_SOURCES) {
    if (declaredVersion(repo, commit) !== version)
      fail(`${commit} does not declare bytecode version ${version}`);
    const encoded = encodeTable(parseOpcodes(gitShow(repo, commit, LIST_PATH)));
    let table = tables.indexOf(encoded);
    if (table < 0) table = tables.push(encoded) - 1;
    versions.push({ version, commit, ref, table });
  }
  const lines = [
    "// Generated by scripts/generate-hermes-opcode-tables.mjs. Do not edit.",
    "//",
    "// Opcode names, order and operand layouts are derived from",
    "// include/hermes/BCGen/HBC/BytecodeList.def in the Hermes source,",
    "// Copyright (c) Meta Platforms, Inc. and affiliates, MIT license.",
    "// See THIRD_PARTY_NOTICES.md and licenses/hermes-MIT.txt.",
    "",
    "/** Upstream commit that defines one released bytecode version. */",
    "export interface HermesOpcodeSource {",
    "  readonly version: number;",
    "  readonly commit: string;",
    "  readonly ref: string;",
    "  readonly table: number;",
    "}",
    "",
    "/** Released versions in ascending order; equal layouts share a table. */",
    "export const HERMES_OPCODE_SOURCES: readonly HermesOpcodeSource[] = [",
    // One property per line, matching the repository formatter's output.
    ...versions.flatMap(({ version, commit, ref, table }) => [
      "  {",
      `    version: ${version},`,
      `    commit: "${commit}",`,
      `    ref: "${ref}",`,
      `    table: ${table},`,
      "  },",
    ]),
    "];",
    "",
    "/**",
    " * Space-separated `Name:operands` in opcode order. Operand codes: r Reg8,",
    " * R Reg32, b UInt8, h UInt16, w UInt32, a Addr8, A Addr32, i Imm32,",
    " * d Double. A trailing s, f or n marks a string, function or bigint id.",
    " */",
    "export const HERMES_OPCODE_TABLES: readonly string[] = [",
    ...tables.map((table) => `  ${JSON.stringify(table)},`),
    "];",
    "",
  ];
  return lines.join("\n");
};

const main = () => {
  const args = process.argv.slice(2);
  const repoFlag = args.indexOf("--hermes-repo");
  const repo = repoFlag >= 0 ? args[repoFlag + 1] : undefined;
  if (repo === undefined)
    fail(
      "Usage: generate-hermes-opcode-tables.mjs --hermes-repo <dir> [--check]",
    );
  const rendered = render(repo);
  if (args.includes("--check")) {
    if (readFileSync(OUTPUT, "utf8") !== rendered)
      fail(`${OUTPUT} is stale; rerun without --check`);
    return;
  }
  writeFileSync(OUTPUT, rendered);
};

if (process.argv[1] === fileURLToPath(import.meta.url)) main();
