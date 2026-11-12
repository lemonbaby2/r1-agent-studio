# -*- mode: python ; coding: utf-8 -*-
"""DGX R1 Agent Studio · PyInstaller 打包配置（单文件 exe，内嵌 web 静态资源）"""
import os

HERE = os.path.dirname(os.path.abspath(SPEC))

datas = [(os.path.join(HERE, 'web'), 'web')]

hiddenimports = [
    'server', 'server.app', 'server.agent', 'server.llm', 'server.tools',
    'webview', 'webview.platforms', 'webview.platforms.edgechromium',
    'webview.platforms.winforms', 'clr', 'bottle', 'proxy_tools',
]

try:
    from PyInstaller.utils.hooks import collect_data_files
    datas += collect_data_files('webview', includes=['*.js', '*.html', '*.css'])
except Exception:
    pass

a = Analysis(
    [os.path.join(HERE, 'main.py')],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy', 'PIL', 'cv2',
        'PyQt5', 'PySide2', 'PySide6', 'wx', 'test', 'unittest',
        'pydoc_data', 'IPython', 'notebook', 'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DGX-R1-Agent-Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(HERE, 'app.ico') if os.path.exists(os.path.join(HERE, 'app.ico')) else None,
)
