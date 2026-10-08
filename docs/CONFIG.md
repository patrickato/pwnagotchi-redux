# redux.config — the unified config.toml

One operator-facing file for the knobs that used to be scattered across per-module
defaults and JSON stores. Built on the fork's `defaults.toml` conventions: explicit
`>>> USER INPUT REQUIRED <<<` markers, no magic.

## Sections

| Section | Keys | Notes |
|---|---|---|
| `[cache]`   | `db_path`, `retention_days`, `max_rows` | the sighting Cache + its retention policy |
| `[web]`     | `bind_scope`, `port`, `token` | dashboard exposure; `token` required for any off-box bind |
| `[mesh]`    | `keystore`, `node_id` | sealed swarm-key store path + this device's id |
| `[persona]` | `default` | recon / red / blue / purple / mesh / sigint |
| `[vault]`   | `passphrase_env` | the **name** of the env var holding the at-rest passphrase |

## Principles

- **Partial files merge over defaults** — a sparse config is fine; unknown sections
  and keys are ignored rather than fatal (forward/backward tolerant).
- **Glass-box validation** — `validate()` returns *all* problems as readable strings
  at once (bad bind scope, off-box bind without a token, unknown persona, negative
  retention, empty node_id…), so `redux config check` shows everything wrong, not
  just the first thing.
- **No secrets in the file** — the dashboard token is the one low-value shared
  secret that lives here; the at-rest passphrase is referenced only by the *name* of
  the env var that holds it, never the value. `config show` masks the token.

## Use

```
redux config init                 # write a template to /etc/redux/config.toml
redux config init --out ./dev.toml
redux config check --config ./dev.toml    # list problems, nonzero exit if any
redux config show  --config ./dev.toml     # resolved values, token masked
```

Library: `AugurConfig.load(path)` / `AugurConfig.resolve(path_or_None)` →
defaults when absent; `.validate() -> [problems]`; `template() -> str`.

## Load-bearing

The config now drives the CLI. Pass `--config <path>` to a command and it fills the
relevant defaults; an explicit flag always wins, and with no `--config` behavior is
exactly as before (nothing silently changes):

- `redux web --config c.toml` → `[web]` bind_scope / port / token
- `redux cache stats|prune|export --config c.toml` → `[cache]` db_path, and prune's
  retention_days / max_rows
- `redux mesh key … --config c.toml` → `[mesh]` keystore (so `--store` is optional)
- `redux tft --config c.toml` → `[tft]` face_pack

Remaining: the on-device service that reads the config once at boot and wires it into
the long-running processes (so the swarm key auto-loads and the Cache prunes on a
schedule) — a systemd/runner concern that pairs with the first-run onboarding.
