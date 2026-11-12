# -*- coding: utf-8 -*-
"""DGX R1 Agent Studio · 程序入口

启动顺序：先起本地 HTTP 服务（127.0.0.1 随机端口），再用 pywebview 开一个原生窗口。
pywebview 不可用时自动回落系统默认浏览器，保证一定能打开。
"""
import os
import sys
import threading
import time
import webbrowser


def _dpi_aware():
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _runpy(argv):
    """--runpy <file>：给工具链里的 run_python 用（打包成 exe 后 sys.executable 就是自己）。"""
    if len(argv) < 2:
        print("usage: --runpy <file.py>")
        return 2
    path = argv[1]
    sys.argv = [path]
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    code = compile(src, path, "exec")
    g = {"__name__": "__main__", "__file__": path,
         "__builtins__": __builtins__}
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    exec(code, g)  # noqa: S102
    return 0


def _fit_window_later(timeout=12.0):
    """窗口创建后再按物理像素摆一次。

    pywebview 的 width/height 会被系统缩放再乘一次（125% → ×1.25），
    结果窗口高过屏幕，底部输入区被推到屏幕外。这里等窗口出现后
    用 Win32 直接 SetWindowPos，尺寸一律按物理像素算，确保整窗可见。
    """
    def work():
        import ctypes
        from ctypes import wintypes as w
        u = ctypes.windll.user32
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
        t0 = time.time()
        hwnd = None
        while time.time() - t0 < timeout and not hwnd:
            time.sleep(0.6)
            hits = []

            def cb(h, _l):
                n = ctypes.create_unicode_buffer(256)
                u.GetWindowTextW(h, n, 256)
                if "DGX R1 Agent Studio" in n.value and u.IsWindowVisible(h):
                    hits.append(h)
                return True

            u.EnumWindows(CB(cb), 0)
            if hits:
                hwnd = hits[0]
        if not hwnd:
            return
        try:
            sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            cw = min(int(sw * 0.90), 1500)
            ch = min(int(sh * 0.86), 900)
            x = max(10, (sw - cw) // 2)
            y = max(10, (sh - ch) // 2 - 24)
            u.SetWindowPos(hwnd, 0, x, y, cw, ch, 0x0004)   # SWP_NOZORDER
        except Exception:
            pass

    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        pass


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] == "--runpy":
        return _runpy(argv)

    _dpi_aware()
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    from server import app as sapp

    # 打包后 web 静态资源在 _MEIPASS/web
    base = getattr(sys, "_MEIPASS", here)
    sapp.WEB_DIR = os.path.join(base, "web")

    # ---- 命令行自检（窗口程序没有控制台，结果写成 txt 用记事本打开）----
    if argv and argv[0] in ("--selftest", "-t"):
        try:
            r = sapp.do_selftest()
        except Exception:
            import traceback
            r = {"ok": False, "items": [{"k": "自检异常", "ok": False,
                                        "v": traceback.format_exc()[-400:]}]}
        lines = ["DGX R1 Agent Studio · 自检报告",
                 "时间：" + time.strftime("%Y-%m-%d %H:%M:%S"),
                 "状态：" + ("全部通过" if r.get("ok") else "有项目未通过"), ""]
        for it in r.get("items") or []:
            k = it.get("k") or ""
            tag = "  ·  " if k.startswith("  ↳") else ("[OK ] " if it.get("ok") else "[FAIL]")
            lines.append("%s %-24s %s" % (tag, k.strip(), it.get("v")))
        txt = "\n".join(lines)
        out = os.path.join(sapp.DATA_DIR, "selftest.txt")
        try:
            os.makedirs(sapp.DATA_DIR, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(txt + "\n")
        except Exception:
            out = os.path.join(os.environ.get("TEMP", "."), "dgx-r1-selftest.txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write(txt + "\n")
        try:
            print(txt)
        except Exception:
            pass
        try:
            os.startfile(out)   # noqa: S606
        except Exception:
            pass
        return 0 if r.get("ok") else 1


    httpd, port = sapp.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % port

    # 运行时信息落盘：排障时直接看这个文件就知道起在哪个端口
    try:
        os.makedirs(sapp.DATA_DIR, exist_ok=True)
        with open(os.path.join(sapp.DATA_DIR, "runtime.txt"), "w", encoding="utf-8") as f:
            f.write("pid=%d\nport=%d\nurl=%s\nfrozen=%s\nat=%s\n"
                    % (os.getpid(), port, url, bool(getattr(sys, "frozen", False)),
                       time.strftime("%Y-%m-%d %H:%M:%S")))
    except Exception:
        pass

    cfg = sapp.load_config()
    w = int((cfg.get("win") or {}).get("w") or 1360)
    h = int((cfg.get("win") or {}).get("h") or 820)
    # pywebview 会把 w/h 再乘一次系统缩放（125% → ×1.25），
    # 直接给 900 会变成 1125 物理像素，窗口高过屏幕 → 底部输入区被推到屏幕外。
    # 所以这里按缩放比例反算，保证物理尺寸装得下。
    try:
        import ctypes
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        try:
            scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
        except Exception:
            scale = 1.0
        if scale <= 0:
            scale = 1.0
        max_w = int(sw / scale * 0.92)
        max_h = int(sh / scale * 0.86)
        w = max(1000, min(w, max_w))
        h = max(620, min(h, max_h))
    except Exception:
        pass

    if "--browser" in argv:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    try:
        import webview
    except Exception:
        webview = None

    if webview is None:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    class Api:
        """暴露给前端 JS 的原生能力。"""

        def pick_folder(self):
            try:
                w0 = webview.windows[0]
                r = w0.create_file_dialog(webview.FOLDER_DIALOG)
                if r:
                    return r[0] if isinstance(r, (list, tuple)) else r
            except Exception:
                pass
            return ""

        def open_external(self, url_):
            try:
                webbrowser.open(url_)
                return True
            except Exception:
                return False

        def app_info(self):
            return {"version": sapp.VERSION, "data_dir": sapp.DATA_DIR,
                    "frozen": bool(getattr(sys, "frozen", False))}

    try:
        window = webview.create_window(
            sapp.APP_NAME, url, width=w, height=h,
            min_size=(1080, 680), background_color="#f7f8fa",
            js_api=Api(), text_select=True,
        )
        _fit_window_later()
        webview.start(debug=False)
        # 退出前记住窗口尺寸
        try:
            cfg = sapp.load_config()
            cfg["win"] = {"w": int(window.width or w), "h": int(window.height or h)}
            sapp.save_config(cfg)
        except Exception:
            pass
    except Exception:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
