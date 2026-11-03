@echo off
chcp 65001 >nul
setlocal
title nightcut Installation

rem ===================================================================
rem  nightcut - Installation fuer Windows 10/11
rem  Installiert Git, ffmpeg, Ollama und uv (per winget), holt nightcut
rem  von GitHub, richtet die Python-Umgebung ein und laedt die KI-Modelle.
rem  Einfach doppelklicken. Erneut ausfuehren = Update.
rem ===================================================================

set "REPO=https://github.com/Nilson89/nightcut.git"
set "VISION_MODEL=qwen2.5vl:7b"
set "EDIT_MODEL=qwen3:14b"

echo.
echo  =============================================
echo   nightcut Installation
echo  =============================================
echo.
echo  Das Skript installiert Git, ffmpeg, Ollama und uv,
echo  holt nightcut von GitHub und laedt die KI-Modelle.
echo  Benoetigt etwa 25 GB freien Speicher und eine Internetverbindung.
echo  Je nach Leitung dauert das 15 bis 60 Minuten.
echo.
pause

rem --- Voraussetzungen ------------------------------------------------
where winget >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [FEHLER] winget wurde nicht gefunden.
    echo  Installiere den "App-Installer" aus dem Microsoft Store und starte das Skript erneut.
    goto :fail
)

where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARNUNG] Keine NVIDIA-Grafikkarte bzw. kein NVIDIA-Treiber gefunden.
    echo  nightcut braucht eine NVIDIA-GPU mit 16 GB VRAM und einen aktuellen Treiber.
    echo.
    pause
) else (
    echo.
    echo  Gefundene Grafikkarte:
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
)

rem --- 1. Programme -----------------------------------------------------
echo.
echo  [1/5] Programme pruefen und installieren ...
call :ensure git    Git.Git        "Git"     || goto :fail
call :ensure ffmpeg Gyan.FFmpeg    "ffmpeg"  || goto :fail
call :ensure ollama Ollama.Ollama  "Ollama"  || goto :fail
call :ensure uv     astral-sh.uv   "uv"      || goto :fail
call :refreshpath

for %%T in (git ffmpeg ollama uv) do (
    where %%T >nul 2>&1 || (
        echo  [FEHLER] %%T ist installiert, aber noch nicht im PATH.
        echo  Bitte das Fenster schliessen und install.bat noch einmal starten.
        goto :fail
    )
)

rem --- 2. Zielordner ----------------------------------------------------
rem Desktop, ausser er wird von OneDrive synchronisiert (sonst laedt OneDrive
rem die mehrere GB grosse Python-Umgebung hoch). Dann C:\nightcut + Verknuepfung.
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP=%%D"
set "TARGET=%DESKTOP%\nightcut"
set "SHORTCUT="
echo "%DESKTOP%" | findstr /i "OneDrive" >nul && (
    set "TARGET=C:\nightcut"
    set "SHORTCUT=1"
)

echo.
echo  [2/5] nightcut nach "%TARGET%" holen ...
if exist "%TARGET%\.git" (
    echo  Schon vorhanden, hole Updates ...
    git -C "%TARGET%" pull --ff-only || goto :fail
) else (
    if exist "%TARGET%" (
        echo  [FEHLER] "%TARGET%" existiert schon, ist aber kein nightcut-Repository.
        echo  Bitte umbenennen oder loeschen und das Skript erneut starten.
        goto :fail
    )
    git clone "%REPO%" "%TARGET%" || goto :fail
)

if defined SHORTCUT (
    powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%DESKTOP%\nightcut.lnk'); $s.TargetPath='%TARGET%'; $s.Save()" >nul
    echo  Desktop liegt in OneDrive: nightcut liegt in C:\nightcut, Verknuepfung auf dem Desktop.
)

rem --- 3. Python-Umgebung -----------------------------------------------
echo.
echo  [3/5] Python-Umgebung einrichten (Python, PyTorch mit CUDA, ca. 5 GB) ...
pushd "%TARGET%"
uv sync || (popd & goto :fail)
popd

rem --- 4. Ollama-Modelle --------------------------------------------------
echo.
echo  [4/5] KI-Modelle fuer Ollama laden (ca. 15 GB) ...
ollama list >nul 2>&1
if errorlevel 1 (
    echo  Starte Ollama ...
    start "" /min ollama serve
    call :waitollama || goto :fail
)
ollama pull %VISION_MODEL% || goto :fail
ollama pull %EDIT_MODEL%   || goto :fail

rem --- 5. Test ----------------------------------------------------------
echo.
echo  [5/5] Kurzer Test ...
pushd "%TARGET%"
uv run nightcut profiles || (popd & goto :fail)
popd

echo.
echo  =============================================
echo   Fertig! nightcut liegt in:
echo   %TARGET%
echo  =============================================
echo.
echo  So startest du deine erste Aufnahme (Eingabeaufforderung im Ordner oben):
echo    uv run nightcut run "D:\Aufnahmen\session.mkv" --profile bf6
echo.
echo  Das Whisper-Modell fuer die Transkription wird beim ersten Lauf geladen.
echo  Anleitung: README.de.md im nightcut-Ordner.
echo.
pause
exit /b 0

rem ===================================================================
:ensure
rem %1 = Befehl, %2 = winget-ID, %3 = Anzeigename
where %1 >nul 2>&1
if not errorlevel 1 (
    echo   - %~3 ist schon installiert.
    exit /b 0
)
echo   - Installiere %~3 ...
winget install --id %2 -e --source winget --accept-package-agreements --accept-source-agreements --silent
if errorlevel 1 (
    call :refreshpath
    where %1 >nul 2>&1 && exit /b 0
    echo  [FEHLER] %~3 konnte nicht installiert werden.
    exit /b 1
)
exit /b 0

:refreshpath
rem Neue PATH-Eintraege der Installer in dieses Fenster uebernehmen.
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
exit /b 0

:waitollama
for /l %%i in (1,1,30) do (
    ollama list >nul 2>&1 && exit /b 0
    timeout /t 2 /nobreak >nul
)
echo  [FEHLER] Ollama startet nicht. Starte Ollama einmal per Hand und fuehre install.bat erneut aus.
exit /b 1

:fail
echo.
echo  Installation abgebrochen. Die Meldung oben zeigt, was schiefging.
echo  install.bat kann einfach noch einmal gestartet werden.
echo.
pause
exit /b 1
