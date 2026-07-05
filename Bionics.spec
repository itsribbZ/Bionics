# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('config.yaml', '.'), ('plans', 'plans'), ('core', 'core'), ('ue5_modules', 'ue5_modules'), ('bionics_tools', 'bionics_tools')],
    hiddenimports=['anthropic', 'bionics_tools', 'bionics_tools.ue5_animgraph', 'core.bridge', 'core.agent', 'core.auto_planner', 'core.mvp_doctor', 'core.ue5_bridge', 'fastmcp', 'pydantic', 'mss', 'cv2', 'pyautogui', 'pynput', 'pynput.keyboard', 'pynput.keyboard._win32', 'pynput.mouse', 'pynput.mouse._win32', 'PIL', 'fitz', 'yaml', 'numpy', 'requests', 'structuresim'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Bionics',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Bionics',
)
