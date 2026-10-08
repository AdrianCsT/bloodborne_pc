@echo off
setlocal
rem AMD object motion test. Put this folder (amd-motion-test) inside your Bloodborne folder, the one
rem that holds Bloodborne.exe, then double-click this file. See README-TEST.txt.

set "KIT=%~dp0"
for %%I in ("%KIT%..") do set "GAME=%%~fI"
if not exist "%GAME%\Bloodborne.exe" (
    echo Bloodborne.exe was not found one folder above this test.
    echo Copy the amd-motion-test folder into your Bloodborne folder, next to Bloodborne.exe, and try again.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%D"
set "OUT=%DESK%\bloodborne-motion-test"
set "ZIP=%DESK%\bloodborne-motion-test.zip"
if not exist "%OUT%" mkdir "%OUT%"

powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion | Format-List" > "%OUT%\gpu.txt" 2>&1

echo.
echo ===== RUN A of 2 =====
echo The game starts now. Press Continue, play for about 2 minutes, then close the game.
echo If the game closes by itself with an error, that is fine: just wait for this window.
echo.
pause
rem Object motion on for this test even if it is switched off in the launcher (bbport.ini), with
rem FSR 3.1: object motion only runs with an upscaler.
set BB_OBJECT_MOTION_AMD=1
set BB_OBJECT_MOTION=1
set BB_UPSCALER=fsr3
set BB_FRAME_STATS=1
set BB_OM_STORE=
if exist "%GAME%\user\last_run.log" del "%GAME%\user\last_run.log"
start /wait "" "%GAME%\Bloodborne.exe"
if exist "%GAME%\user\last_run.log" (
    copy /y "%GAME%\user\last_run.log" "%OUT%\A.log" > nul
    echo Run A log saved.
    findstr /c:"Object motion: on" "%OUT%\A.log" > nul || echo WARNING: object motion did not switch on in run A. Tell the person who sent you this test.
) else (
    echo No log found for run A. Tell the person who sent you this test.
)

echo.
echo ===== RUN B of 2 =====
echo Same again: press Continue, play for about 2 minutes, then close the game.
echo.
pause
set BB_OM_STORE=plain
if exist "%GAME%\user\last_run.log" del "%GAME%\user\last_run.log"
start /wait "" "%GAME%\Bloodborne.exe"
if exist "%GAME%\user\last_run.log" (
    copy /y "%GAME%\user\last_run.log" "%OUT%\B.log" > nul
    echo Run B log saved.
    findstr /c:"Object motion: on" "%OUT%\B.log" > nul || echo WARNING: object motion did not switch on in run B. Tell the person who sent you this test.
) else (
    echo No log found for run B. Tell the person who sent you this test.
)

if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\*' -DestinationPath '%ZIP%' -Force"
echo.
echo Done. Please send this file: %ZIP%
pause
endlocal
