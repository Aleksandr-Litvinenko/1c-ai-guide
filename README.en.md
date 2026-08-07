# AI × 1C Guide

A practical, community-driven guide to AI in the 1C:Enterprise ecosystem: Agent Skills, MCP servers, OData, Bitrix24 integrations, security patterns, and reproducible use cases.

[Russian guide](README.md) · [Machine-readable catalog](catalog/tools.json) · [Choose a stack](recipes/choose-stack.md) · [Contribute](CONTRIBUTING.md)

> This is a decision guide, not a ranking or certification. Review the source code, license, data flow, and permissions of every project before use.

## Who it is for

- **1C developers** who want AI-assisted BSL navigation, generation, build, and testing.
- **Analysts** who need controlled read-only access to 1C data through OData.
- **Product and engineering leaders** evaluating safe, useful AI workflows.
- **Integrators** connecting 1C, Bitrix24, external APIs, and local models.

## Start here

| Goal | Starting point |
|---|---|
| AI-assisted 1C development | [cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills) |
| Context from a live 1C configuration | [mcp-1c](https://github.com/feenlace/mcp-1c), [1c_mcp](https://github.com/vladimir-kharin/1c_mcp) |
| Work inside 1C:EDT | [EDT-MCP](https://github.com/DitriXNew/EDT-MCP) |
| Index a large BSL codebase | [code-index-mcp](https://github.com/Regsorm/code-index-mcp), [mcp-1c-v1](https://github.com/fserg/mcp-1c-v1) |
| Read-only business analytics | OData + a read-only MCP server |
| A protected gateway to business data | [1c-trusted-gateway](https://github.com/alonehobo/1c-trusted-gateway) |
| 1C and Bitrix24 integrations | [OpenIntegrations](https://github.com/Bayselonarrend/OpenIntegrations) |
| Current Bitrix24 REST documentation | [official mcp-rest-doc](https://github.com/bitrix24/mcp-rest-doc) |
| Full technical catalog of 1C MCP servers | [Awesome 1C MCP Servers](https://github.com/Untru/1c-mcp) |

## Safe adoption path

1. Work with exported source code and no business data.
2. Use a test database with a dedicated read-only account.
3. Move read-only workflows to production with allowlists, logs, and limits.
4. Enable writes only with dry runs, previews, explicit human approval, and audit logs.

See [SECURITY.md](SECURITY.md) for the complete baseline.

## Scope

The guide includes:

- scenario-based stack selection;
- a curated, machine-readable project catalog;
- practical recipes for development, business audits, and Bitrix24;
- security and data-governance checklists;
- contribution rules that require verifiable technical claims.

The project does not certify listed tools, accept paid placement, or recommend administrative access for LLM agents.

## Contributing

Add a project, improve a recipe, or submit a verified compatibility note. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

This project is not affiliated with 1C Company or Bitrix24. Product names and trademarks belong to their respective owners.

## License

The guide and its validation code are available under the [MIT License](LICENSE). Listed projects use their own licenses.
