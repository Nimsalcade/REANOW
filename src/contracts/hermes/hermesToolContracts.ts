import {
  hermesBytecodeSchema,
  hermesSearchSchema,
  inspectHermesBytecodeInputSchema,
  searchHermesBytecodeInputSchema,
} from "../../domain/hermes/hermesBytecode.js";
import type { ToolContract } from "../toolContractTypes.js";
import { toolContractMetadata } from "../toolEffects.js";
import { evidenceResultOf } from "../toolOutputSchemaPrimitives.js";

/** Static Hermes bytecode reading is independent of native-analysis providers and JavaScript workers. */
export const HERMES_TOOL_CONTRACTS = [
  {
    name: "inspect_hermes_bytecode",
    ...toolContractMetadata("inspect_hermes_bytecode"),
    kind: "artifact-provider",
    description:
      "Inspect one explicit local Hermes bytecode file, the compiled JavaScript that React Native and Expo applications ship as index.android.bundle or main.jsbundle. Without function_id it returns the artifact SHA-256, bytecode version, the upstream commit its opcode layouts were generated from, whether decoding of that version was checked against real compiler output, the recorded source hash, footer hash and whether it matches, header options, table counts and section offsets. With function_id it also returns that function's header facts, exception handlers, the functions that create or directly call it, and one page of its disassembly as `byte_offset<TAB>Opcode operands` lines with string, function and jump-target operands resolved; continue with offset from instructions.next_offset. Decodes released bytecode versions 59, 60, 62, 71, 74, 76, 83, 84, 85, 89, 90, 94 and 96 without installing Hermes, executing the bundle, launching a process, writing temporary files or accessing the network. Disassembly is not decompilation: it does not reconstruct source, control-flow structure or variable names. A plain JavaScript bundle, delta-form bytecode and versions 98 and later are typed unsupported targets. Resource guards bound files to 256 MiB, decode work to four file lengths and the complete report to 8 MiB; malformed, unsupported and oversized inputs receive typed failures rather than partial success.",
    inputSchema: inspectHermesBytecodeInputSchema,
    outputSchema: evidenceResultOf(hermesBytecodeSchema),
    examples: [
      {
        title: "Identify a React Native bundle",
        input: { path: "/artifacts/app/assets/index.android.bundle" },
      },
      {
        title: "Disassemble one function",
        input: {
          path: "/artifacts/app/assets/index.android.bundle",
          function_id: 41583,
        },
      },
    ],
  },
  {
    name: "search_hermes_bytecode",
    ...toolContractMetadata("search_hermes_bytecode"),
    kind: "artifact-provider",
    description:
      "Search the string table or the function table of one explicit local Hermes bytecode file and return one page of matches in ascending table order. Target strings matches string values by substring and returns each string id, recorded kind, UTF-16 length, leading value, the functions that reference it and the functions it names, which is the direct route from a visible label, URL, key or error message to the code that uses it; pass a returned function id to inspect_hermes_bytecode. Target functions matches compiler-recorded function names and returns function-table rows. References cover string operands of instructions and the literals read by array and object literal instructions; a string used only inside a regular expression or built at runtime has no referrer, so an empty list is not proof that a string is unused. Each list holds at most 32 function ids with the exact distinct count. Omit contains to page through every entry; continue with offset from page.next_offset. Reads the file without installing Hermes, executing the bundle, launching a process, writing temporary files or accessing the network. Resource guards bound files to 256 MiB, decode work to four file lengths and the complete report to 8 MiB; an oversized page is a typed failure that names limit and max_value_units as the remedy.",
    inputSchema: searchHermesBytecodeInputSchema,
    outputSchema: evidenceResultOf(hermesSearchSchema),
    examples: [
      {
        title: "Find the code that uses an API host",
        input: {
          path: "/artifacts/app/assets/index.android.bundle",
          target: "strings",
          contains: "api.example.com",
        },
      },
      {
        title: "Find functions by name",
        input: {
          path: "/artifacts/app/assets/index.android.bundle",
          target: "functions",
          contains: "login",
        },
      },
    ],
  },
] as const satisfies readonly ToolContract[];
