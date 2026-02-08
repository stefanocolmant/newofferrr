#!/usr/bin/env python3
"""
Local dev server for this static site.

Features:
- Serves the folder as a website.
- Injects a tiny script into HTML that auto-reloads the page when files change.
- Optional element inspector: add ?inspect=1 to the URL, then click anything to copy a CSS selector.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Tuple


INJECT_MARKER = b"__dev_server_injected__"

# Keep this snippet self-contained: no external JS, no imports.
INJECT_SNIPPET = b"""
<script id="__dev_server_injected__">
(() => {
  // Auto-reload when a file changes on disk (SSE).
  const url = new URL(location.href);
  if (url.searchParams.get("noreload") !== "1") {
    const connect = () => {
      const es = new EventSource("/__livereload");
      es.addEventListener("reload", () => location.reload());
      es.onerror = () => {
        try { es.close(); } catch {}
        setTimeout(connect, 500);
      };
    };
    connect();
  }

  // Inspect mode: click any element to copy a selector (useful for asking Codex to edit specific parts).
  if (url.searchParams.get("inspect") === "1") {
    const cssEscape = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, (m) => "\\\\%s".replace("%s", m));
    const getSelector = (el) => {
      if (!el || el.nodeType !== 1) return "";
      if (el.id) return "#" + cssEscape(el.id);
      const parts = [];
      let cur = el;
      while (cur && cur.nodeType === 1 && cur !== document.body) {
        let part = cur.tagName.toLowerCase();
        const classes = cur.classList ? Array.from(cur.classList).filter(Boolean) : [];
        if (classes.length) part += "." + classes.slice(0, 2).map(cssEscape).join(".");
        const parent = cur.parentElement;
        if (parent) {
          const sameTag = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
          if (sameTag.length > 1) part += `:nth-of-type(${sameTag.indexOf(cur) + 1})`;
        }
        parts.unshift(part);
        cur = cur.parentElement;
      }
      return "body > " + parts.join(" > ");
    };

    const style = document.createElement("style");
    style.textContent = `
      #__dev_inspect_tip { position: fixed; left: 12px; bottom: 12px; z-index: 2147483647;
        font: 12px/1.2 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        color: #0b0b0b; background: rgba(180, 249, 192, 0.92); border: 1px solid rgba(0,0,0,.25);
        padding: 10px 12px; border-radius: 10px; max-width: 70vw; box-shadow: 0 8px 30px rgba(0,0,0,.35);
      }
      #__dev_inspect_tip code { user-select: all; }
      #__dev_inspect_hl { position: fixed; z-index: 2147483646; pointer-events: none;
        outline: 2px solid rgba(180, 249, 192, 0.95); box-shadow: 0 0 0 1px rgba(0,0,0,.35) inset;
      }
    `;
    document.head.appendChild(style);

    const tip = document.createElement("div");
    tip.id = "__dev_inspect_tip";
    tip.innerHTML = `<div style="margin-bottom:6px;"><b>Inspect mode</b>: click anything to copy its selector</div><code>(none)</code>`;
    document.body.appendChild(tip);

    const hl = document.createElement("div");
    hl.id = "__dev_inspect_hl";
    document.body.appendChild(hl);

    const updateHL = (el) => {
      if (!el || el === document.documentElement || el === document.body) {
        hl.style.display = "none";
        return;
      }
      const r = el.getBoundingClientRect();
      hl.style.display = "block";
      hl.style.left = r.left + "px";
      hl.style.top = r.top + "px";
      hl.style.width = r.width + "px";
      hl.style.height = r.height + "px";
    };

    let lastEl = null;
    window.addEventListener("mousemove", (e) => {
      const el = document.elementFromPoint(e.clientX, e.clientY);
      if (el && el !== lastEl) {
        lastEl = el;
        updateHL(el);
      }
    }, true);

    window.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const sel = getSelector(el);
      tip.querySelector("code").textContent = sel || "(none)";
      try { await navigator.clipboard.writeText(sel); } catch {}
    }, true);
  }
})();
</script>
"""


class LiveReloadState:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.change_id = 0

    def bump(self) -> int:
        with self.cond:
            self.change_id += 1
            self.cond.notify_all()
            return self.change_id


def _snapshot_tree(root: Path) -> Dict[str, Tuple[int, int]]:
    snap: Dict[str, Tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune dirs we never want to watch.
        dirnames[:] = [d for d in dirnames if d not in {".git", "refs"}]
        for name in filenames:
            if name in {".DS_Store"}:
                continue
            if name == "recording.mov":
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            snap[str(p)] = (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))), int(st.st_size))
    return snap


def _watch_tree(root: Path, state: LiveReloadState, interval_s: float) -> None:
    prev = _snapshot_tree(root)
    while True:
        time.sleep(interval_s)
        cur = _snapshot_tree(root)
        if cur != prev:
            prev = cur
            state.bump()


class DevHandler(SimpleHTTPRequestHandler):
    server: "DevServer"  # type: ignore[assignment]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/__livereload":
            self._handle_livereload()
            return

        local_path = Path(self.translate_path(parsed.path))
        if local_path.is_dir():
            # Redirect /faq -> /faq/ so relative assets resolve correctly.
            if not parsed.path.endswith("/"):
                self.send_response(301)
                self.send_header("Location", parsed.path + "/")
                self.end_headers()
                return
            # Serve index.html with injection if present.
            for index in ("index.html", "index.htm"):
                candidate = local_path / index
                if candidate.is_file():
                    self._serve_html(candidate, head_only=False)
                    return
            # Fallback to directory listing behavior.
            super().do_GET()
            return

        if local_path.suffix.lower() in {".html", ".htm"} and local_path.is_file():
            self._serve_html(local_path, head_only=False)
            return

        super().do_GET()

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/__livereload":
            self.send_error(405, "Method Not Allowed")
            return

        local_path = Path(self.translate_path(parsed.path))
        if local_path.is_dir():
            if not parsed.path.endswith("/"):
                self.send_response(301)
                self.send_header("Location", parsed.path + "/")
                self.end_headers()
                return
            for index in ("index.html", "index.htm"):
                candidate = local_path / index
                if candidate.is_file():
                    self._serve_html(candidate, head_only=True)
                    return
            super().do_HEAD()
            return

        if local_path.suffix.lower() in {".html", ".htm"} and local_path.is_file():
            self._serve_html(local_path, head_only=True)
            return

        super().do_HEAD()

    def _serve_html(self, file_path: Path, head_only: bool) -> None:
        try:
            data = file_path.read_bytes()
        except OSError:
            self.send_error(404, "File not found")
            return

        if INJECT_MARKER not in data:
            lower = data.lower()
            idx = lower.rfind(b"</body>")
            if idx != -1:
                data = data[:idx] + INJECT_SNIPPET + data[idx:]
            else:
                data = data + INJECT_SNIPPET

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _handle_livereload(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # A comment line helps some proxies keep the stream open.
        try:
            self.wfile.write(b": connected\\n\\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

        last_id = self.server.state.change_id
        while True:
            # Wait for a change or send periodic ping to keep the connection alive.
            with self.server.state.cond:
                changed = self.server.state.cond.wait_for(
                    lambda: self.server.state.change_id != last_id,
                    timeout=15.0,
                )
                if changed:
                    last_id = self.server.state.change_id
                    payload = f"event: reload\\ndata: {last_id}\\n\\n".encode("utf-8")
                else:
                    payload = b"event: ping\\ndata: 0\\n\\n"
            try:
                self.wfile.write(payload)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break


class DevServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, root: Path):
        super().__init__(server_address, RequestHandlerClass)
        self.state = LiveReloadState()
        self.root = root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--interval", type=float, default=0.4, help="Watch interval in seconds")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"Root folder does not exist: {root}")

    # Ensure handler serves from the chosen root.
    handler_cls = DevHandler
    handler_cls.directory = str(root)

    httpd = DevServer((args.bind, args.port), handler_cls, root=root)

    watcher = threading.Thread(target=_watch_tree, args=(root, httpd.state, float(args.interval)), daemon=True)
    watcher.start()

    print(f"Dev server running: http://{args.bind}:{args.port}/")
    print("Live reload: on (auto refresh on file changes)")
    print("Inspect mode: add ?inspect=1 then click elements to copy a selector")
    print("Stop server: Ctrl+C")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

