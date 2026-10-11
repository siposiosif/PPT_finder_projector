# -*- mode: python ; coding: utf-8 -*-

datas = [('web', 'web')]
binaries = []
hiddenimports = [
    'web_server', 'realtime_asr',
    'torch.testing',
    'transformers.models.parakeet.configuration_parakeet',
    'transformers.models.parakeet.feature_extraction_parakeet',
    'transformers.models.parakeet.generation_parakeet',
    'transformers.models.parakeet.modeling_parakeet',
    'transformers.models.parakeet.processing_parakeet',
    'transformers.models.parakeet.tokenization_parakeet',
    'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on',
]

a = Analysis(
    ['voice_executable.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'IPython', 'jupyter', 'matplotlib.tests'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Voice_Recognition',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Voice_Recognition',
)
