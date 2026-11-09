# Windows x64 portable build. No personal data or local settings are included.
from PyInstaller.utils.hooks import collect_all
ofd_data, ofd_binaries, ofd_imports = collect_all('easyofd')
a = Analysis(['main.py'], pathex=[],
    binaries=ofd_binaries,
    datas=[('templates/index.html','templates'),('static','static')] + ofd_data,
    hiddenimports=ofd_imports + ['waitress'],
    excludes=['tkinter','pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
    name='InvoiceBox', console=True, icon='app.ico', upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='InvoiceBox', upx=False)
