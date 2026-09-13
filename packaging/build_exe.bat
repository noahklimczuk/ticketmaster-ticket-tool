@echo off
REM Build ticketwatch.exe on Windows. Needs Python 3.9+ installed.
REM Run from the repository root:  packaging\build_exe.bat

echo Installing PyInstaller...
python -m pip install --quiet --upgrade pyinstaller || goto :error

echo Building...
python -m PyInstaller --clean --noconfirm --distpath dist --workpath build\pyinstaller ^
    packaging\ticketwatch.spec || goto :error

echo.
echo Done: dist\ticketwatch.exe
echo Double-click it to open the control panel.
exit /b 0

:error
echo.
echo Build failed. Check the messages above.
exit /b 1
