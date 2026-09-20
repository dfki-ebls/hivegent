# Hivegent

Agentic system for non-expert users to interact with LLMs guided by experience.

A self-hosted, retrieval-augmented assistant: a React SPA over a FastAPI service that searches, reads, and reasons over the documents you upload, and cites what it used.

## Development

The Nix flake carries the whole environment, including PostgreSQL with `pgvector`.

```sh
nix develop          # dev shell for the backend, frontend, and bridge
nix run .#hivegent   # full dev stack: database, backend, frontend, egress proxy
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

## License

See [`LICENSE`](LICENSE).
