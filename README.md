# llama-lite-top

![llama-lite-top-screen](screen.jp)

A lightweight, `btop`-style terminal monitor for [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`.

One Python file, standard library only, one-command install on a headless Debian box. It tells you whether the server is reading a prompt or generating, how fast, how full each slot's context is, and, when run on the same machine, how much CPU, RAM and swap are left.


## Install

On Debian, Ubuntu or any Linux with `curl`:

```bash
curl -fsSL https://raw.githubcontent.com/raffaeleMalagnini/llama-lite-top/main/install.sh | sudo bash
```

The installer:

1. installs `python3` with apt if it is missing;
2. downloads `llama-lite-top.py` and checks it against `SHA256SUMS`;
3. installs it as `/usr/local/bin/llama-lite-top`;
4. asks for the llama-server URL and API key, and saves them in `~/.config/llama-lite-top/config`, readable only by you.

Then run:

```bash
llama-lite-top
```

### Installer options

Pass options after `bash -s --`:

```bash
curl -fsSL .../install.sh | sudo bash -s -- --url http://127.0.0.1:8080 --api-key YOUR_KEY
```

| Option | Description |
| --- | --- |
| `--url URL` | llama-server address to save in the config file |
| `--api-key KEY` | llama-server API key to save in the config file |
| `--interval SEC` | default refresh interval to save in the config file |
| `--ref REF` | branch or tag to install (default `main`) |
| `--` | install in `~/.local/bin` for the current , without sudo |
| `--yes` | never ask questions (automation) |
| `--uninstall` | remove llama-lite-top; add `--purge` to delete the config file too |

Without root:

```bash
curl -fsSL .../install.sh | bash -s -- --
```

### Manual install

```bash
sudo install -m 755 llama-lite-top.py /usr/local/bin/llama-lite-top
```

Or just `python3 llama-lite-top.py`. From a cloned repository, `sudo ./install.sh` installs the local copy.

## Preparing llama-server

Start `llama-server` with `--metrics`. Without it llama-lite-top still works, but queue counters, last-request speeds and server averages are missing.

```bash
llama-server -m model.gguf --port 8080 --metrics
```

If the server uses `--api-key`, llama-lite-top needs the same key: only `/health` is public, while `/slots`, `/props` and `/metrics` require it. The `/slots` endpoint must not be disabled with `--no-slots`.

## Usage

```bash
llama-lite-top                                    # config file or http://127.0.0.1:8080
llama-lite-top --url http://192.168.1.50:8080     # another server
llama-lite-top --api-key YOUR_KEY --interval 2
llama-lite-top --version
```

Settings are read in this order, first match wins:

1. command-line options: `--url`, `--api-key`, `--interval`
2. environment variables: `LLAMA_URL`, `LLAMA_API_KEY`
3. config file `~/.config/llama-lite-top/config`:

```ini
url=http://127.0.0.1:8080
api_key=YOUR_KEY
interval=1
```

| Key | Action |
| --- | --- |
| `q` / `Esc` | quit |
| `+` / `-` | refresh interval up / down (0.5 to 10 s) |

## What it shows

| Panel | Contents | Source |
| --- | --- | --- |
| server | status (ready, reading prompt, generating, loading, offline), model file, context size, busy slots, queued and running requests | `/health`, `/props`, `/slots`, `/metrics` |
| throughput | live generation speed, live prompt-reading speed, last finished request, server averages, history sparkline | `/slots`, `/metrics` |
| slots | per slot: phase, context bar (green; yellow above 70%; red above 90%), tokens used / context, tokens generated, current t/s | `/slots` |
| host | CPU, RAM and swap of the machine running llama-lite-top; shown only when `--url` points to localhost | `/proc` |

Prompt reading and generation are shown separately on purpose. On CPU-only machines prompt reading is often the slower phase: a long RAG prompt can take a minute or more before the first token, even when generation looks fast.

### How speeds are computed

- **Live generation / live prompt reading**: increase in generated tokens or context tokens between two polls, divided by the elapsed time.
- **Last request**: change in the `tokens_predicted_total` and `prompt_tokens_total` counters divided by the change in their `*_seconds_total` counters.
- **Server average**: the `predicted_tokens_seconds` and `prompt_tokens_seconds` values published by llama-server.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `● OFFLINE` | wrong URL or port, server not running, or a firewall in between; test with `curl <url>/health` |
| model shows `?` and "/slots unavailable" | API key missing or wrong |
| "Metrics unavailable" | llama-server started without `--metrics` |
| server average not shown yet | llama-server publishes it only after a request completes |
| live prompt reading stays at `—` | your llama.cpp build updates the slot's token count only when the prompt is done; the value appears under *Last request* |
| a slot column stays at `—` | `/slots` field names change between llama.cpp builds; open an issue with the output of `curl -s <url>/slots` |
| no host panel | it appears only when `--url` points to `localhost` or `127.x` |
| installer: "checksum mismatch" | the download was corrupted or modified; retry, and open an issue if it persists |

## Requirements

- Linux, Python 3.8 or newer (`curses` ships with `python3` on Debian and Ubuntu)
- a UTF-8 terminal of at least 50×12 characters
- `llama-server` from a recent llama.cpp build

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later).

You can use, study, modify and share llama-lite-top freely. If you distribute a modified version, or make one available to others over a network, you must release its source code under the same license.

llama-lite-top is an independent project. It is not affiliated with or endorsed by Meta, ggml-org or the llama.cpp project. "Llama" is a trademark of Meta Platforms, Inc.
