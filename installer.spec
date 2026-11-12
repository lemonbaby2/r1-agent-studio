# -*- mode: python ; coding: utf-8 -*-
"""安装程序打包：把 payload/ 整个塞进 Setup.exe"""
import os
HERE = os.path.dirname(os.path.abspath(SPEC))
a = Analysis(
    [os.path.join(HERE, 'installer.py')],
    pathex=[HERE],
    binaries=[],
    datas=[(os.path.join(HERE, 'payload'), 'payload')],
    hiddenimports=['winreg'],
    excludes=['matplotlib', 'numpy', 'pandas', 'scipy', 'PIL', 'cv2',
              'webview', 'bottle', 'proxy_tools', 'tkinter', 'clr'],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
          name='DGX-R1-Agent-Studio-Setup-v1.1.0',
          debug=False, strip=False, upx=False, console=True,
          icon=os.path.join(HERE, 'app.ico'))
