# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gui_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('frontend', 'frontend')],
    hiddenimports=['uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'fastapi', 'starlette', 'pydantic', 'psutil', 'aiofiles', 'websockets', 'humanize', 'PIL', 'pystray', 'webview', 'customtkinter'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pygments', 'IPython', 'ipykernel', 'matplotlib', 'scipy', 'pygame', 'jupyter', 'jupyter_client', 'jupyter_core', 'tkinter.test', 'unittest', 'django', 'flask', 'docker', 'dipy', 'h5py', 'nibabel', 'esphome', 'aioesphomeapi', 'zeroconf', 'cryptography', 'setuptools', 'distutils', 'pkg_resources'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DiskPulse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\adity\\Downloads\\files\\DiskPulse\\DiskPulseNAS\\diskpulse.ico'],
)
