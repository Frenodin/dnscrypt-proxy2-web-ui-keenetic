#!/opt/bin/python3
"""
dnscrypt-proxy2-ui — минималистичная веб-панель для управления
dnscrypt-proxy2 на Keenetic/Entware. Только стандартная библиотека
Python (без Flask/pip), чтобы не тащить лишние зависимости на роутер.

Запуск: /opt/bin/python3 /opt/etc/dnscrypt-proxy2-ui/server.py
"""

import http.server
import socketserver
import subprocess
import json
import re
import os
import base64
import time
from urllib.parse import urlparse

# ===== Конфигурация панели =====

PANEL_PORT = 8091
PANEL_USER_DEFAULT = "admin"
PANEL_PASS_DEFAULT = "changeme"  # используется только при самом первом запуске

AUTH_FILE = "/opt/etc/dnscrypt-proxy2-ui/panel-auth.json"

CONFIG_PATH = "/opt/etc/dnscrypt-proxy.toml"
BACKUP_DIR = "/opt/etc/dnscrypt-proxy-backups"
INIT_SCRIPT = "/opt/etc/init.d/S09dnscrypt-proxy2"
LOG_FILE = "/opt/var/log/dnscrypt-proxy.log"
DNSCRYPT_BIN = "/opt/sbin/dnscrypt-proxy"

ODOH_SERVERS_MD = "/opt/etc/odoh-servers.md"
ODOH_RELAYS_MD = "/opt/etc/odoh-relays.md"

VALIDATE_TEST_PORT = 59999
VALIDATE_TIMEOUT = 4  # секунд на тестовый запуск перед проверкой на FATAL

# ===========================================================================


def load_auth():
    """Читает логин/пароль из panel-auth.json. При первом запуске
    создаёт файл с дефолтными значениями (0600), чтобы дальше пароль
    менялся только через файл, без правки самого скрипта."""
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r") as f:
                data = json.load(f)
            return {"user": data.get("user", PANEL_USER_DEFAULT),
                    "pass": data.get("pass", PANEL_PASS_DEFAULT)}
        except Exception:
            pass
    auth = {"user": PANEL_USER_DEFAULT, "pass": PANEL_PASS_DEFAULT}
    save_auth(auth)
    return auth


def save_auth(auth):
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    with open(AUTH_FILE, "w") as f:
        json.dump(auth, f)
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass


PANEL_AUTH = load_auth()


def get_status():
    status = {"running": False, "pid": None, "server_name": None,
              "relay_name": None, "rtt": None}
    try:
        out = subprocess.check_output(["ps"], stderr=subprocess.DEVNULL).decode(errors="ignore")
        for line in out.splitlines():
            if "dnscrypt-proxy -config" in line and "grep" not in line:
                parts = line.split()
                status["running"] = True
                status["pid"] = parts[0]
                break
    except Exception:
        pass

    try:
        with open(LOG_FILE, "r", errors="ignore") as f:
            lines = f.readlines()[-300:]
        for line in reversed(lines):
            if status["server_name"] is None and "Anonymizing queries for" in line:
                m = re.search(r"Anonymizing queries for \[([^\]]+)\] via \[([^\]]+)\]", line)
                if m:
                    status["server_name"], status["relay_name"] = m.group(1), m.group(2)
            if status["rtt"] is None:
                m2 = re.search(r"OK \(.*?\) - rtt: (\S+)", line)
                if m2:
                    status["rtt"] = m2.group(1)
            if status["server_name"] and status["rtt"]:
                break
    except FileNotFoundError:
        pass

    return status


def parse_md_names(path, prefix_filter=None):
    """Достаёт имена серверов/релеев из ## заголовков odoh-*.md файлов."""
    names = []
    try:
        with open(path, "r", errors="ignore") as f:
            content = f.read()
    except FileNotFoundError:
        return names
    for m in re.finditer(r"^## (\S+)\s*$", content, re.MULTILINE):
        name = m.group(1)
        if prefix_filter and not name.startswith(prefix_filter):
            continue
        names.append(name)
    return names


def validate_config_text(text):
    """Пробный запуск dnscrypt-proxy с этим конфигом на отдельном порту,
    чтобы не конфликтовать с уже работающим сервисом. Возвращает
    (ok: bool, output: str)."""
    test_path = CONFIG_PATH + ".validate-tmp"
    test_text = re.sub(
        r"listen_addresses\s*=\s*\[[^\]]*\]",
        f"listen_addresses = ['127.0.0.1:{VALIDATE_TEST_PORT}']",
        text,
        count=1,
    )
    with open(test_path, "w") as f:
        f.write(test_text)

    proc = subprocess.Popen(
        [DNSCRYPT_BIN, "-config", test_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    try:
        out, _ = proc.communicate(timeout=VALIDATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=2)
        except Exception:
            out = b""
            proc.kill()

    try:
        os.remove(test_path)
    except OSError:
        pass

    text_out = out.decode(errors="ignore")
    ok = "FATAL" not in text_out
    return ok, text_out


def backup_config():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"dnscrypt-proxy.toml.{ts}")
    try:
        with open(CONFIG_PATH, "r") as src, open(dest, "w") as dst:
            dst.write(src.read())
    except FileNotFoundError:
        pass
    # Держим только последние 20 бэкапов
    backups = sorted(os.listdir(BACKUP_DIR))
    for old in backups[:-20]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def do_service_action(action):
    if action not in ("start", "stop", "restart"):
        return False, "unknown action"
    try:
        proc = subprocess.run([INIT_SCRIPT, action], capture_output=True, timeout=20)
        return proc.returncode == 0, proc.stdout.decode(errors="ignore") + proc.stderr.decode(errors="ignore")
    except Exception as e:
        return False, str(e)


INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>dnscrypt-proxy2 панель</title>
<style>
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#14161a; color:#e6e6e6; margin:0; }
  header { background:#1c1f26; padding:14px 20px; border-bottom:1px solid #2a2e37; }
  h1 { font-size:18px; margin:0; }
  nav { display:flex; gap:4px; padding:0 20px; background:#1c1f26; }
  nav button { background:none; border:none; color:#9aa0aa; padding:12px 16px; cursor:pointer; font-size:14px; border-bottom:2px solid transparent; }
  nav button.active { color:#fff; border-bottom-color:#4f8cff; }
  main { padding:20px; max-width:900px; margin:0 auto; }
  .tab { display:none; }
  .tab.active { display:block; }
  .card { background:#1c1f26; border:1px solid #2a2e37; border-radius:8px; padding:16px; margin-bottom:16px; }
  .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
  .badge { display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:600; }
  .badge.on { background:#1f4d2e; color:#7cf29c; }
  .badge.off { background:#4d1f1f; color:#f27c7c; }
  button.action { background:#2a2e37; color:#fff; border:1px solid #3a3f4b; border-radius:6px; padding:8px 16px; cursor:pointer; font-size:13px; }
  button.action:hover { background:#343a46; }
  button.primary { background:#2857c4; border-color:#2857c4; }
  button.primary:hover { background:#3567df; }
  textarea { width:100%; height:420px; background:#0f1114; color:#d8ffb0; border:1px solid #2a2e37; border-radius:6px; padding:10px; font-family:monospace; font-size:13px; box-sizing:border-box; }
  pre#logbox { background:#0f1114; border:1px solid #2a2e37; border-radius:6px; padding:10px; height:420px; overflow-y:auto; font-size:12px; white-space:pre-wrap; }
  select { background:#0f1114; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px; padding:6px; }
  select[multiple] { height:140px; width:100%; }
  label { font-size:13px; color:#9aa0aa; display:block; margin-bottom:4px; }
  .msg { padding:10px; border-radius:6px; margin-top:10px; font-size:13px; white-space:pre-wrap; }
  .msg.ok { background:#1f4d2e; color:#c7ffd7; }
  .msg.err { background:#4d1f1f; color:#ffd0d0; }
  .kv { color:#9aa0aa; font-size:13px; }
  .kv b { color:#e6e6e6; }
</style>
</head>
<body>
<header><h1>dnscrypt-proxy2 — панель управления</h1></header>
<nav>
  <button class="tabbtn active" data-tab="dash">Дашборд</button>
  <button class="tabbtn" data-tab="route">Relay / Target</button>
  <button class="tabbtn" data-tab="config">Редактор toml</button>
  <button class="tabbtn" data-tab="logs">Логи</button>
  <button class="tabbtn" data-tab="settings">Настройки</button>
</nav>
<main>

  <div class="tab active" id="tab-dash">
    <div class="card">
      <div class="row">
        <span>Статус: <span id="st-badge" class="badge off">...</span></span>
        <span class="kv">PID: <b id="st-pid">-</b></span>
      </div>
      <div class="row kv">
        Target: <b id="st-server">-</b> &nbsp;|&nbsp; Relay: <b id="st-relay">-</b> &nbsp;|&nbsp; RTT: <b id="st-rtt">-</b>
      </div>
      <div class="row">
        <button class="action primary" onclick="doAction('start')">Start</button>
        <button class="action" onclick="doAction('stop')">Stop</button>
        <button class="action" onclick="doAction('restart')">Restart</button>
      </div>
      <div id="action-msg"></div>
    </div>
  </div>

  <div class="tab" id="tab-route">
    <div class="card">
      <label>Target ODoH-сервер</label>
      <select id="sel-server"></select>
      <br><br>
      <label>Relay(и) — можно выбрать несколько (Ctrl+клик)</label>
      <select id="sel-relays" multiple></select>
      <div class="row" style="margin-top:12px;">
        <button class="action primary" onclick="applyRoute()">Применить и перезапустить</button>
      </div>
      <div id="route-msg"></div>
    </div>
  </div>

  <div class="tab" id="tab-config">
    <div class="card">
      <textarea id="config-text"></textarea>
      <div class="row" style="margin-top:10px;">
        <button class="action" onclick="loadConfig()">Обновить из файла</button>
        <button class="action primary" onclick="saveConfig()">Проверить и сохранить (+рестарт)</button>
      </div>
      <div id="config-msg"></div>
    </div>
  </div>

  <div class="tab" id="tab-logs">
    <div class="card">
      <pre id="logbox">загрузка...</pre>
    </div>
  </div>

  <div class="tab" id="tab-settings">
    <div class="card">
      <label>Текущий пароль</label>
      <input type="password" id="old-pwd" style="width:100%; padding:8px; margin-bottom:10px; background:#0f1114; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px;">
      <label>Новый пароль</label>
      <input type="password" id="new-pwd" style="width:100%; padding:8px; margin-bottom:10px; background:#0f1114; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px;">
      <label>Повторите новый пароль</label>
      <input type="password" id="new-pwd2" style="width:100%; padding:8px; margin-bottom:10px; background:#0f1114; color:#e6e6e6; border:1px solid #2a2e37; border-radius:6px;">
      <div class="row">
        <button class="action primary" onclick="changePassword()">Сменить пароль</button>
      </div>
      <div id="settings-msg"></div>
    </div>
  </div>

</main>

<script>
let logsTimer = null, statusTimer = null;

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tabbtn').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector(`.tabbtn[data-tab="${name}"]`).classList.add('active');
  if (name === 'logs') { loadLogs(); logsTimer = setInterval(loadLogs, 3000); }
  else { clearInterval(logsTimer); }
  if (name === 'config') loadConfig();
  if (name === 'route') loadRouteOptions();
}
document.querySelectorAll('.tabbtn').forEach(b => b.onclick = () => switchTab(b.dataset.tab));

async function loadStatus() {
  const r = await fetch('/api/status');
  const s = await r.json();
  const badge = document.getElementById('st-badge');
  badge.textContent = s.running ? 'работает' : 'остановлен';
  badge.className = 'badge ' + (s.running ? 'on' : 'off');
  document.getElementById('st-pid').textContent = s.pid || '-';
  document.getElementById('st-server').textContent = s.server_name || '-';
  document.getElementById('st-relay').textContent = s.relay_name || '-';
  document.getElementById('st-rtt').textContent = s.rtt || '-';
}
statusTimer = setInterval(loadStatus, 5000);
loadStatus();

async function doAction(action) {
  const msg = document.getElementById('action-msg');
  msg.textContent = 'выполняю...';
  msg.className = 'msg';
  const r = await fetch('/api/action', {method:'POST', body: JSON.stringify({action})});
  const j = await r.json();
  msg.textContent = j.output || (j.ok ? 'OK' : 'ошибка');
  msg.className = 'msg ' + (j.ok ? 'ok' : 'err');
  loadStatus();
}

async function loadConfig() {
  const r = await fetch('/api/config');
  document.getElementById('config-text').value = await r.text();
  document.getElementById('config-msg').textContent = '';
}

async function saveConfig() {
  const msg = document.getElementById('config-msg');
  msg.textContent = 'проверяю конфиг...';
  msg.className = 'msg';
  const text = document.getElementById('config-text').value;
  const r = await fetch('/api/config?restart=1', {method:'POST', body: text});
  const j = await r.json();
  msg.textContent = j.output || (j.ok ? 'Сохранено и перезапущено' : 'Ошибка, конфиг НЕ применён');
  msg.className = 'msg ' + (j.ok ? 'ok' : 'err');
  if (j.ok) loadStatus();
}

async function loadRouteOptions() {
  const [servers, relays] = await Promise.all([
    fetch('/api/servers').then(r => r.json()),
    fetch('/api/relays').then(r => r.json())
  ]);
  const selServer = document.getElementById('sel-server');
  selServer.innerHTML = servers.map(s => `<option value="${s}">${s}</option>`).join('');
  const selRelays = document.getElementById('sel-relays');
  selRelays.innerHTML = relays.map(r => `<option value="${r}">${r}</option>`).join('');
}

async function applyRoute() {
  const msg = document.getElementById('route-msg');
  msg.textContent = 'применяю...';
  msg.className = 'msg';
  const server_name = document.getElementById('sel-server').value;
  const relays = Array.from(document.getElementById('sel-relays').selectedOptions).map(o => o.value);
  if (!server_name || relays.length === 0) {
    msg.textContent = 'Выберите target и хотя бы один relay';
    msg.className = 'msg err';
    return;
  }
  const r = await fetch('/api/apply-route', {method:'POST', body: JSON.stringify({server_name, relays})});
  const j = await r.json();
  msg.textContent = j.output || (j.ok ? 'Применено и перезапущено' : 'Ошибка, изменения НЕ применены');
  msg.className = 'msg ' + (j.ok ? 'ok' : 'err');
  if (j.ok) loadStatus();
}

async function changePassword() {
  const msg = document.getElementById('settings-msg');
  const oldPwd = document.getElementById('old-pwd').value;
  const newPwd = document.getElementById('new-pwd').value;
  const newPwd2 = document.getElementById('new-pwd2').value;
  if (newPwd !== newPwd2) {
    msg.textContent = 'Новые пароли не совпадают';
    msg.className = 'msg err';
    return;
  }
  msg.textContent = 'меняю...';
  msg.className = 'msg';
  const r = await fetch('/api/change-password', {method:'POST', body: JSON.stringify({old_password: oldPwd, new_password: newPwd})});
  const j = await r.json();
  msg.textContent = j.output;
  msg.className = 'msg ' + (j.ok ? 'ok' : 'err');
  if (j.ok) {
    document.getElementById('old-pwd').value = '';
    document.getElementById('new-pwd').value = '';
    document.getElementById('new-pwd2').value = '';
  }
}

async function loadLogs() {
  const r = await fetch('/api/logs');
  const box = document.getElementById('logbox');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
  box.textContent = await r.text();
  if (atBottom) box.scrollTop = box.scrollHeight;
}
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "dnscrypt-panel/1.0"

    def log_message(self, fmt, *args):
        pass  # тише в консоли; при желании можно включить обратно

    def check_auth(self):
        if not PANEL_AUTH.get("user"):
            return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, _, pwd = decoded.partition(":")
            return user == PANEL_AUTH["user"] and pwd == PANEL_AUTH["pass"]
        except Exception:
            return False

    def require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="dnscrypt-proxy2 panel"')
        self.end_headers()

    def _send(self, code, body, content_type="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        if not self.check_auth():
            return self.require_auth()
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/api/status":
            self._send_json(get_status())
        elif path == "/api/config":
            try:
                with open(CONFIG_PATH, "r") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, "config not found")
        elif path == "/api/logs":
            try:
                with open(LOG_FILE, "r", errors="ignore") as f:
                    lines = f.readlines()[-400:]
                self._send(200, "".join(lines))
            except FileNotFoundError:
                self._send(200, "(лог ещё не создан)")
        elif path == "/api/servers":
            self._send_json(parse_md_names("/opt/etc/odoh-servers.md"))
        elif path == "/api/relays":
            self._send_json(parse_md_names("/opt/etc/odoh-relays.md"))
        else:
            self._send(404, "not found")

    def do_POST(self):
        if not self.check_auth():
            return self.require_auth()
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if parsed.path == "/api/config":
            text = body.decode("utf-8", errors="ignore")
            ok, out = validate_config_text(text)
            if not ok:
                return self._send_json({"ok": False, "output": out[-3000:]})
            backup_config()
            with open(CONFIG_PATH, "w") as f:
                f.write(text)
            restart_msg = ""
            if "restart=1" in parsed.query:
                rok, rout = do_service_action("restart")
                if not rok:
                    return self._send_json({"ok": False, "output": "Сохранено, но рестарт не удался:\n" + rout})
                restart_msg = "Сервис перезапущен.\n"
            self._send_json({"ok": True, "output": restart_msg + "Конфиг проверен и сохранён."})

        elif parsed.path == "/api/action":
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                return self._send_json({"ok": False, "output": "bad request"})
            ok, out = do_service_action(data.get("action", ""))
            self._send_json({"ok": ok, "output": out})

        elif parsed.path == "/api/apply-route":
            try:
                data = json.loads(body.decode("utf-8"))
                server_name = data["server_name"]
                relays = data["relays"]
            except Exception:
                return self._send_json({"ok": False, "output": "bad request"})

            with open(CONFIG_PATH, "r") as f:
                content = f.read()

            content = re.sub(
                r"server_names\s*=\s*\[[^\]]*\]",
                f"server_names = ['{server_name}']",
                content, count=1,
            )
            relays_str = ", ".join(f"'{r}'" for r in relays)
            new_route_block = (
                "routes = [\n"
                f"    {{ server_name='{server_name}', via=[{relays_str}] }}\n"
                "]"
            )
            if re.search(r"routes\s*=\s*\[.*?\]", content, re.DOTALL):
                content = re.sub(
                    r"routes\s*=\s*\[.*?\]",
                    new_route_block.replace("\\", "\\\\"),
                    content, count=1, flags=re.DOTALL,
                )
            else:
                content += (
                    "\n[anonymized_dns]\n" + new_route_block + "\nskip_incompatible = true\n"
                )

            ok, out = validate_config_text(content)
            if not ok:
                return self._send_json({"ok": False, "output": out[-3000:]})
            backup_config()
            with open(CONFIG_PATH, "w") as f:
                f.write(content)
            rok, rout = do_service_action("restart")
            if not rok:
                return self._send_json({"ok": False, "output": "Сохранено, но рестарт не удался:\n" + rout})
            self._send_json({"ok": True, "output": "Маршрут применён, сервис перезапущен."})

        elif parsed.path == "/api/change-password":
            try:
                data = json.loads(body.decode("utf-8"))
                old_pwd = data["old_password"]
                new_pwd = data["new_password"]
            except Exception:
                return self._send_json({"ok": False, "output": "bad request"})

            if old_pwd != PANEL_AUTH["pass"]:
                return self._send_json({"ok": False, "output": "Текущий пароль неверен."})
            if not new_pwd or len(new_pwd) < 4:
                return self._send_json({"ok": False, "output": "Новый пароль слишком короткий."})

            PANEL_AUTH["pass"] = new_pwd
            save_auth(PANEL_AUTH)
            self._send_json({"ok": True, "output": "Пароль изменён. Браузер запросит новые логин/пароль при следующем запросе."})

        else:
            self._send(404, "not found")


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    with ReusableTCPServer(("0.0.0.0", PANEL_PORT), Handler) as httpd:
        print(f"dnscrypt-proxy2 panel listening on :{PANEL_PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
