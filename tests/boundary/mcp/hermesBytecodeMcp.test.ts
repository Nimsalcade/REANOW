import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect } from "vitest";
import { toolContract } from "../../../src/contracts/toolContracts.js";
import { createServer } from "../../../src/server/createServer.js";
import { createTestBinarySession } from "../../fixtures/binarySession.js";
import {
  createHermesBytecode,
  sampleHermesFixture,
} from "../../fixtures/hermes/bytecode.js";
import { parseMcpToolError } from "../../fixtures/mcpToolError.js";
import { createTestTempDirectory } from "../../fixtures/temporaryDirectory.js";
import { mcpTest, type TestMcp } from "../../support/mcp/mcpFixture.js";

const connect = async (mcp: TestMcp) => {
  const session = createTestBinarySession(() => {
    throw new Error("Hermes bytecode must not start a deep provider");
  });
  const client = await mcp.connect(createServer({ kind: "session", session }));
  return { client, session };
};

mcpTest(
  "advertises the Hermes tools and follows a string to its function",
  async ({ mcp }) => {
    const { client, session } = await connect(mcp);
    try {
      const advertised = (await client.listTools()).tools.filter((tool) =>
        tool.name.endsWith("_hermes_bytecode"),
      );
      expect(advertised.map((tool) => tool.name).sort()).toEqual([
        "inspect_hermes_bytecode",
        "search_hermes_bytecode",
      ]);
      for (const tool of advertised)
        expect(tool.annotations).toMatchObject({
          readOnlyHint: false,
          idempotentHint: true,
          openWorldHint: false,
        });
      const root = await createTestTempDirectory("rea-hermes-mcp-");
      const path = join(root, "index.android.bundle");
      await writeFile(path, createHermesBytecode(sampleHermesFixture()));
      const searched = await client.callTool({
        name: "search_hermes_bytecode",
        arguments: { path, target: "strings", contains: "api.example.test" },
      });
      expect(searched.isError, JSON.stringify(searched.content)).not.toBe(true);
      const matches = toolContract("search_hermes_bytecode").outputSchema.parse(
        searched.structuredContent,
      );
      const functionId =
        matches.normalized_result.strings?.[0]?.referenced_by.function_ids[0];
      expect(functionId).toBe(1);
      const inspected = await client.callTool({
        name: "inspect_hermes_bytecode",
        arguments: { path, function_id: functionId },
      });
      expect(inspected.isError, JSON.stringify(inspected.content)).not.toBe(
        true,
      );
      const evidence = toolContract(
        "inspect_hermes_bytecode",
      ).outputSchema.parse(inspected.structuredContent);
      expect(evidence.normalized_result.disassembly).toMatchObject({
        function: { name: "login" },
        instructions: {
          total: 5,
          lines: expect.arrayContaining([
            '0\tLoadConstString r0, "https://api.example.test/login"',
          ]),
        },
      });
    } finally {
      await session.close();
    }
  },
);

mcpTest(
  "returns typed Hermes failures through the public error contract",
  async ({ mcp }) => {
    const { client, session } = await connect(mcp);
    try {
      const relative = await client.callTool({
        name: "inspect_hermes_bytecode",
        arguments: { path: "index.android.bundle" },
      });
      expect(relative.isError).toBe(true);
      expect(parseMcpToolError(relative).error).toMatchObject({
        code: "invalid_request",
        details: { operation: "inspect_hermes_bytecode" },
      });
      const root = await createTestTempDirectory("rea-hermes-mcp-");
      const path = join(root, "plain.bundle");
      await writeFile(path, "var __BUNDLE_START_TIME__ = 0;");
      const plain = await client.callTool({
        name: "search_hermes_bytecode",
        arguments: { path, target: "strings" },
      });
      expect(plain.isError).toBe(true);
      expect(parseMcpToolError(plain).error).toMatchObject({
        code: "unsupported_target",
        details: { operation: "search_hermes_bytecode", path },
      });
      expect(plain.structuredContent).toBeUndefined();
    } finally {
      await session.close();
    }
  },
);
