# Operator Model Routing Notes

Use OpenAI Codex as the default implementation lane and use Claude Code as a
read-only review lane when you want a second opinion on local work.

## Claude Review Helper

Run:

```bash
hermes claude-review
```

or its alias:

```bash
hermes codex-review
```

The helper shells out to the local `claude -p` CLI, defaults to
`claude-opus-5`, and uses the operator's existing Claude Code login. It does
not route through Hermes' `provider: anthropic` adapter and does not require an
`ANTHROPIC_API_KEY`.

By default, the helper attaches the current git diff, includes untracked text
files, runs with `--permission-mode plan`, and passes an empty strict MCP config
so review runs stay read-only and avoid project MCP startup side effects. Use
`--allow-mcp` only when a review intentionally needs Claude Code MCP context.

Keep these lanes separate:

- `openai-codex`: implementation, editing, tool execution, and normal agent work.
- `hermes claude-review`: local Claude Code / Claude Max review of Codex or
  Hermes changes.
- `provider: anthropic`: Anthropic API-compatible runtime lane; use it only when
  explicitly testing API-provider behavior or API-key-backed Claude access.
