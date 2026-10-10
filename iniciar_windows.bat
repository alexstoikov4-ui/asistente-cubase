@echo off
REM Lanza AI Audio Engineer. La primera vez crea un entorno virtual e instala numpy.
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
python -m aiengineer %*
