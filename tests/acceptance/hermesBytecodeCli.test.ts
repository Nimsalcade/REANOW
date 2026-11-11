import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect } from "vitest";
import { toolContract } from "../../src/contracts/toolContracts.js";
import {
  createHermesBytecode,
  sampleHermesFixture,
} from "../fixtures/hermes/bytecode.js";
import { connectLocalToolsMcp } from "../fixtures/localToolsMcp.js";
import { createTestTempDirectory } from "../fixtures/temporaryDirectory.js";
import { cliTest } from "../support/cli/cliFixture.js";

cliTest(
  "follows a string to its function through the public CLI and stdio MCP",
  async ({ cli }) => {
    const root = await createTestTempDirectory("rea-hermes-cli-");
    const path = join(root, "index.android.bundle");
    await writeFile(path, createHermesBytecode(sampleHermesFixture()));
    const searched = await cli.run({
      arguments: [
        "search-hermes-bytecode",
        path,
        "--contains",
        "api.example.test",
        "--json",
      ],
    });
    expect(searched.exitCode, searched.stderr).toBe(0);
    const matches = toolContract("search_hermes_bytecode").outputSchema.parse(
      searched.json,
    );
    expect(matches.normalized_result).toMatchObject({
      target: "strings",
      page: { returned: 1, total: 1, next_offset: null },
      strings: [
        {
          string_id: 2,
          value: "https://api.example.test/login",
          referenced_by: { function_ids: [1], function_count: 1 },
        },
      ],
    });
    const inspected = await cli.run({
      arguments: [
        "inspect-hermes-bytecode",
        path,
        "--function-id",
        "1",
        "--limit",
        "2",
        "--json",
      ],
    });
    expect(inspected.exitCode, inspected.stderr).toBe(0);
    const evidence = toolContract("inspect_hermes_bytecode").outputSchema.parse(
      inspected.json,
    );
    expect(evidence.normalized_result).toMatchObject({
      bytecode: { version: 96 },
      file: { footer_hash_matches: true },
      disassembly: {
        function: { function_id: 1, name: "login" },
        instructions: {
          returned: 2,
          total: 5,
          next_offset: 2,
          lines: [
            '0\tLoadConstString r0, "https://api.example.test/login"',
            '4\tGetById r1, r0, 1, "fetch"',
          ],
        },
      },
    });
    const { client, call } = await connectLocalToolsMcp();
    const response = await call("inspect_hermes_bytecode", {
      path,
      function_id: 1,
      limit: 2,
    });
    expect(response.isError, JSON.stringify(response)).not.toBe(true);
    expect(response.structuredContent).toEqual(evidence);
    await client.ping();
  },
  30000,
);

cliTest(
  "rejects a plain JavaScript bundle and an unknown function",
  async ({ cli }) => {
    const root = await createTestTempDirectory("rea-hermes-cli-");
    const plain = join(root, "plain.bundle");
    await writeFile(plain, "var __BUNDLE_START_TIME__ = 0;");
    const unsupported = await cli.run({
      arguments: ["inspect-hermes-bytecode", plain, "--json"],
    });
    expect(unsupported.exitCode).toBe(1);
    expect(unsupported.json).toMatchObject({
      code: "unsupported_target",
      details: { operation: "inspect_hermes_bytecode", path: plain },
    });
    const path = join(root, "index.android.bundle");
    await writeFile(path, createHermesBytecode(sampleHermesFixture()));
    const unknown = await cli.run({
      arguments: [
        "inspect-hermes-bytecode",
        path,
        "--function-id",
        "9",
        "--json",
      ],
    });
    expect(unknown.exitCode).toBe(1);
    expect(unknown.json).toMatchObject({
      code: "invalid_request",
      details: {
        operation: "inspect_hermes_bytecode",
        issues: [{ path: ["function_id"], reason: "invalid_value" }],
      },
    });
    expect(unknown.json).not.toHaveProperty("normalized_result");
  },
  30000,
);
