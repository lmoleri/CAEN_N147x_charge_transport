@echo off
REM Build the THGEM GUI one-folder Windows bundle with PyInstaller.
REM Run from a Windows machine with Python 3.10+ on PATH.
cd /d "%~dp0"

python -m venv .venv || goto :error
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements.txt -r requirements-dev.txt || goto :error
pyinstaller --noconfirm THGEM_GUI.spec || goto :error

echo.
echo Build complete: dist\THGEM_GUI\THGEM_GUI.exe
echo To use real hardware, copy CAENHVWrapper.dll next to THGEM_GUI.exe.
goto :eof

:error
echo.
echo Build FAILED. See messages above.
exit /b 1
