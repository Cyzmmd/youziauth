# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import argparse
import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import campus_auth
from campus_auth_gui import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOG_LINES,
    GuiSettings,
    build_auth_config,
    load_gui_settings,
    resolve_log_path,
    save_gui_settings,
    tail_log,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def settings_from_payload(payload: dict[str, Any], log_file: str = "campus_auth.log") -> GuiSettings:
    return GuiSettings(
        username=str(payload.get("username", "")).strip(),
        password=str(payload.get("password", "")),
        check_interval_seconds=campus_auth.parse_positive_int(
            str(payload.get("check_interval_seconds", "60")),
            "check_interval_seconds",
        ),
        log_file=log_file,
    )


def render_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>校园网登录设置</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef2f4;
      --panel: #ffffff;
      --text: #172026;
      --muted: #667085;
      --line: #d9e2e8;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --warning: #b45309;
      --log-bg: #111827;
      --log-text: #e5e7eb;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei UI", "Segoe UI", Arial, sans-serif;
    }
    .app {
      width: min(1180px, 100%);
      min-height: 100vh;
      margin: 0 auto;
      padding: 28px;
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
      gap: 18px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
    }
    .settings {
      align-self: start;
      position: sticky;
      top: 28px;
    }
    h1, h2 {
      margin: 0;
      letter-spacing: 0;
    }
    h1 { font-size: 24px; line-height: 1.25; }
    h2 { font-size: 20px; line-height: 1.3; }
    .sub {
      margin: 8px 0 22px;
      color: var(--muted);
      line-height: 1.6;
      font-size: 14px;
    }
    label {
      display: block;
      margin: 14px 0 7px;
      font-size: 14px;
      font-weight: 600;
    }
    input {
      width: 100%;
      height: 40px;
      border: 1px solid #c8d2da;
      border-radius: 6px;
      padding: 0 11px;
      color: var(--text);
      background: #fbfcfd;
      font: inherit;
      outline: none;
    }
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14);
    }
    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 18px;
    }
    button {
      height: 40px;
      border: 1px solid #b9c6ce;
      border-radius: 6px;
      color: var(--text);
      background: #f8fafb;
      font: inherit;
      cursor: pointer;
    }
    button:hover { background: #edf3f2; }
    button.primary {
      border-color: var(--accent);
      color: #ffffff;
      background: var(--accent);
    }
    button.primary:hover { background: var(--accent-strong); }
    button.full { grid-column: 1 / -1; }
    button:disabled {
      cursor: default;
      opacity: 0.55;
    }
    .status {
      min-height: 42px;
      margin-top: 20px;
      padding: 10px 12px;
      border: 1px solid #f1d4a5;
      border-radius: 6px;
      color: var(--warning);
      background: #fff8eb;
      line-height: 1.45;
      font-size: 14px;
    }
    .logs-panel {
      min-width: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 12px;
    }
    .logs-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    pre {
      min-height: 420px;
      max-height: calc(100vh - 150px);
      margin: 0;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      border-radius: 8px;
      padding: 16px;
      background: var(--log-bg);
      color: var(--log-text);
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 13px;
      line-height: 1.5;
    }
    @media (max-width: 820px) {
      .app {
        grid-template-columns: 1fr;
        padding: 16px;
      }
      .settings {
        position: static;
      }
      pre {
        max-height: 60vh;
      }
    }
  </style>
</head>
<body>
  <main class="app">
    <section class="panel settings" aria-labelledby="settings-title">
      <h1 id="settings-title">校园网登录设置</h1>
      <p class="sub">填写账号密码，设置检测间隔，保存后可立即检测或后台保持在线。</p>

      <label for="username">账号</label>
      <input id="username" data-testid="username" autocomplete="username">

      <label for="password">密码</label>
      <input id="password" data-testid="password" type="password" autocomplete="current-password">

      <label for="interval">检测间隔（秒）</label>
      <input id="interval" data-testid="interval" type="number" min="5" step="5">

      <div class="actions">
        <button id="save" class="primary" type="button">保存</button>
        <button id="runOnce" type="button">检测一次</button>
        <button id="start" class="full" type="button">开始后台检测</button>
        <button id="stop" class="full" type="button">停止后台检测</button>
      </div>

      <div id="status" class="status" role="status">正在读取设置...</div>
    </section>

    <section class="panel logs-panel" aria-labelledby="logs-title">
      <div class="logs-header">
        <div>
          <h2 id="logs-title">日志</h2>
          <p class="sub">自动刷新最近的认证日志。</p>
        </div>
        <button id="refresh" type="button">刷新</button>
      </div>
      <pre id="logs" data-testid="logs">正在读取日志...</pre>
    </section>
  </main>

  <script>
    const username = document.querySelector("#username");
    const password = document.querySelector("#password");
    const interval = document.querySelector("#interval");
    const statusBox = document.querySelector("#status");
    const logs = document.querySelector("#logs");
    const startButton = document.querySelector("#start");
    const stopButton = document.querySelector("#stop");

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || "请求失败");
      }
      return data;
    }

    function formPayload() {
      return {
        username: username.value.trim(),
        password: password.value,
        check_interval_seconds: interval.value
      };
    }

    function setStatus(text) {
      statusBox.textContent = text || "就绪";
    }

    function setMonitoring(isMonitoring) {
      startButton.disabled = isMonitoring;
      stopButton.disabled = !isMonitoring;
    }

    async function loadSettings() {
      const data = await api("/api/settings");
      username.value = data.username || "";
      interval.value = data.check_interval_seconds || 60;
      setStatus(data.status);
      setMonitoring(Boolean(data.monitoring));
    }

    async function saveSettings(showStatus = true) {
      const data = await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(formPayload())
      });
      password.value = "";
      if (showStatus) setStatus(data.status);
      return data;
    }

    async function refreshLogs() {
      const data = await api("/api/logs");
      logs.textContent = data.text || "暂无日志";
    }

    async function postAction(path) {
      await saveSettings(false);
      const data = await api(path, { method: "POST", body: "{}" });
      setStatus(data.status);
      setMonitoring(Boolean(data.monitoring));
      await refreshLogs();
    }

    document.querySelector("#save").addEventListener("click", async () => {
      try { await saveSettings(true); await loadSettings(); }
      catch (error) { setStatus(error.message); }
    });
    document.querySelector("#runOnce").addEventListener("click", async () => {
      try { await postAction("/api/run-once"); }
      catch (error) { setStatus(error.message); }
    });
    startButton.addEventListener("click", async () => {
      try { await postAction("/api/start"); }
      catch (error) { setStatus(error.message); }
    });
    stopButton.addEventListener("click", async () => {
      try {
        const data = await api("/api/stop", { method: "POST", body: "{}" });
        setStatus(data.status);
        setMonitoring(Boolean(data.monitoring));
      } catch (error) { setStatus(error.message); }
    });
    document.querySelector("#refresh").addEventListener("click", async () => {
      try { await refreshLogs(); await loadSettings(); }
      catch (error) { setStatus(error.message); }
    });

    async function boot() {
      try {
        await loadSettings();
        await refreshLogs();
      } catch (error) {
        setStatus(error.message);
      }
      setInterval(async () => {
        try { await loadSettings(); await refreshLogs(); }
        catch (error) { setStatus(error.message); }
      }, 3000);
    }

    boot();
  </script>
</body>
</html>"""


@dataclasses.dataclass
class WebAppState:
    config_path: Path = DEFAULT_CONFIG_PATH
    status: str = "就绪"
    monitoring: bool = False
    worker: Optional[threading.Thread] = None
    stop_event: threading.Event = dataclasses.field(default_factory=threading.Event)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def settings_payload(self) -> dict[str, Any]:
        settings = load_gui_settings(self.config_path)
        with self.lock:
            status = self.status
            monitoring = self.monitoring
        return {
            "ok": True,
            "username": settings.username,
            "check_interval_seconds": settings.check_interval_seconds,
            "log_file": settings.log_file,
            "status": status,
            "monitoring": monitoring,
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = load_gui_settings(self.config_path)
        settings = settings_from_payload(payload, log_file=current.log_file)
        save_gui_settings(self.config_path, settings)
        with self.lock:
            self.status = "设置已保存"
        return self.settings_payload()

    def logs_payload(self) -> dict[str, Any]:
        settings = load_gui_settings(self.config_path)
        log_path = resolve_log_path(self.config_path, settings.log_file)
        return {"ok": True, "text": tail_log(log_path, DEFAULT_LOG_LINES)}

    def run_once_async(self) -> dict[str, Any]:
        config = build_auth_config(self.config_path)
        with self.lock:
            self.status = "正在检测..."
        thread = threading.Thread(target=self._run_once_worker, args=(config,), daemon=True)
        thread.start()
        return self.settings_payload()

    def start_monitor(self) -> dict[str, Any]:
        with self.lock:
            if self.monitoring:
                already_running = True
            else:
                already_running = False
        if already_running:
            return self.settings_payload()

        config = build_auth_config(self.config_path)
        with self.lock:
            self.status = "后台检测已启动"
            self.monitoring = True
            self.stop_event.clear()
        self.worker = threading.Thread(target=self._monitor_worker, args=(config,), daemon=True)
        self.worker.start()
        return self.settings_payload()

    def stop_monitor(self) -> dict[str, Any]:
        self.stop_event.set()
        with self.lock:
            self.monitoring = False
            self.status = "后台检测已停止"
        return self.settings_payload()

    def _run_once_worker(self, config: campus_auth.AuthConfig) -> None:
        ok = self._run_auth_once(config)
        with self.lock:
            self.status = "检测完成：已认证或登录成功" if ok else "检测完成：登录失败"

    def _monitor_worker(self, config: campus_auth.AuthConfig) -> None:
        while not self.stop_event.is_set():
            ok = self._run_auth_once(config)
            with self.lock:
                self.status = "后台检测：正常" if ok else "后台检测：登录失败"
            if self.stop_event.wait(config.check_interval_seconds):
                break
        with self.lock:
            self.monitoring = False
            if self.status not in ("后台检测已停止",):
                self.status = "后台检测已停止"

    def _run_auth_once(self, config: campus_auth.AuthConfig) -> bool:
        try:
            logger = campus_auth.configure_logging(config.log_file, verbose=True)
            client = campus_auth.CampusAuthClient(config, logger)
            return campus_auth.run_once(client, logger)
        except Exception as exc:  # noqa: BLE001 - local UI should surface worker errors.
            with self.lock:
                self.status = f"检测异常：{exc}"
            return False


class CampusAuthRequestHandler(BaseHTTPRequestHandler):
    state: WebAppState

    def do_GET(self) -> None:
        if self.path == "/":
            self._send_html(render_page())
            return
        if self.path == "/api/settings":
            self._send_json(self.state.settings_payload())
            return
        if self.path == "/api/logs":
            self._send_json(self.state.logs_payload())
            return
        self._send_json({"ok": False, "error": "Not found"}, status=404)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/settings":
                self._send_json(self.state.save_settings(self._read_json()))
                return
            if self.path == "/api/run-once":
                self._send_json(self.state.run_once_async())
                return
            if self.path == "/api/start":
                self._send_json(self.state.start_monitor())
                return
            if self.path == "/api/stop":
                self._send_json(self.state.stop_monitor())
                return
            self._send_json({"ok": False, "error": "Not found"}, status=404)
        except Exception as exc:  # noqa: BLE001 - return clear local API errors.
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def make_handler(state: WebAppState) -> type[CampusAuthRequestHandler]:
    class Handler(CampusAuthRequestHandler):
        pass

    Handler.state = state
    return Handler


def run_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    state = WebAppState(config_path=config_path)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    url = f"http://{host}:{server.server_port}/"
    print(f"Campus auth settings UI: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        state.stop_monitor()
    finally:
        server.server_close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the campus auth local settings UI.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    run_server(host=args.host, port=args.port, config_path=args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
