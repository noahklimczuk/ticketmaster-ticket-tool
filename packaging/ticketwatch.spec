# PyInstaller spec: one self-contained executable, no Python install needed.
#
#   pyinstaller packaging/ticketwatch.spec
#
# Run it on the platform you want a binary for - PyInstaller does not
# cross-compile, so a Windows .exe has to be built on Windows. The GitHub
# Actions workflow in .github/workflows/build-exe.yml does exactly that.

block_cipher = None

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
    # The panel's HTML lives in ticketwatch/webui.py, and gui/providers are
    # imported lazily, so spell them out for the analyser.
    hiddenimports=[
        "ticketwatch.aggregate",
        "ticketwatch.cli",
        "ticketwatch.gui",
        "ticketwatch.webui",
        "ticketwatch.http",
        "ticketwatch.providers",
        "ticketwatch.providers.bandsintown",
        "ticketwatch.providers.links",
        "ticketwatch.providers.seatgeek",
        "ticketwatch.providers.ticketmaster",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Nothing here needs a GUI toolkit or the scientific stack.
    excludes=["tkinter", "numpy", "pandas", "matplotlib", "PIL", "test", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ticketwatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,          # the window doubles as the log; closing it quits
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
