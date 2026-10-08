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
rem Logs from an earlier test would end up in the zip next to these.
del /q "%OUT%\*.log" 2> nul

rem The game writes last_run.log into the saves folder chosen in the launcher (default: user).
set "LOGDIR=%GAME%\user"
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "try { $s = Get-Content -Raw (Join-Path $env:APPDATA 'bbport-launcher\settings.json') | ConvertFrom-Json; if ($s.user_dir) { $s.user_dir } } catch {}"`) do set "LOGDIR=%%U"
echo Logs are read from: %LOGDIR%> "%OUT%\where.txt"

powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion | Format-List" > "%OUT%\gpu.txt" 2>&1

echo.
rem Object motion on for this test even if it is switched off in the launcher (bbport.ini), with
rem FSR 3.1: object motion only runs with an upscaler.
set BB_OBJECT_MOTION_AMD=1
set BB_OBJECT_MOTION=1
set BB_UPSCALER=fsr3
set BB_FRAME_STATS=1
set BB_OM_STORE=
rem No saved shader cache: the part a run leaves out is not in the cache key, so a pipeline saved by
rem one run must not be preloaded by the next. The first minute stutters more; that is expected.
set BB_PIPELINE_CACHE=0

echo ===== RUN C of 3 =====
echo The game starts now. Press Continue and play until the game crashes, or for 2 minutes, then close the game.
echo It may crash right after the save loads. That is expected and is a useful result.
echo If the launcher window opens instead of the game, press PLAY in it, then close the launcher after the game.
echo If the game closes by itself with an error, that is fine: just wait for this window.
echo.
pause
set BB_OM_PART=nobda
if exist "%LOGDIR%\last_run.log" del "%LOGDIR%\last_run.log"
start /wait "" "%GAME%\Bloodborne.exe"
if exist "%LOGDIR%\last_run.log" (
    copy /y "%LOGDIR%\last_run.log" "%OUT%\C.log" > nul
    echo Run C log saved.
    findstr /c:"Object motion: on" "%OUT%\C.log" > nul || echo WARNING: object motion did not switch on in run C. Tell the person who sent you this test.
) else (
    echo No log found for run C. Tell the person who sent you this test.
)

echo.
echo ===== RUN D of 3 =====
echo Same again: press Continue and play until the game crashes, or for 2 minutes, then close the game.
echo If the launcher window opens instead of the game, press PLAY in it, then close the launcher after the game.
echo.
pause
set BB_OM_PART=nofs
if exist "%LOGDIR%\last_run.log" del "%LOGDIR%\last_run.log"
start /wait "" "%GAME%\Bloodborne.exe"
if exist "%LOGDIR%\last_run.log" (
    copy /y "%LOGDIR%\last_run.log" "%OUT%\D.log" > nul
    echo Run D log saved.
    findstr /c:"Object motion: on" "%OUT%\D.log" > nul || echo WARNING: object motion did not switch on in run D. Tell the person who sent you this test.
) else (
    echo No log found for run D. Tell the person who sent you this test.
)

echo.
echo ===== RUN E of 3 =====
echo Last one: press Continue and play until the game crashes, or for 2 minutes, then close the game.
echo If the launcher window opens instead of the game, press PLAY in it, then close the launcher after the game.
echo.
pause
set BB_OM_PART=novary
if exist "%LOGDIR%\last_run.log" del "%LOGDIR%\last_run.log"
start /wait "" "%GAME%\Bloodborne.exe"
if exist "%LOGDIR%\last_run.log" (
    copy /y "%LOGDIR%\last_run.log" "%OUT%\E.log" > nul
    echo Run E log saved.
    findstr /c:"Object motion: on" "%OUT%\E.log" > nul || echo WARNING: object motion did not switch on in run E. Tell the person who sent you this test.
) else (
    echo No log found for run E. Tell the person who sent you this test.
)

if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\*' -DestinationPath '%ZIP%' -Force"
echo.
echo Done. Please send this file: %ZIP%
pause
endlocal
