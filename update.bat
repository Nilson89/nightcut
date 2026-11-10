@echo off
chcp 65001 >nul
setlocal

rem ===================================================================
rem  nightcut - Update fuer Windows
rem  Holt den neuesten Stand von GitHub, aktualisiert die Python-Umgebung
rem  und laedt fehlende KI-Modelle. Einfach doppelklicken.
rem  Deine eigenen Einstellungen (config\local.yaml, eigene Profile,
rem  Ausgabeordner) bleiben erhalten.
rem ===================================================================

rem Git ersetzt diese Datei waehrend des Updates. Windows liest .bat-Dateien
rem Zeile fuer Zeile von der Platte, deshalb laeuft das Update aus einer Kopie.
if /i not "%~1"=="__run" (
    copy /y "%~f0" "%TEMP%\nightcut-update.bat" >nul || (
        echo  [FEHLER] Konnte keine Kopie in "%TEMP%" anlegen.
        pause
        exit /b 1
    )
    "%TEMP%\nightcut-update.bat" __run "%~dp0."
    exit /b
)

set "DIR=%~f2"
title nightcut Update
cd /d "%DIR%" || goto :fail

echo.
echo  =============================================
echo   nightcut Update
echo  =============================================
echo   Ordner: %DIR%
echo.

call :refreshpath
for %%T in (git uv) do (
    where %%T >nul 2>&1 || (
        echo  [FEHLER] %%T wurde nicht gefunden. Bitte install.bat einmal ausfuehren.
        goto :fail
    )
)
if not exist "%DIR%\.git" (
    echo  [FEHLER] Dieser Ordner ist kein nightcut-Repository.
    echo  update.bat muss im nightcut-Ordner liegen. Fuer eine Neuinstallation install.bat nutzen.
    goto :fail
)

call :version OLD

rem --- 1. Neuen Stand holen -------------------------------------------
echo  [1/4] Neuesten Stand von GitHub holen ...
git fetch --tags --force origin || goto :fail

rem uv.lock kann uv selbst umschreiben, das ist keine Aenderung von dir.
git checkout -- uv.lock >nul 2>&1

set "DIRTY="
for /f "usebackq delims=" %%L in (`git status --porcelain -uno`) do set "DIRTY=1"
if defined DIRTY (
    echo.
    echo  Du hast Dateien von nightcut selbst geaendert:
    git status --short --untracked-files=no
    echo.
    echo  Tipp: Eigene Einstellungen gehoeren nach config\local.yaml, die wird nie ueberschrieben.
    echo  Eigene Spielprofile am besten unter einem neuen Namen in profiles\ anlegen.
    echo.
    choice /c JN /m "  Aenderungen beiseitelegen (git stash) und trotzdem updaten"
    if errorlevel 2 (
        echo  Update abgebrochen, nichts wurde veraendert.
        goto :fail
    )
    git stash push -m "nightcut update.bat %DATE% %TIME%" || goto :fail
    echo  Deine Aenderungen liegen sicher im Stash. Zurueckholen mit: git stash pop
)

set "NEW_COMMITS=0"
for /f "usebackq delims=" %%N in (`git rev-list --count HEAD..@{u}`) do set "NEW_COMMITS=%%N"
if "%NEW_COMMITS%"=="0" (
    echo  nightcut ist schon auf dem neuesten Stand.
) else (
    echo.
    echo  Neu seit deiner Version:
    git log --oneline --no-decorate HEAD..@{u}
    echo.
    rem Von Hand in einen alten Ordner gelegt? Dann blockiert die Datei den Merge.
    git ls-files --error-unmatch update.bat >nul 2>&1 || del /q "%DIR%\update.bat" >nul 2>&1
    git merge --ff-only @{u} || (
        echo  [FEHLER] Dein Stand weicht von GitHub ab ^(eigene Commits?^).
        echo  Bitte von Hand zusammenfuehren, z.B. mit: git pull --rebase
        goto :fail
    )
)

rem --- 2. Python-Umgebung -----------------------------------------------
echo.
echo  [2/4] Python-Umgebung aktualisieren ...
uv sync || goto :fail
git checkout -- uv.lock >nul 2>&1

rem --- 3. KI-Modelle ----------------------------------------------------
echo.
echo  [3/4] KI-Modelle pruefen ...
where ollama >nul 2>&1 || (
    echo  [FEHLER] Ollama wurde nicht gefunden. Bitte install.bat einmal ausfuehren.
    goto :fail
)
ollama list >nul 2>&1
if errorlevel 1 (
    echo  Starte Ollama ...
    start "" /min ollama serve
    call :waitollama || goto :fail
)
set "CFG="
if exist "%DIR%\config\local.yaml" set "CFG=--config config\local.yaml"
for /f "usebackq delims=" %%M in (`uv run --no-sync nightcut models %CFG%`) do (
    ollama show %%M >nul 2>&1 && (
        echo   - %%M ist vorhanden.
    ) || (
        echo   - Lade %%M ...
        ollama pull %%M || goto :fail
    )
)

rem --- 4. Test ----------------------------------------------------------
echo.
echo  [4/4] Kurzer Test ...
uv run --no-sync nightcut profiles >nul || goto :fail
call :version NEW

echo.
echo  =============================================
if "%OLD%"=="%NEW%" (
    echo   Fertig! nightcut %NEW% ist aktuell.
) else (
    echo   Fertig! nightcut %OLD% -^> %NEW%
    echo   Was neu ist, steht in CHANGELOG.md.
)
echo  =============================================
echo.
pause
exit /b 0

rem ===================================================================
:version
rem Version aus pyproject.toml lesen (Zeile: version = "x.y.z").
set "%1=?"
for /f "usebackq tokens=3" %%V in (`findstr /b /c:"version = " pyproject.toml`) do set "%1=%%~V"
exit /b 0

:refreshpath
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
exit /b 0

:waitollama
for /l %%i in (1,1,30) do (
    ollama list >nul 2>&1 && exit /b 0
    timeout /t 2 /nobreak >nul
)
echo  [FEHLER] Ollama startet nicht. Starte Ollama einmal per Hand und fuehre update.bat erneut aus.
exit /b 1

:fail
echo.
echo  Update nicht abgeschlossen. Die Meldung oben zeigt, was schiefging.
echo  update.bat kann einfach noch einmal gestartet werden.
echo.
pause
exit /b 1
