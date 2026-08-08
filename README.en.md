# AI × 1C Guide

A Russian-first, community-driven decision guide for AI in the 1C:Enterprise ecosystem: Agent Skills, MCP servers, OData, Bitrix24 integrations, security boundaries, and reproducible checks.

[Russian guide](README.md) · [Verification matrix](VERIFICATION.md) · [Catalog v2](catalog/tools.json) · [Choose a stack](recipes/choose-stack.md) · [Contribute](CONTRIBUTING.md)

> This is not a ranking, certification, or security guarantee. Every catalog entry states whether it was reviewed from documentation, inspected as a release artifact, smoke-tested through a CLI/live endpoint, or tested end-to-end.

## Verified on 2026-08-08

- All 14 repositories exist, are public, and are not archived.
- Every entry now records an upstream commit, prerequisites, license status, access surface, known mutating/destructive operations, and evidence links.
- `cc-1c-skills` and `mcp-1c` passed limited local CLI smoke tests on macOS.
- `EDT-MCP` and `OpenIntegrations` release artifacts were inspected but not run inside EDT/1C.
- The hosted Bitrix24 documentation MCP passed initialize, tool listing, search, and method-details calls.

No listed 1C tool has yet passed this guide's complete end-to-end matrix with a real test database. See [VERIFICATION.md](VERIFICATION.md) for exact boundaries.

## Critical OData warning

The standard 1C OData interface is **not read-only**. It can expose create, update, delete, and document-posting operations. A read-only MCP tool name, system prompt, or hidden client control is not a security boundary.

A read-only pilot requires a dedicated 1C role without write permissions, a minimal published entity set, an optional server-side GET-only gateway, negative mutation tests in a disposable database, and a post-test data-integrity check. See the [official 1C Developer Guide](https://kb.1ci.com/1C_Enterprise_Platform/Guides/Developer_Guides/1C_Enterprise_8.3.23_Developer_Guide/Chapter_17._Integration_with_external_systems/17.4._Standard_OData_interface/17.4.1._General_information/?language=en).

## Safer starting points

| Goal | Starting point | Boundary to enforce |
|---|---|---|
| Work with exported source only | `cc-1c-skills` | Use a repository copy; enable database load/delete operations separately |
| Read configuration context | `mcp-1c` offline dump | A live database also requires a 1C extension and HTTP service |
| Work inside EDT | `EDT-MCP` | EDT 2026.1/2026.2 only; start with `Analysis Only` or `Code Review` |
| Index a large BSL repository | `code-index-mcp` | Install `bsl-indexer`; the standard npm `code-index` binary has no 1C support |
| Read Bitrix24 REST documentation | Official hosted `mcp-rest-doc` | Online-only; server source is not published; it does not access your portal |

## Safe adoption path

1. Work with exported source code and no business data.
2. Use a disposable test database with server-enforced least privilege.
3. Prove write attempts fail and verify checksums before production read access.
4. Enable writes only with dry runs, previews, explicit human approval, audit logs, and tested rollback.

See [SECURITY.md](SECURITY.md), the [stack selector](recipes/choose-stack.md), and the machine-readable [catalog](catalog/tools.json).

## Contributing

Add a project, reproduce a smoke test, or submit a verified compatibility note. Claims must link to an upstream source or include a reproducible test with version, environment, expected output, and limitations. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

This project is not affiliated with 1C Company or Bitrix24. Product names and trademarks belong to their respective owners.

## License

The guide and validation code are available under the [MIT License](LICENSE). Listed projects use their own licenses or may have no declared license.
