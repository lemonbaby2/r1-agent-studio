# -*- coding: utf-8 -*-
"""本地 HTTP 服务：静态页面 + SSE 流式 Agent + 文件浏览 + 会话持久化。"""
import base64
import json
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agent, llm

APP_NAME = "DGX R1 Agent Studio"
VERSION = "1.1.0"

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                        "DGX-R1-Agent-Studio")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SESS_PATH = os.path.join(DATA_DIR, "sessions.json")

DEFAULT_SYSTEM_NOTE = ""

WEB_DIR = None          # 由 main.py 注入
_STOP = threading.Event()
_BUSY = threading.Lock()


def web_dir():
    global WEB_DIR
    if WEB_DIR and os.path.isdir(WEB_DIR):
        return WEB_DIR
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "web"),
                 os.path.join(getattr(sys, "_MEIPASS", here), "web")):
        cand = os.path.abspath(cand)
        if os.path.isdir(cand):
            WEB_DIR = cand
            return cand
    return os.path.abspath(os.path.join(here, "..", "web"))


# --------------------------------------------------------------------------
# 配置 / 会话
# --------------------------------------------------------------------------
def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


DEFAULT_CONFIG = {
    "base": "",
    "route_label": "",
    "model": "qwen3-coder:30b",
    "temperature": 0.6,
    "max_steps": 8,
    "agent": True,
    "show_think": True,
    "cwd": os.path.expanduser("~"),
    "system_note": "",
    "sizes": {},
    "win": {"w": 1320, "h": 800},
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read(CONFIG_PATH, {}) or {})
    if not cfg.get("cwd") or not os.path.isdir(cfg.get("cwd")):
        cfg["cwd"] = os.path.expanduser("~")
    return cfg


def save_config(cfg):
    _write(CONFIG_PATH, cfg)


def mask(tok):
    return (tok[:12] + "…") if tok else "(无需)"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "R1Studio/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # 静音
        pass

    # ---------------- 基础 ----------------
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8", "ignore"))
        except Exception:
            return {}

    def _static(self, rel):
        root = web_dir()
        p = os.path.abspath(os.path.join(root, rel.lstrip("/")))
        if not p.startswith(os.path.abspath(root)):
            return self._send(403, {"error": "forbidden"})
        if not os.path.isfile(p):
            p = os.path.join(root, "index.html")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(p)[1].lower(), "application/octet-stream")
        with open(p, "rb") as f:
            data = f.read()
        self._send(200, data, ctype)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith(("/app.", "/favicon", "/logo", "/v")):
                return self._static(path)
            if path == "/api/state":
                return self._send(200, self._state())
            if path == "/api/fs":
                q = self._query()
                return self._send(200, self._fs_list(q.get("path") or ""))
            if path == "/api/file":
                q = self._query()
                return self._send(200, self._fs_read(q.get("path") or ""))
            if path == "/api/sessions":
                return self._send(200, {"sessions": _read(SESS_PATH, []) or []})
            if path == "/api/health":
                return self._send(200, {"ok": True, "version": VERSION})
            return self._send(404, {"error": "not found"})
        except Exception:
            return self._send(500, {"error": traceback.format_exc()[-800:]})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/chat":
                return self._chat(self._json_body())
            if path == "/api/stop":
                _STOP.set()
                return self._send(200, {"ok": True})
            if path == "/api/config":
                return self._send(200, self._save_cfg(self._json_body()))
            if path == "/api/sessions":
                body = self._json_body()
                _write(SESS_PATH, body.get("sessions") or [])
                return self._send(200, {"ok": True})
            if path == "/api/route/refresh":
                return self._send(200, self._route(force=True))
            if path == "/api/selftest":
                return self._send(200, self._selftest())
            if path == "/api/models":
                return self._send(200, self._models())
            return self._send(404, {"error": "not found"})
        except Exception:
            return self._send(500, {"error": traceback.format_exc()[-800:]})

    def _query(self):
        from urllib.parse import parse_qs, unquote, urlparse
        q = parse_qs(urlparse(self.path).query)
        return {k: unquote(v[0]) for k, v in q.items()}

    # ---------------- 业务 ----------------
    def _route(self, force=False):
        cfg = load_config()
        cur = cfg.get("base") or ""
        # 已保存的链路先快速探一次，通了就不必全扫
        if cur and not force:
            tok = None if "11434" in cur else llm.GW_TOKEN
            ok, ms = llm.probe(cur, tok, 1.5)
            if ok:
                return {"base": cur, "token": tok, "label": cfg.get("route_label") or "已保存",
                        "ms": ms, "candidates": []}
        r = llm.auto_route(force=force, saved=cur)
        if r.get("base"):
            cfg["base"] = r["base"]
            cfg["route_label"] = r.get("label") or ""
            save_config(cfg)
        return r

    def _models(self):
        cfg = load_config()
        route = self._route()
        base = route.get("base") or cfg.get("base")
        if not base:
            return {"ok": False, "models": [], "error": "没有可用链路"}
        try:
            ms = llm.list_models(base, route.get("token"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "models": [], "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        out = []
        for m in ms:
            nm = m.get("name") or ""
            out.append({"name": nm,
                        "size": m.get("size") or 0,
                        "tags": llm.caps_of(nm),
                        "reasoner": llm.is_reasoner(nm)})
        out.sort(key=lambda x: (-x["size"]))
        return {"ok": True, "models": out, "base": base,
                "route_label": route.get("label"), "ms": route.get("ms")}

    def _state(self):
        cfg = load_config()
        route = self._route()
        models = self._models() if route.get("base") else {"models": []}
        return {
            "ok": True,
            "version": VERSION,
            "config": cfg,
            "route": {"base": route.get("base"), "label": route.get("label"),
                      "ms": route.get("ms"), "token": mask(route.get("token")),
                      "candidates": [{"label": c["label"], "base": c["base"],
                                      "ok": c["ok"], "ms": c["ms"]}
                                     for c in (route.get("candidates") or [])]},
            "models": models.get("models") or [],
            "host": "local",  # open-source build: do not leak hostname
            "data_dir": DATA_DIR,
        }

    def _save_cfg(self, body):
        cfg = load_config()
        for k in ("base", "model", "temperature", "max_steps", "agent",
                  "show_think", "cwd", "system_note", "route_label"):
            if k in body:
                cfg[k] = body[k]
        try:
            cfg["temperature"] = float(cfg.get("temperature") or 0.6)
            cfg["max_steps"] = int(cfg.get("max_steps") or 8)
        except Exception:
            cfg["temperature"], cfg["max_steps"] = 0.6, 8
        save_config(cfg)
        return {"ok": True, "config": cfg}

    def _fs_list(self, path):
        path = path or load_config().get("cwd") or os.path.expanduser("~")
        p = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(p):
            p = os.path.dirname(p)
        if not os.path.isdir(p):
            return {"ok": False, "error": "目录不存在", "path": p}
        items = []
        try:
            with os.scandir(p) as it:
                for e in it:
                    try:
                        isdir = e.is_dir()
                        size = 0 if isdir else e.stat().st_size
                    except Exception:
                        isdir, size = False, 0
                    items.append({"name": e.name, "dir": isdir, "size": size})
        except PermissionError:
            return {"ok": False, "error": "无权限", "path": p}
        items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        parent = os.path.dirname(p)
        return {"ok": True, "path": p, "parent": parent if parent != p else None,
                "items": items[:500]}

    def _fs_read(self, path):
        p = os.path.abspath(os.path.expanduser(path or ""))
        if not os.path.isfile(p):
            return {"ok": False, "error": "文件不存在"}
        if os.path.getsize(p) > 3 * 1024 * 1024:
            return {"ok": False, "error": "文件超过 3MB，请在对话里让 Agent 分段读"}
        raw = open(p, "rb").read()
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return {"ok": True, "path": p, "name": os.path.basename(p),
                        "text": raw.decode(enc)}
            except Exception:
                continue
        return {"ok": False, "error": "二进制或未知编码文件"}

    def _selftest(self):
        return do_selftest()

    # ---------------- 流式对话 ----------------
    def _chat(self, body):
        if not _BUSY.acquire(blocking=False):
            return self._send(429, {"error": "上一个任务还在跑，请先点停止或等它结束"})
        _STOP.clear()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # SSE 没有 Content-Length，若用 keep-alive 客户端永远等不到 EOF，
            # 表现为「模型答完了但页面一直转圈」/ 脚本读流卡死。必须显式关闭连接。
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True

            def emit(d):
                try:
                    self.wfile.write(("data: " + json.dumps(d, ensure_ascii=False) + "\n\n")
                                     .encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    _STOP.set()

            cfg = load_config()
            route = self._route()
            if not route.get("base"):
                emit({"type": "error", "message": "没有可用链路：DGX 与 PC 都探不通。请检查网络或 VPN。"})
                emit({"type": "end", "answer": "", "steps": [], "ms": 0})
                return

            model = body.get("model") or cfg["model"]
            temperature = float(body.get("temperature") or cfg["temperature"])
            max_steps = int(body.get("max_steps") or cfg["max_steps"])
            cwd = body.get("cwd") or cfg["cwd"]
            history = body.get("messages") or []
            images = body.get("images") or None
            if images:
                images = [im.split(",", 1)[-1] for im in images]
            direct = not bool(body.get("agent", cfg.get("agent", True)))

            agent.run(route, model, history, emit,
                      stop_flag=_STOP, max_steps=max_steps, cwd=cwd,
                      temperature=temperature, images=images, direct=direct,
                      note=cfg.get("system_note") or "")
        except Exception:
            try:
                self.wfile.write(("data: " + json.dumps(
                    {"type": "error", "message": traceback.format_exc()[-500:]},
                    ensure_ascii=False) + "\n\n").encode("utf-8"))
            except Exception:
                pass
        finally:
            _BUSY.release()


def seed_sessions():
    """首次运行时把内置示例会话放进用户数据目录。

    目的：装完第一次打开就有东西可看，用户立刻能明白
    「已思考 / 执行轨迹 / 步骤卡」长什么样，而不是面对一个空白页。
    只在 sessions.json 不存在时写一次，永不覆盖用户自己的会话。
    """
    try:
        if os.path.exists(SESS_PATH):
            return
        src = os.path.join(web_dir(), "seed_sessions.json")
        if not os.path.isfile(src):
            return
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.makedirs(DATA_DIR, exist_ok=True)
        _write(SESS_PATH, data)
    except Exception:
        pass


def serve(port=0, host="127.0.0.1"):
    """起 HTTP 服务。

    port=0 时不再交给系统随机分配，而是在 18600-18699 里挑一个空口。
    原因：Windows 临时端口段（49152+）会被 WebView2 的连接反复占用，
    实测出现过"窗口能打开、几秒后监听消失"的情况，固定段位可以规避。
    """
    seed_sessions()
    if port:
        httpd = ThreadingHTTPServer((host, port), Handler)
    else:
        last = None
        httpd = None
        for p in range(18600, 18700):
            try:
                httpd = ThreadingHTTPServer((host, p), Handler)
                break
            except OSError as e:
                last = e
                continue
        if httpd is None:
            raise last or OSError("18600-18699 全部被占用")
    httpd.daemon_threads = True
    return httpd, httpd.server_address[1]


def do_selftest():
    """链路 + 模型 + 工具三项自检。CLI（--selftest）与界面共用。"""
    from . import tools as _tools
    out = []
    cfg = load_config()
    t0 = time.time()

    r = llm.auto_route(force=True)
    out.append({"k": "链路探测", "ok": bool(r.get("base")),
                "v": "%s %s (%sms)" % (r.get("label") or "-",
                                       r.get("base") or "-", r.get("ms"))})
    for c in (r.get("candidates") or []):
        out.append({"k": "  ↳ " + c["label"], "ok": c["ok"], "v": "%sms" % c["ms"]})
    if not r.get("base"):
        out.append({"k": "总耗时", "ok": False, "v": "%.1fs · 无可用链路" % (time.time() - t0)})
        return {"ok": False, "items": out}

    cfg["base"], cfg["route_label"] = r["base"], r.get("label") or ""
    save_config(cfg)

    try:
        ms = llm.list_models(r["base"], r.get("token"))
        out.append({"k": "模型列表", "ok": bool(ms), "v": "%d 个 · %s" % (
            len(ms), ", ".join(sorted((m.get("name") or "") for m in ms)[:4]))})
    except Exception as e:  # noqa: BLE001
        out.append({"k": "模型列表", "ok": False, "v": str(e)[:120]})

    try:
        t1 = time.time()
        txt = llm.chat_once(r["base"], r.get("token"), cfg["model"],
                            [{"role": "user", "content": "只回复两个字：通了"}],
                            temperature=0.1, num_predict=24, timeout=180)
        out.append({"k": "真实推理 · %s" % cfg["model"], "ok": "通" in (txt or ""),
                    "v": "%.1fs · %s" % (time.time() - t1, (txt or "").strip()[:40])})
    except Exception as e:  # noqa: BLE001
        out.append({"k": "真实推理", "ok": False, "v": str(e)[:140]})

    try:
        rr = _tools.call("list_dir", {"path": cfg["cwd"]})
        out.append({"k": "本地工具 list_dir", "ok": bool(rr.get("ok")),
                    "v": (rr.get("summary") or "")[:70]})
    except Exception as e:  # noqa: BLE001
        out.append({"k": "本地工具", "ok": False, "v": str(e)[:120]})

    try:
        rr = _tools.call("run_python", {"code": "print(6*7)"})
        out.append({"k": "本地工具 run_python", "ok": bool(rr.get("ok")) and "42" in (rr.get("output") or ""),
                    "v": "6*7 = " + ("42" if "42" in (rr.get("output") or "") else "?!")})
    except Exception as e:  # noqa: BLE001
        out.append({"k": "本地工具 run_python", "ok": False, "v": str(e)[:120]})

    out.append({"k": "总耗时", "ok": True, "v": "%.1fs" % (time.time() - t0)})
    return {"ok": all(x["ok"] for x in out if not x["k"].startswith("  ↳")),
            "items": out}

