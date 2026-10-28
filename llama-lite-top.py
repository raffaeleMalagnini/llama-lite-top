#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Raffaele Malagnini
#
# llama-lite-top is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version. It is distributed WITHOUT ANY WARRANTY; see the
# GNU Affero General Public License for details: <https://www.gnu.org/licenses/>.
"""
llama-lite-top: a lightweight, btop-style terminal monitor for llama.cpp's
llama-server.

Shows server status, model, slots with context usage and tokens/s, prompt
reading vs. generation speed, queued requests and, when run on the same
machine, host CPU, RAM and swap. Python 3 standard library only.

USAGE
    llama-lite-top                              # http://127.0.0.1:8080
    llama-lite-top --url http://192.168.1.50:8080 --api-key KEY
    llama-lite-top --version

SETTINGS (highest priority first)
    1. command-line options
    2. environment: LLAMA_URL, LLAMA_API_KEY
    3. config file: ~/.config/llama-lite-top/config  (url=, api_key=, interval=)

KEYS
    q / Esc  quit        + / -  refresh interval

Start llama-server with --metrics to get throughput counters.
"""
import argparse
import curses
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque

__version__ = "1.0.0"

DEFAULT_URL = "http://127.0.0.1:8080"
SPARK = " ▁▂▃▄▅▆▇█"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "llama-lite-top", "config")


def load_config(path):
    """Read simple KEY=VALUE lines; unknown keys and comments are ignored."""
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                cfg[key.strip().lower()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return cfg


# --------------------------------------------------------------------------- #
# llama-server client
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self, url, api_key=None, timeout=2.0):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout

    def _get(self, path):
        req = urllib.request.Request(self.url + path, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def json(self, path):
        try:
            _, body = self._get(path)
            return json.loads(body)
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def health(self):
        try:
            status, body = self._get("/health")
            try:
                return json.loads(body).get("status", "ok")
            except ValueError:
                return "ok" if status == 200 else f"http {status}"
        except urllib.error.HTTPError as e:
            # 503 means the model is still loading
            return "loading" if e.code == 503 else f"http {e.code}"
        except (urllib.error.URLError, OSError):
            return None

    def metrics(self):
        try:
            _, body = self._get("/metrics")
        except (urllib.error.URLError, OSError):
            return {}
        out = {}
        for line in body.splitlines():
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_:][\w:]*)(?:\{[^}]*\})?\s+([-+0-9.eE]+|NaN|nan)", line)
            if m:
                try:
                    out[m.group(1).replace("llamacpp:", "")] = float(m.group(2))
                except ValueError:
                    pass
        return out


# --------------------------------------------------------------------------- #
# Local host stats (only meaningful when running on the model's machine)
# --------------------------------------------------------------------------- #
class Host:
    def __init__(self):
        self.prev = self._cpu_times()

    @staticmethod
    def _cpu_times():
        try:
            with open("/proc/stat") as f:
                vals = list(map(int, f.readline().split()[1:]))
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return idle, sum(vals)
        except OSError:
            return None

    def cpu(self):
        cur = self._cpu_times()
        if not cur or not self.prev:
            return None
        di, dt = cur[0] - self.prev[0], cur[1] - self.prev[1]
        self.prev = cur
        return 0.0 if dt <= 0 else 100.0 * (1 - di / dt)

    @staticmethod
    def mem():
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0])
            total, avail = info["MemTotal"], info["MemAvailable"]
            swap_used = info.get("SwapTotal", 0) - info.get("SwapFree", 0)
            return total, total - avail, swap_used
        except (OSError, KeyError, ValueError):
            return None


# --------------------------------------------------------------------------- #
# Slot parsing (field names change between llama.cpp builds)
# --------------------------------------------------------------------------- #
def slot_view(s):
    nt = s.get("next_token")
    if isinstance(nt, list):
        nt = nt[0] if nt else {}
    nt = nt or {}
    return {
        "id": s.get("id", "?"),
        "busy": bool(s.get("is_processing", s.get("state", 0) not in (0, "idle"))),
        "n_ctx": s.get("n_ctx"),
        "n_past": s.get("n_past", s.get("n_tokens", s.get("n_prompt_tokens"))),
        "decoded": nt.get("n_decoded", s.get("n_decoded")),
    }


def human_kb(kb):
    return f"{kb / 1048576:.1f}G" if kb >= 1048576 else f"{kb / 1024:.0f}M"


def bar(frac, width):
    frac = max(0.0, min(1.0, frac or 0.0))
    full = int(round(frac * width))
    return "█" * full + "░" * (width - full)


def sparkline(values, width):
    vals = list(values)[-width:]
    if not vals:
        return ""
    top = max(vals) or 1.0
    return "".join(SPARK[min(8, int(v / top * 8))] for v in vals)


def is_local(url):
    host = url.split("//")[-1]
    return host.startswith(("127.", "localhost", "0.0.0.0", "[::1]"))


# --------------------------------------------------------------------------- #
# User interface
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, args):
        self.cli = Client(args.url, args.api_key)
        self.interval = args.interval
        self.host = Host()
        self.local = is_local(args.url)
        self.hist = deque(maxlen=240)
        self.prev_decoded = {}
        self.prev_past = {}
        self.prev_metrics = None
        self.last_req_tps = None
        self.last_pp_tps = None
        self.props = None
        self.props_at = 0

    def poll(self):
        now = time.monotonic()
        health = self.cli.health()
        if health is None:
            return {"online": False}

        if self.props is None or now - self.props_at > 30:
            self.props = self.cli.json("/props") or {}
            self.props_at = now

        raw_slots = self.cli.json("/slots")
        slots = [slot_view(s) for s in raw_slots] if isinstance(raw_slots, list) else None
        metrics = self.cli.metrics()

        # Live speed per slot from the increase between two polls:
        # generated tokens ("gen" phase) and prompt tokens read ("prompt" phase).
        live = live_pp = 0.0
        for s in slots or []:
            key, dec, past = s["id"], s["decoded"], s["n_past"]
            s["tps"] = s["pp_tps"] = None
            s["phase"] = "idle"
            if not s["busy"]:
                self.prev_decoded.pop(key, None)
                self.prev_past.pop(key, None)
                continue
            s["phase"] = "gen" if isinstance(dec, (int, float)) and dec > 0 else "prompt"
            if isinstance(dec, (int, float)):
                prev = self.prev_decoded.get(key)
                if prev and dec > prev[0] and now > prev[1]:
                    s["tps"] = (dec - prev[0]) / (now - prev[1])
                    live += s["tps"]
                self.prev_decoded[key] = (dec, now)
            if s["phase"] == "prompt" and isinstance(past, (int, float)):
                prev = self.prev_past.get(key)
                if prev and past > prev[0] and now > prev[1]:
                    s["pp_tps"] = (past - prev[0]) / (now - prev[1])
                    live_pp += s["pp_tps"]
                self.prev_past[key] = (past, now)
            else:
                self.prev_past.pop(key, None)
        self.hist.append(live)

        # Speed of the last finished request, from the Prometheus counters
        if metrics and self.prev_metrics:
            pm = self.prev_metrics
            dt_tok = metrics.get("tokens_predicted_total", 0) - pm.get("tokens_predicted_total", 0)
            dt_sec = metrics.get("tokens_predicted_seconds_total", 0) - pm.get("tokens_predicted_seconds_total", 0)
            if dt_tok > 0 and dt_sec > 0:
                self.last_req_tps = dt_tok / dt_sec
            dp_tok = metrics.get("prompt_tokens_total", 0) - pm.get("prompt_tokens_total", 0)
            dp_sec = metrics.get("prompt_seconds_total", 0) - pm.get("prompt_seconds_total", 0)
            if dp_tok > 0 and dp_sec > 0:
                self.last_pp_tps = dp_tok / dp_sec
        if metrics:
            self.prev_metrics = metrics

        return {"online": True, "health": health, "slots": slots, "metrics": metrics,
                "live": live, "live_pp": live_pp}

    # -- drawing -------------------------------------------------------------
    def draw(self, scr, d):
        scr.erase()
        h, w = scr.getmaxyx()
        if h < 12 or w < 50:
            scr.addstr(0, 0, "Terminal too small (min 50x12)")
            return

        def put(y, x, text, attr=0):
            if 0 <= y < h and x < w:
                try:
                    scr.addnstr(y, x, text, max(0, w - x - 1), attr)
                except curses.error:
                    pass

        def box(y, x, bh, bw, title):
            try:
                win = scr.derwin(bh, bw, y, x)
                win.attron(curses.color_pair(4))
                win.box()
                win.attroff(curses.color_pair(4))
                put(y, x + 2, f" {title} ", curses.color_pair(5) | curses.A_BOLD)
            except curses.error:
                pass

        title = f"llama-lite-top {__version__}"
        put(0, 1, title, curses.A_BOLD | curses.color_pair(5))
        put(0, len(title) + 3, self.cli.url, curses.color_pair(4))
        put(0, w - 28, time.strftime("%H:%M:%S") + f"  refresh {self.interval:.1f}s", curses.color_pair(4))
        footer = "q quit  +/- refresh interval"

        if not d["online"]:
            put(2, 2, "● OFFLINE", curses.color_pair(3) | curses.A_BOLD)
            put(3, 2, "llama-server is not responding, retrying...")
            put(h - 1, 1, footer, curses.color_pair(4))
            return

        p = self.props or {}
        gen = p.get("default_generation_settings") or {}
        model = os.path.basename(str(p.get("model_path", "") or gen.get("model", "") or "?"))
        n_ctx = gen.get("n_ctx") or p.get("n_ctx")
        slots = d["slots"]
        busy = sum(1 for s in slots if s["busy"]) if slots else 0
        state = d["health"]
        st_attr = curses.color_pair(1) if state == "ok" else curses.color_pair(2)
        phases = {s["phase"] for s in slots or []}
        st_txt = ("reading prompt" if phases == {"prompt"} else "generating" if busy
                  else ("ready" if state == "ok" else state))

        # --- server panel
        box(1, 0, 6, w, "server")
        put(2, 2, "Status")
        put(2, 11, f"● {st_txt}", st_attr | curses.A_BOLD)
        put(3, 2, f"Model    {model}")
        total_slots = p.get("total_slots", len(slots) if slots else "?")
        put(4, 2, f"Context  {n_ctx or '?'} tokens   Slots {busy}/{total_slots} busy")
        m = d["metrics"]
        if m:
            put(5, 2, f"Queued   {int(m.get('requests_deferred', 0))}   "
                      f"Running {int(m.get('requests_processing', 0))}")
        else:
            put(5, 2, "Metrics unavailable: start llama-server with --metrics", curses.color_pair(2))

        # --- throughput panel
        box(7, 0, 6, w, "throughput")
        live = d["live"]
        put(8, 2, "Live generation")
        put(8, 20, f"{live:6.1f} t/s" if live else "     — t/s", curses.color_pair(1) | curses.A_BOLD)
        if d.get("live_pp"):
            put(8, 34, f"Live prompt reading {d['live_pp']:6.1f} t/s", curses.color_pair(2) | curses.A_BOLD)
        last = f"{self.last_req_tps:6.1f} t/s" if self.last_req_tps else "     —"
        pp = f"{self.last_pp_tps:6.1f} t/s" if self.last_pp_tps else "     —"
        put(9, 2, f"{'Last request':<18}{last}   Prompt {pp}")
        if m:
            g, q = m.get("predicted_tokens_seconds", 0), m.get("prompt_tokens_seconds", 0)
            if g or q:
                put(10, 2, f"{'Server average':<18}gen {g:6.1f} t/s   prompt {q:6.1f} t/s")
            else:
                put(10, 2, f"{'Server average':<18}available after the first completed request",
                    curses.color_pair(4))
        put(11, 2, sparkline(self.hist, w - 5), curses.color_pair(1))

        # --- slots panel
        y = 13
        rows = max(1, min(len(slots) if slots else 1, h - y - 6 if self.local else h - y - 3))
        box(y, 0, rows + 3, w, "slots")
        bw = max(10, w - 52)
        put(y + 1, 2, f"{'ID':>3}  {'state':<9}{'context':<{bw}} {'used':>12}  {'gen':>6}  {'t/s':>6}",
            curses.A_BOLD)
        if slots is None:
            put(y + 2, 2, "/slots unavailable: API key missing, or server started with --no-slots",
                curses.color_pair(2))
        else:
            for i, s in enumerate(slots[:rows]):
                yy = y + 2 + i
                sn_ctx = s["n_ctx"] or n_ctx
                used = s["n_past"] if isinstance(s["n_past"], (int, float)) else None
                frac = (used / sn_ctx) if (used is not None and sn_ctx) else 0
                color = (curses.color_pair(3) if frac > 0.9 else
                         curses.color_pair(2) if frac > 0.7 else curses.color_pair(1))
                put(yy, 2, f"{s['id']:>3}  ")
                lbl = {"gen": "● gen", "prompt": "● prompt"}.get(s["phase"], "○ idle")
                col = {"gen": curses.color_pair(1), "prompt": curses.color_pair(2)}.get(
                    s["phase"], curses.color_pair(4))
                put(yy, 7, f"{lbl:<9}", col)
                put(yy, 16, bar(frac, bw), color)
                used_txt = f"{used}/{sn_ctx}" if used is not None else f"—/{sn_ctx or '?'}"
                put(yy, 17 + bw, f"{used_txt:>12}")
                dec = s["decoded"] if isinstance(s["decoded"], (int, float)) else "—"
                put(yy, 31 + bw, f"{dec:>6}")
                speed = s.get("tps") or s.get("pp_tps")
                put(yy, 39 + bw, f"{speed:6.1f}" if speed else f"{'—':>6}",
                    curses.color_pair(2) if s.get("pp_tps") and not s.get("tps") else 0)
            y = y + rows + 3

        # --- host panel (local only)
        if self.local and y + 3 <= h - 1:
            box(y, 0, 3, w, "host")
            cpu = self.host.cpu()
            mem = self.host.mem()
            half = max(10, (w - 40) // 2)
            if cpu is not None:
                put(y + 1, 2, f"CPU {cpu:5.1f}% ")
                put(y + 1, 14, bar(cpu / 100, half), curses.color_pair(1))
            if mem:
                total, used, swap = mem
                x = 16 + half
                put(y + 1, x, f"RAM {human_kb(used)}/{human_kb(total)} ")
                put(y + 1, x + 16, bar(used / total, max(5, half - 16)),
                    curses.color_pair(3) if used / total > 0.9 else curses.color_pair(1))
                if swap > 0:
                    put(y + 1, w - 14, f"swap {human_kb(swap)}", curses.color_pair(2))

        put(h - 1, 1, footer, curses.color_pair(4))

    def run(self, scr):
        curses.curs_set(0)
        curses.use_default_colors()
        for i, c in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED,
                               curses.COLOR_BLUE, curses.COLOR_CYAN), start=1):
            curses.init_pair(i, c, -1)
        scr.timeout(100)
        next_poll = 0.0
        data = {"online": False}
        while True:
            now = time.monotonic()
            if now >= next_poll:
                data = self.poll()
                next_poll = now + self.interval
                self.draw(scr, data)
                scr.refresh()
            key = scr.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (ord("+"), ord("=")):
                self.interval = min(10.0, self.interval + 0.5)
            elif key in (ord("-"), ord("_")):
                self.interval = max(0.5, self.interval - 0.5)
            elif key == curses.KEY_RESIZE:
                self.draw(scr, data)
                scr.refresh()


def main():
    cfg_file = config_path()
    cfg = load_config(cfg_file)
    try:
        default_interval = float(cfg.get("interval", 1.0))
    except ValueError:
        default_interval = 1.0

    ap = argparse.ArgumentParser(
        prog="llama-lite-top",
        description="Lightweight real-time terminal monitor for llama.cpp's llama-server.",
        epilog=f"Config file: {cfg_file}",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--url", default=os.environ.get("LLAMA_URL") or cfg.get("url") or DEFAULT_URL,
                    help=f"llama-server address (default: $LLAMA_URL, config file, or {DEFAULT_URL})")
    ap.add_argument("--api-key", default=os.environ.get("LLAMA_API_KEY") or cfg.get("api_key"),
                    help="key set with --api-key on llama-server (default: $LLAMA_API_KEY or config file)")
    ap.add_argument("--interval", type=float, default=default_interval,
                    help="seconds between refreshes (default: 1.0)")
    args = ap.parse_args()
    if args.interval <= 0:
        ap.error("--interval must be greater than 0")
    try:
        curses.wrapper(App(args).run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
