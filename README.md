# herdr-app-traex

Show TRAE CLI (`traecli` / `traex`) agent state in Herdr App by reporting
lifecycle events through Herdr Core's OSC 7501 protocol.

This plugin targets the current `herdr-app` + `hc` architecture. If you use the
legacy Herdr 0.9.x socket API, use
[`szrenwei/herdr-traex`](https://github.com/szrenwei/herdr-traex) instead.

## Requirements

- Herdr App with Herdr Core (`hc`) and OSC 7501 support
- TRAE CLI with plugin hooks support
- Python 3.9 or newer

## Install

```bash
traecli plugin install https://github.com/szrenwei/herdr-app-traex.git --type git
```

Restart TRAE CLI, open `/hooks`, review the `herdr-app-traex` hooks, and trust
them. TRAE CLI intentionally does not run a newly discovered non-managed hook
until it has been reviewed.

The plugin is additive: TRAE CLI merges plugin hooks with user and project
hooks, so it does not replace `~/.trae/cli/hooks.json`.

## State mapping

| TRAE CLI event | Herdr state |
| --- | --- |
| `SessionStart` | `idle` |
| `UserPromptSubmit`, `PreToolUse`, compaction | `working` |
| `PermissionRequest` | `blocked` (`kind=permission`) |
| `Notification: auth_prompt` | `blocked` (`kind=auth`) |
| `Stop` | `done` |
| `Interrupt` | `idle` |
| `SessionEnd` | `clear` |

Every non-clear report includes `app=traex`. The bridge writes directly to the
controlling TTY so hook JSON on stdout remains valid. If TRAE's embedded server
detaches a hook from its controlling TTY, the bridge walks the parent process
chain and accepts only safe `/dev/tty*` or `/dev/pts/*` devices. Terminals that
do not implement OSC 7501 ignore the private sequence. Missing TTYs and
malformed payloads are treated as no-ops and never block the agent. Set
`HERDR_TRAEX_DISABLE=1` before starting TRAE CLI to disable reporting.

## Verify

Validate the plugin and run its dependency-free test suite:

```bash
traecli plugin validate --path .
python3 -m unittest discover -s tests -v
```

After installation, start TRAE CLI inside Herdr App and submit a prompt. You can
inspect the terminal record with:

```bash
hc get "$HC_TERMINAL_ID"
```

The record should contain `"app": "traex"` and transition through `working`,
`blocked`, and `done` as the session changes.

## Development override

Automated tests can point `HERDR_TRAEX_TTY` at a temporary writable file.
Normal users should not set this variable.

## License

MIT
