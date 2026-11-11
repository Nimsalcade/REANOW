# React Native Hermes bytecode

React Native and Expo applications built with the Hermes engine ship their
JavaScript as compiled bytecode, usually `assets/index.android.bundle` inside
an APK or `main.jsbundle` inside an iOS application bundle. The JavaScript
tools cannot read that file. `search_hermes_bytecode` and
`inspect_hermes_bytecode` read it directly, without installing Hermes, running
the bundle, launching a process, or opening a disassembler.

The usual route from something visible in the application to the code behind
it is two calls. Search for a string, then disassemble a function that
references it:

```bash
rea search-hermes-bytecode /absolute/path/to/index.android.bundle \
  --contains api.example.com --json
rea inspect-hermes-bytecode /absolute/path/to/index.android.bundle \
  --function-id 41583 --json
```

The CLI resolves relative paths against the caller's working directory. MCP
requires an absolute filesystem path and preserves that exact selection:

```json
{
  "name": "search_hermes_bytecode",
  "arguments": {
    "path": "/artifacts/app/assets/index.android.bundle",
    "target": "strings",
    "contains": "api.example.com"
  }
}
```

Extract the bundle from its APK or IPA first; both are ZIP archives. A file
that starts with JavaScript text is a plain bundle and belongs to
[application analysis](javascript-application-workflows.md).

## Search

`target: "strings"` matches string-table values by substring,
case-insensitively unless `match_case` is true. Each match has its `string_id`,
recorded `kind` (`string`, `identifier` for property and variable names, or
`predefined` in versions before 71), complete `utf16_length`, the leading
`value`, and two function lists:

- `referenced_by`, the functions with an instruction operand or an array or
  object literal that uses the string;
- `names_functions`, the functions whose compiler-recorded name is the string.

Each list holds at most 32 function ids in ascending order together with the
exact count of distinct functions. `string_kind` restricts the search to one
kind. `max_value_units` sets how many leading UTF-16 code units of each value
are returned (default 240); `value_truncated` reports whether more exist.

`target: "functions"` matches compiler-recorded function names and returns
function-table rows. Minified bundles leave most functions unnamed, so a
string search is normally the better starting point.

Omit `contains` to page through every entry. Results are in ascending table
order; continue with `offset` set to `page.next_offset`.

## Inspect

Without `function_id` the result identifies the file: bytecode version, the
upstream Hermes commit its opcode layouts were generated from, the recorded
source hash, the footer hash and whether it equals the SHA-1 of the preceding
bytes, header options, table counts and the offset and size of every section.

With `function_id` the result adds one function's header facts, exception
handlers, `referenced_by` (functions that create it as a closure or call it
directly), and one page of instructions. Each line is
`byte_offset<TAB>Opcode operands`:

```text
13	NewObject r11
18	PutNewOwnById r11, r1, "code"
23	LoadConstStringLongIndex r1, "authorization_code"
40	Call1 r1, r1, r2
88	JmpUndefined L159, r10
```

Registers are `r<n>`. String operands are quoted and elided after 96 code
units. Function operands are `fn#<id>` followed by the recorded name when there
is one. Jump targets are `L<byte offset>` within the same function, and
`SwitchImm` lists its jump-table targets. Continue with `offset` set to
`instructions.next_offset`; `limit` defaults to 400 instructions.

## Versions

Hermes changes its opcode numbering between bytecode versions, so each
released version has its own table. The tables are generated from
`BytecodeList.def` at a pinned Hermes commit per version by
`scripts/generate-hermes-opcode-tables.mjs`.

| Bytecode version | Hermes / React Native release line | `decoder_verification` |
| ---------------- | ---------------------------------- | ---------------------- |
| 59               | Hermes 0.1.0                       | `real-bundle`          |
| 60               | Hermes 0.1.1                       | `source-derived`       |
| 62               | Hermes 0.2.1                       | `real-bundle`          |
| 71               | Hermes 0.3.0                       | `source-derived`       |
| 74               | Hermes 0.4 to 0.6                  | `real-bundle`          |
| 76               | Hermes 0.7                         | `real-bundle`          |
| 83               | Hermes 0.8.0                       | `source-derived`       |
| 84               | Hermes 0.8.1 to 0.11               | `real-bundle`          |
| 85               | React Native 0.69                  | `source-derived`       |
| 89               | React Native 0.70                  | `real-bundle`          |
| 90               | React Native 0.71                  | `real-bundle`          |
| 94               | React Native 0.72                  | `real-bundle`          |
| 96               | React Native 0.73 to 0.84          | `real-bundle`          |

`real-bundle` means decoding of that version was checked against bundles
produced by a real Hermes compiler: every function decoded to exactly its
recorded size, and every jump target, exception-handler bound and string,
function and bigint operand was in range. `source-derived` versions use the
same generator and file-layout code but were not checked against compiler
output. The check was a development measurement on public bundles; the
repository's tests use synthetic files and do not repeat it.

Versions 98 and later (the Hermes V1 compiler) use a different file layout and
are a typed unsupported target, as are delta-form bytecode and any version not
listed above.

## Limits and failures

The selected file is limited to 256 MiB and one report to 8 MiB. A page that
exceeds the report limit is a typed failure that names `limit` and
`max_value_units` as the remedy. A function id outside the function table and
an instruction offset past the end of a function are input failures on that
field. Truncated tables, undefined opcodes, and instructions that cross the
end of their function are malformed-input failures rather than partial
results.

Function headers may share one bytecode body, so a crafted file can ask for
far more decoding than its size suggests. One operation may decode at most
four file lengths of instructions and literals (at least 16 MiB); beyond that
it fails with a `cpu` resource constraint. Bundles from real compilers used
under a sixth of that allowance when measured.

Disassembly is not decompilation. It does not reconstruct JavaScript source,
control-flow structure, variable names or types. Debug information,
regular-expression bytecode, bigint values and the CommonJS module table are
located in `sections` but are not decoded. A string used only inside a regular
expression or built at runtime has no referrer, so empty function lists are
not proof that a string is unused. Opcodes that exist only in compilers built
with `HERMES_RUN_WASM` are not in the tables.

Strings, names and hashes are file content. They do not authenticate the
application or its build.
