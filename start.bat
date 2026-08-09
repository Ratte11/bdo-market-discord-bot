@echo off
title BDO Market Discord Bot
cd /d "%~dp0"

echo ========================================
echo       BDO MARKET DISCORD BOT
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Erstelle Virtual Environment...
    python -m venv .venv

    if errorlevel 1 (
        echo.
        echo [FEHLER] Python konnte .venv nicht erstellen.
        pause
        exit /b 1
    )
)

echo Installiere Abhaengigkeiten...
".venv\Scripts\python.exe" -m pip install -r requirements.txt

if not exist ".env" (
    copy ".env.example" ".env"
    echo.
    echo .env wurde erstellt.
    echo Trage DISCORD_TOKEN und GUILD_ID ein.
    echo Danach start.bat erneut starten.
    pause
    exit /b 0
)

echo.
echo Starte Bot...
echo.

".venv\Scripts\python.exe" -u bot.py

echo.
echo ========================================
echo BOT WURDE BEENDET
echo Exit Code: %ERRORLEVEL%
echo ========================================
pause
