@echo off
REM RunDinoPet.bat — portable launcher for pet.py (uses pythonw for no console)

REM Change directory to the location of this .bat (the pet folder)
cd /d "%~dp0"

REM Prefer the Python 3.13 install; change this if your pythonw is elsewhere
set "PYW=%LocalAppData%\Programs\Python\Python313\pythonw.exe"

REM If that specific pythonw doesn't exist, fallback to 'pythonw' on PATH
if not exist "%PYW%" (
    set "PYW=pythonw.exe"
)

REM Start pythonw with the script. start "" keeps it detached.
start "" "%PYW%" "%~dp0pet.py"

exit /b 0
