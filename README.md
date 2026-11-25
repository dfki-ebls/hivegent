# Hivegent

Agentic system for non-expert users to interact with LLMs guided by experience.

A self-hosted, retrieval-augmented assistant: a React SPA over a FastAPI service that searches, reads, and reasons over the documents you upload, and cites what it used.

> [!IMPORTANT]
> **Additional terms of use:** Hivegent is provided under the [MIT license](LICENSE) and the DFKI's [additional terms of use](TERMS).
> By downloading, installing, or using the software, you accept the additional terms, please read them in full beforehand.

## Development

The Nix flake carries the whole environment, including PostgreSQL with `pgvector`.

```sh
nix develop          # dev shell for the backend, frontend, and bridge
nix run .#hivegent   # full dev stack: database, backend, frontend, egress proxy, SearXNG
nix flake check      # everything CI runs
```

## Documentation

| Where                                      | What it covers                                                        |
| ------------------------------------------ | --------------------------------------------------------------------- |
| [`docs/`](docs/src/en/README.md)           | The user manual (mdBook, English and German): concepts, setup, usage. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md)       | How the pieces fit together, the agent's features, asset processing.  |
| [`backend/README.md`](backend/README.md)   | Backend invariants: storage, conversations, tools, Python sandbox.    |
| [`frontend/README.md`](frontend/README.md) | Client-side decisions: chat history, settings, approvals.             |
| [`bridge/README.md`](bridge/README.md)     | The Teams / Chat SDK bridge service.                                  |
| [`AGENTS.md`](AGENTS.md)                   | House rules for coding agents.                                        |

## Roadmap

### Next

- Constrain cbrkit to `>=2,<3` and exar to `>=1,<2` and drop the editable path sources once both are released, which the Nix builds need.
- Run the PostgreSQL-specific paths against a live database: the global workspace migration, the advisory locks of reset and the journal fencing, the source lease claims and tables, the owner subquery of search, `DocumentMemory.read`, and the expected-page check of `UserMemory.write`.
- Pin the new revision in raise-infra and drop its CSP workaround and `PrivateDevices` override.

### Later

- Adopt the `CodeMode` capability of pydantic-ai-harness for `run_python`, and its `RepairToolArguments` where it covers `exar.pydantic_ai.LenientArguments`.
- Cache the function schemas of toolsets if their generation of about 30 ms per run shows in traces.
- Subagents that outlive their turn, which need a `DelegationTasks` owner of pydantic-ai-harness kept across turns and a place to render their later turns.
- Withhold subagents per request, and bound the total cost of a turn also where models report no cost.
- Let a second model approve low-risk calls of `execute` tools with the `ApprovalReviewer` of pydantic-ai-harness, so subagents can run them instead of denying them, while writes into shared workspaces and memory stay with a person.
- Delegated per-user auth for external services, starting with Entra on-behalf-of, then token exchange and MCP enterprise-managed auth.
- MCP Apps rendering, catalog tools for subagents, and subagents over MCP.
- Per-user identity for the Teams bridge.
- Operator and assistant tool rules with exar's `Rules`, such as asking before `web_fetch` or denying a plugin tool, combined with the mode by `Strictest` in `HOST_GATE` so a rule only tightens, which also folds `tools.disabled` and an assistant's `disabled_tools` into `deny` rules and holds the sandbox's functions to the same decisions.
- Approvals of gateway writes per root as exar actions with a `target`, decided by a root policy within the same `Strictest`, so every approval comes from one policy and rules can name paths.
- Full multi-replica support: workspace locks as PostgreSQL advisory transaction locks with an in-flight table, the job and list feeds over LISTEN/NOTIFY with a jobs row, sticky routing of turns by conversation, shared rate-limit buckets, a migration lock, and a TTL on the maintenance flag. Source syncs already run on a lease any process may claim.
- Scheduled maintenance of case bases.
- Audit log, usage aggregation, quotas, and retention policies.
- Scheduled runs, webhooks, and notifications.
- Reply feedback feeding RAGold, office document output, and tool generation from OpenAPI.

## License

See the [license](LICENSE) and the [additional terms](TERMS).
