# PyInstaller build: produces dist/STJParts/STJParts.exe
# Build on Windows with:  pyinstaller --noconfirm stjparts.spec
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    datas=[("stjparts/web", "stjparts/web")],
    hiddenimports=collect_submodules("uvicorn") + ["barcode.writer"],
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="STJParts", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="STJParts")
