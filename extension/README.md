# Atlas VSCode Extension (M0)

Minimal client for the local Atlas daemon.

## What it does

- `Atlas: Chat` — prompts for a message, creates/reuses a session, calls `chat.send`, prints the answer in the `Atlas` output channel.
- `Atlas: Doctor` — calls `doctor` and prints the JSON.
- `Atlas: Initialize Project` — runs `atlas init --path <workspace>`.
- `Atlas: Start Local Server` — opens a terminal running `atlas serve --port 8765`.
- `Atlas: Set Token` — stores the daemon bearer token in VS Code `SecretStorage`.

This is intentionally a shell, not the final 4-panel UI. The next iteration adds
Webview chat, native diff review, plan approval and the task board.

## Build

```bash
cd extension
npm install
npm run compile
```

Then press `F5` in VSCode (Extension Development Host), or package:

```bash
npx @vscode/vsce package
code --install-extension atlas-vscode-0.0.1.vsix
```

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `atlas.endpoint` | `http://127.0.0.1:8765` | JSON-RPC daemon |
| `atlas.token` | empty | Deprecated fallback; prefer `Atlas: Set Token` |
| `atlas.requestTimeoutMs` | `300000` | Per-request timeout |
| `atlas.executable` | `atlas` | CLI path |

## Prereqs

```bash
pip install -e ".[dev]"
atlas init --path .
atlas serve --port 8765
```

The extension runs in the **workspace** extension host (`extensionKind: ["workspace"]`),
so in Remote SSH / WSL / Codespaces the daemon starts on the remote machine.

## Known limits

- No streaming yet; chat is request/response.
- No native diff/plan/task panel yet.
- No Marketplace publisher; use VSIX or F5.
- The daemon has no authentication by default on loopback; a non-loopback bind
  without `ATLAS_TOKEN` is refused. Set a token and use `Atlas: Set Token`.
