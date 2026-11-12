# -*- coding: utf-8 -*-
"""零依赖启动器：python serve.py 即可在浏览器打开 R1 Agent Studio。

不需要 pywebview、不需要打包、不需要安装任何第三方包（Python 3.9+ 标准库即可）。
浏览器访问 http://127.0.0.1:18600/

可选环境变量：
    R1_HOST        监听地址，默认 127.0.0.1
    R1_PORT        端口，默认 18600
    R1_GW_TOKEN    远程网关 Bearer Token（仅 auth=true 的链路使用）
    R1_ROUTES      链路 JSON 数组，覆盖默认链路
    R1_ROUTES_FILE 链路 JSON 文件路径
"""
import os
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server.app import serve  # noqa: E402

HOST = os.environ.get("R1_HOST", "127.0.0.1")
PORT = int(os.environ.get("R1_PORT", "18600"))


def _open_later():
    time.sleep(1.2)
    try:
        webbrowser.open(f"http://{HOST}:{PORT}/")
    except Exception:
        pass


if __name__ == "__main__":
    print(f"R1 Agent Studio  ->  http://{HOST}:{PORT}/")
    httpd, port = serve(PORT, HOST)
    print(f"R1 Agent Studio  ->  http://{HOST}:{port}/  (Ctrl+C 停止)")
    if os.environ.get("R1_NO_BROWSER") != "1":
        threading.Thread(target=_open_later, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
