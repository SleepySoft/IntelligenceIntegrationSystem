#!/usr/bin/env python3
"""Loopback-only controller for sleepysoft.org service/announcement routing."""

import argparse
import datetime as dt
import hashlib
import hmac
import html
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


CONFIG_PATH = Path(os.environ.get("SITE_CONTROLLER_CONFIG", "/etc/site-controller/config.json"))
STATE_PATH = Path("/var/lib/site-controller/state.json")
AUDIT_PATH = Path("/var/log/site-controller/audit.log")
UPSTREAM_PATH = Path("/etc/nginx/conf.d/home_upstream.conf")
ACTIVE_PATH = Path("/etc/nginx/site-modes/active.conf")
MODE_PATHS = {
    "service": Path("/etc/nginx/site-modes/service.conf"),
    "announcement": Path("/etc/nginx/site-modes/announcement.conf"),
}
ANNOUNCEMENT_PATH = Path("/var/www/site-announcement/__site_announcement.html")
CERT_PATH = Path("/etc/letsencrypt/live/sleepysoft.org/fullchain.pem")
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PBKDF2_ROUNDS = 310_000


def atomic_write(path: Path, content: str, mode=0o640):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def init_config(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SystemExit(f"Refusing to overwrite existing config: {path}")
    password = secrets.token_urlsafe(18)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    config = {
        "username": "admin",
        "password_salt": salt.hex(),
        "password_hash": digest.hex(),
        "session_secret": secrets.token_hex(32),
    }
    atomic_write(path, json.dumps(config, indent=2) + "\n", 0o640)
    print(f"SITE_CONTROLLER_USERNAME=admin")
    print(f"SITE_CONTROLLER_PASSWORD={password}")


class Controller:
    def __init__(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.lock = threading.RLock()
        self.sessions = {}
        self.login_attempts = {}
        self.state = {
            "policy": "service",
            "effective": self.current_effective(),
            "health": "unknown",
            "consecutive_success": 0,
            "consecutive_failure": 0,
            "last_check": None,
            "last_success": None,
            "last_error": None,
            "latency_ms": None,
            "upstream": None,
            "ddns_last_report": None,
            "ddns_fresh": False,
            "last_switch": None,
            "last_switch_reason": None,
            "cooldown_until": 0,
        }
        if STATE_PATH.exists():
            try:
                saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                for key in ("policy", "last_switch", "last_switch_reason", "cooldown_until"):
                    if key in saved:
                        self.state[key] = saved[key]
            except Exception:
                pass
        self.stop_event = threading.Event()

    @staticmethod
    def now():
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def verify_password(self, password):
        salt = bytes.fromhex(self.config["password_salt"])
        expected = bytes.fromhex(self.config["password_hash"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
        return hmac.compare_digest(actual, expected)

    def new_session(self):
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        self.sessions[sid] = {"csrf": csrf, "expires": time.time() + 8 * 3600}
        return sid, csrf

    def get_session(self, raw_cookie):
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw_cookie or "")
            sid = jar["site_session"].value
        except (KeyError, cookies.CookieError):
            return None, None
        session = self.sessions.get(sid)
        if not session or session["expires"] < time.time():
            self.sessions.pop(sid, None)
            return None, None
        session["expires"] = time.time() + 8 * 3600
        return sid, session

    def current_effective(self):
        try:
            target = ACTIVE_PATH.resolve(strict=True)
            for name, path in MODE_PATHS.items():
                if target == path.resolve(strict=True):
                    return name
        except Exception:
            pass
        return "unknown"

    def upstream(self):
        text = UPSTREAM_PATH.read_text(encoding="utf-8")
        match = re.search(r"server\s+([0-9.]+):(\d+)\s*;", text)
        if not match:
            raise RuntimeError("home_backend not found")
        return match.group(1), int(match.group(2))

    def ddns_report(self):
        try:
            output = subprocess.check_output(
                ["journalctl", "-u", "home-ddns.service", "-n", "1", "-o", "json", "--no-pager"],
                text=True, timeout=3,
            ).strip()
            record = json.loads(output)
            timestamp = int(record["__REALTIME_TIMESTAMP"]) / 1_000_000
            return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(timespec="seconds"), time.time() - timestamp < 180
        except Exception:
            return None, False

    def health_check(self):
        started = time.monotonic()
        ip, port = self.upstream()
        connection = http.client.HTTPConnection(ip, port, timeout=5)
        try:
            connection.request("GET", "/", headers={"Host": "sleepysoft.org", "User-Agent": "site-controller-health/1.0"})
            response = connection.getresponse()
            response.read(2048)
            latency = round((time.monotonic() - started) * 1000)
            if not 200 <= response.status < 400:
                raise RuntimeError(f"HTTP {response.status}")
            return True, latency, None, f"{ip}:{port}"
        except Exception as exc:
            latency = round((time.monotonic() - started) * 1000)
            return False, latency, str(exc), f"{ip}:{port}"
        finally:
            connection.close()

    def persist(self):
        saved = {key: self.state[key] for key in ("policy", "last_switch", "last_switch_reason", "cooldown_until")}
        atomic_write(STATE_PATH, json.dumps(saved, indent=2) + "\n")

    def audit(self, event, detail):
        line = json.dumps({"time": self.now(), "event": event, "detail": detail}, ensure_ascii=False)
        with AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def apply_effective(self, target, reason):
        if target not in MODE_PATHS:
            raise ValueError("invalid effective mode")
        old_target = None
        try:
            old_target = ACTIVE_PATH.resolve(strict=True)
        except Exception:
            pass
        temp_link = ACTIVE_PATH.with_name("active.conf.next")
        try:
            temp_link.unlink(missing_ok=True)
            temp_link.symlink_to(MODE_PATHS[target])
            os.replace(temp_link, ACTIVE_PATH)
            subprocess.run(["sudo", "-n", "/usr/sbin/nginx", "-t"], check=True, timeout=10, capture_output=True, text=True)
            subprocess.run(["sudo", "-n", "/bin/systemctl", "reload", "nginx"], check=True, timeout=10, capture_output=True, text=True)
        except Exception:
            temp_link.unlink(missing_ok=True)
            if old_target:
                rollback = ACTIVE_PATH.with_name("active.conf.rollback")
                rollback.unlink(missing_ok=True)
                rollback.symlink_to(old_target)
                os.replace(rollback, ACTIVE_PATH)
                subprocess.run(["sudo", "-n", "/usr/sbin/nginx", "-t"], timeout=10, capture_output=True)
                subprocess.run(["sudo", "-n", "/bin/systemctl", "reload", "nginx"], timeout=10, capture_output=True)
            raise
        with self.lock:
            self.state["effective"] = target
            self.state["last_switch"] = self.now()
            self.state["last_switch_reason"] = reason
            self.state["cooldown_until"] = time.time() + 300
            self.persist()
        self.audit("switch", {"effective": target, "reason": reason})

    def set_policy(self, policy, actor="admin"):
        if policy not in ("service", "announcement", "auto"):
            raise ValueError("invalid policy")
        with self.lock:
            self.state["policy"] = policy
            health = self.state["health"]
            self.persist()
        target = policy if policy != "auto" else ("service" if health == "up" else "announcement")
        self.apply_effective(target, f"{actor}: policy={policy}")

    def write_announcement(self, title, message, eta):
        title = title.strip()[:100]
        message = message.strip()[:2000]
        eta = eta.strip()[:100]
        if not title or not message:
            raise ValueError("标题和正文不能为空")
        updated = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in message.splitlines() if line.strip())
        eta_html = f'<div class="eta"><span>预计恢复</span><strong>{html.escape(eta)}</strong></div>' if eta else ""
        page = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)}</title><style>\n+*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#07110f;color:#eef7f2;font-family:Inter,system-ui,sans-serif;padding:24px}}main{{width:min(680px,100%);background:#0d1c18;border:1px solid #24473d;border-radius:24px;padding:clamp(28px,6vw,56px);box-shadow:0 30px 80px #0008}}.mark{{width:52px;height:52px;border-radius:16px;background:#d4ff52;color:#102015;display:grid;place-items:center;font-size:26px;font-weight:900}}h1{{font-size:clamp(30px,6vw,54px);line-height:1.04;margin:28px 0 18px}}p{{color:#b8ccc4;font-size:17px;line-height:1.7}}.eta{{display:flex;justify-content:space-between;gap:24px;border-top:1px solid #24473d;border-bottom:1px solid #24473d;padding:18px 0;margin:28px 0}}.eta span,footer{{color:#78968a}}footer{{font-size:13px}}</style></head><body><main><div class=\"mark\">!</div><h1>{html.escape(title)}</h1>{paragraphs}{eta_html}<footer>最近更新：{html.escape(updated)} · sleepysoft.org</footer></main></body></html>"""
        atomic_write(ANNOUNCEMENT_PATH, page, 0o644)
        self.audit("announcement_updated", {"title": title, "eta": eta})

    def certificate_expiry(self):
        try:
            context = ssl.create_default_context()
            with socket.create_connection(("127.0.0.1", 443), timeout=3) as raw:
                with context.wrap_socket(raw, server_hostname="sleepysoft.org") as wrapped:
                    certificate = wrapped.getpeercert()
            expires = dt.datetime.strptime(certificate["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
            return expires.isoformat(), (expires - dt.datetime.now(dt.timezone.utc)).days
        except Exception:
            return None, None

    def service_states(self):
        states = {}
        for service in ("nginx", "home-ddns", "xray"):
            result = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=3)
            states[service] = result.stdout.strip() or "unknown"
        return states

    def public_status(self):
        with self.lock:
            data = dict(self.state)
        data["effective"] = self.current_effective()
        data["services"] = self.service_states()
        data["certificate_expiry"], data["certificate_days"] = self.certificate_expiry()
        return data

    def monitor_loop(self):
        while not self.stop_event.is_set():
            try:
                ok, latency, error, upstream = self.health_check()
                report, fresh = self.ddns_report()
                should_switch = None
                with self.lock:
                    self.state.update({"last_check": self.now(), "latency_ms": latency, "upstream": upstream, "ddns_last_report": report, "ddns_fresh": fresh})
                    if ok:
                        self.state["consecutive_success"] += 1
                        self.state["consecutive_failure"] = 0
                        self.state["last_success"] = self.now()
                        self.state["last_error"] = None
                        if self.state["consecutive_success"] >= 3:
                            self.state["health"] = "up"
                    else:
                        self.state["consecutive_failure"] += 1
                        self.state["consecutive_success"] = 0
                        self.state["last_error"] = error
                        if self.state["consecutive_failure"] >= 3:
                            self.state["health"] = "down"
                    if self.state["policy"] == "auto" and time.time() >= self.state["cooldown_until"]:
                        desired = "service" if self.state["health"] == "up" else "announcement"
                        if self.current_effective() != desired:
                            should_switch = desired
                if should_switch:
                    self.apply_effective(should_switch, f"auto: health={self.state['health']}")
            except Exception as exc:
                with self.lock:
                    self.state["last_error"] = f"monitor: {exc}"
            self.stop_event.wait(15)


CONTROLLER = None


class Handler(BaseHTTPRequestHandler):
    server_version = "site-controller"

    def security_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def send_bytes(self, code, data, content_type="text/plain; charset=utf-8", extra_headers=None):
        self.send_response(code)
        self.security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, payload, extra_headers=None):
        self.send_bytes(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8", extra_headers)

    def session(self):
        return CONTROLLER.get_session(self.headers.get("Cookie"))

    def require_auth(self):
        sid, session = self.session()
        if not session:
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return None
        return sid, session

    def body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16_384:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def valid_csrf(self, session):
        return hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"])

    def do_GET(self):
        path = urlparse(self.path).path
        sid, session = self.session()
        if path == "/login":
            data = (TEMPLATE_DIR / "login.html").read_bytes()
            self.send_bytes(200, data, "text/html; charset=utf-8")
        elif path == "/" and session:
            template = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
            data = template.replace("{{CSRF}}", html.escape(session["csrf"])).encode()
            self.send_bytes(200, data, "text/html; charset=utf-8")
        elif path == "/":
            self.send_response(303)
            self.send_header("Location", "/login")
            self.end_headers()
        elif path == "/static/style.css":
            self.send_bytes(200, (STATIC_DIR / "style.css").read_bytes(), "text/css; charset=utf-8")
        elif path == "/static/app.js":
            if not session:
                self.send_json(401, {"ok": False})
            else:
                self.send_bytes(200, (STATIC_DIR / "app.js").read_bytes(), "application/javascript; charset=utf-8")
        elif path == "/static/login.js":
            self.send_bytes(200, (STATIC_DIR / "login.js").read_bytes(), "application/javascript; charset=utf-8")
        elif path == "/api/status":
            if not self.require_auth():
                return
            self.send_json(200, {"ok": True, "status": CONTROLLER.public_status()})
        else:
            self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            try:
                body = self.body_json()
                key = self.client_address[0]
                attempts = [t for t in CONTROLLER.login_attempts.get(key, []) if time.time() - t < 300]
                if len(attempts) >= 5:
                    self.send_json(429, {"ok": False, "error": "尝试次数过多，请稍后再试"})
                    return
                if body.get("username") != CONTROLLER.config["username"] or not CONTROLLER.verify_password(body.get("password", "")):
                    attempts.append(time.time())
                    CONTROLLER.login_attempts[key] = attempts
                    self.send_json(403, {"ok": False, "error": "用户名或密码错误"})
                    return
                CONTROLLER.login_attempts.pop(key, None)
                sid, _ = CONTROLLER.new_session()
                self.send_json(200, {"ok": True}, {"Set-Cookie": f"site_session={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800"})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return
        auth = self.require_auth()
        if not auth:
            return
        sid, session = auth
        if not self.valid_csrf(session):
            self.send_json(403, {"ok": False, "error": "CSRF validation failed"})
            return
        try:
            body = self.body_json()
            if path == "/api/switch":
                CONTROLLER.set_policy(body.get("policy", ""))
            elif path == "/api/announcement":
                CONTROLLER.write_announcement(body.get("title", ""), body.get("message", ""), body.get("eta", ""))
            elif path == "/api/check":
                ok, latency, error, upstream = CONTROLLER.health_check()
                self.send_json(200, {"ok": True, "check": {"healthy": ok, "latency_ms": latency, "error": error, "upstream": upstream}})
                return
            elif path == "/api/logout":
                CONTROLLER.sessions.pop(sid, None)
                self.send_json(200, {"ok": True}, {"Set-Cookie": "site_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
                return
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
                return
            self.send_json(200, {"ok": True, "status": CONTROLLER.public_status()})
        except Exception as exc:
            CONTROLLER.audit("operation_failed", {"path": path, "error": str(exc)})
            self.send_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt, *args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-config", type=Path)
    args = parser.parse_args()
    if args.init_config:
        init_config(args.init_config)
        return
    global CONTROLLER
    CONTROLLER = Controller()
    monitor = threading.Thread(target=CONTROLLER.monitor_loop, daemon=True)
    monitor.start()
    server = ThreadingHTTPServer(("127.0.0.1", 9100), Handler)
    try:
        server.serve_forever()
    finally:
        CONTROLLER.stop_event.set()


if __name__ == "__main__":
    main()
