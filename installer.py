# -*- coding: utf-8 -*-
"""DGX R1 Agent Studio · 安装程序（自带解包，不依赖 NSIS）

用法：双击运行；或 `DGX-R1-Agent-Studio-Setup.exe /S` 静默安装。
安装位置：%LOCALAPPDATA%\\DGX R1 Agent Studio（免管理员）
"""
import os
import shutil
import subprocess
import sys
import time
import winreg

APP_NAME = "DGX R1 Agent Studio"
APP_VER = "1.1.0"
EXE_NAME = "DGX-R1-Agent-Studio.exe"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DGXR1AgentStudio"
REG_SOFT = r"Software\DGXR1AgentStudio"

FILES = [EXE_NAME, "使用说明.txt", "一键自检.cmd", "app.ico"]

BANNER = r"""
  ____   ____  __  __    ____  _   _    _                    _
 |  _ \ / ___| \ \/ /   |  _ \| | | |  / \   _ __   __ _  ___| |_
 | | | | |  _   \  /____| |_) | |_| | / _ \ | '_ \ / _` |/ _ \ __|
 | |_| | |_| |  /  \____|  _ <|  _  |/ ___ \| | | | (_| |  __/ |_
 |____/ \____| /_/\_\   |_| \_\_| |_/_/   \_\_| |_|\__, |\___|\__|
                                                   |___/
          DGX R1 Agent Studio · 安装程序 v%s
""" % APP_VER


def payload_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, "payload"), base):
        if os.path.isfile(os.path.join(cand, EXE_NAME)):
            return cand
    return os.path.join(base, "payload")


def target_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        APP_NAME)


def log(msg, quiet=False):
    if not quiet:
        print(msg, flush=True)


def mkshortcut(lnk, target, icon=None, workdir=None):
    icon = icon or target
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s,0';$s.Save()"
          % (lnk, target, workdir or os.path.dirname(target), icon))
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", ps],
                   capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def make_uninstaller(dst):
    bat = r"""@echo off
chcp 936 >nul
set D=%~dp0
echo 正在卸载 DGX R1 Agent Studio ...
del "%USERPROFILE%\Desktop\DGX R1 Agent Studio.lnk" 2>nul
del "%USERPROFILE%\Desktop\DGX R1 一键自检.lnk" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\DGX R1 Agent Studio\*.lnk" 2>nul
rmdir "%APPDATA%\Microsoft\Windows\Start Menu\Programs\DGX R1 Agent Studio" 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DGXR1AgentStudio" /f >nul 2>&1
reg delete "HKCU\Software\DGXR1AgentStudio" /f >nul 2>&1
choice /c YN /n /m "是否同时删除配置和会话记录 (Y=删除 / N=保留)? "
if errorlevel 2 goto keep
rmdir /s /q "%APPDATA%\DGX-R1-Agent-Studio" 2>nul
:keep
echo 正在删除程序文件 ...
del "%D%DGX-R1-Agent-Studio.exe" 2>nul
del "%D%使用说明.txt" 2>nul
del "%D%一键自检.cmd" 2>nul
del "%D%app.ico" 2>nul
del "%D%Uninstall.cmd" 2>nul
echo.
echo 卸载完成，此窗口可关闭。
timeout /t 3 >nul
"""
    path = os.path.join(dst, "Uninstall.cmd")
    with open(path, "wb") as f:
        f.write(bat.replace("\n", "\r\n").encode("gbk", "ignore"))
    return path


def main():
    silent = "/S" in sys.argv or "/s" in sys.argv
    if not silent:
        print(BANNER)

    src = payload_dir()
    dst = target_dir()
    missing = [f for f in FILES if not os.path.isfile(os.path.join(src, f))]
    if missing:
        print("[错误] 安装包不完整，缺少：%s" % ", ".join(missing))
        print("安装源目录：%s" % src)
        if not silent:
            input("按回车退出...")
        return 2

    log("安装到：%s" % dst, silent)
    os.makedirs(dst, exist_ok=True)

    for f in FILES:
        s = os.path.join(src, f)
        d = os.path.join(dst, f)
        try:
            if os.path.exists(d):
                os.remove(d)
        except Exception:
            pass
        shutil.copy2(s, d)
        log("  + %s" % f, silent)

    un = make_uninstaller(dst)
    log("  + Uninstall.cmd", silent)

    exe = os.path.join(dst, EXE_NAME)
    ico = os.path.join(dst, "app.ico")
    cmd = os.path.join(dst, "一键自检.cmd")
    desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    startm = os.path.join(os.environ.get("APPDATA", ""),
                          r"Microsoft\Windows\Start Menu\Programs\DGX R1 Agent Studio")

    try:
        os.makedirs(startm, exist_ok=True)
        if os.path.isdir(desktop):
            mkshortcut(os.path.join(desktop, "DGX R1 Agent Studio.lnk"), exe, ico)
            mkshortcut(os.path.join(desktop, "DGX R1 一键自检.lnk"), cmd, ico)
            log("  + 桌面快捷方式 ×2", silent)
        mkshortcut(os.path.join(startm, "DGX R1 Agent Studio.lnk"), exe, ico)
        mkshortcut(os.path.join(startm, "一键自检.lnk"), cmd, ico)
        mkshortcut(os.path.join(startm, "卸载.lnk"), un, ico)
        log("  + 开始菜单", silent)
    except Exception as e:  # noqa: BLE001
        log("  ! 快捷方式创建失败：%s" % e, silent)

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_SOFT) as k:
            winreg.SetValueEx(k, "InstallDir", 0, winreg.REG_SZ, dst)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VER)
            winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "lzp")
            winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, ico)
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, dst)
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, '"%s"' % un)
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        log("  + 注册卸载项（控制面板可见）", silent)
    except Exception as e:  # noqa: BLE001
        log("  ! 写注册表失败：%s" % e, silent)

    log("", silent)
    log("安装完成。", silent)

    if silent:
        return 0

    print("")
    print("  桌面已生成两个图标：")
    print("    · DGX R1 Agent Studio   —— 打开界面")
    print("    · DGX R1 一键自检       —— 链路/模型/工具全检，结果用记事本打开")
    print("")
    try:
        ans = input("现在启动吗？(Y/n) ").strip().lower()
    except Exception:
        ans = ""
    if ans in ("", "y", "yes"):
        try:
            os.startfile(exe)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            print("启动失败：%s" % e)
            input("按回车退出...")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            input("出错了，按回车退出...")
        except Exception:
            pass
        sys.exit(1)
