@echo off
REM ============================================================
REM  Compilation de media_scene_player.py en executable Windows
REM  A lancer depuis le venv (celui ou python-vlc et PyQt5 sont installes)
REM ============================================================

REM --- 1. Adapte ce chemin si VLC est installe ailleurs ---
set VLC_DIR=C:\Program Files\VideoLAN\VLC

if not exist "%VLC_DIR%\libvlc.dll" (
    echo ERREUR : libvlc.dll introuvable dans %VLC_DIR%
    echo Modifie la variable VLC_DIR en haut de ce script avec le bon chemin.
    pause
    exit /b 1
)

REM --- 2. Installer PyInstaller si besoin ---
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installation de PyInstaller...
    pip install pyinstaller
)

REM --- 3. Nettoyage des anciennes compilations ---
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del MediaScenePlayer.spec 2>nul

REM --- 4. Compilation ---
REM --onedir (pas --onefile) : plus fiable pour que VLC retrouve ses plugins.
REM Enleve --windowed le temps des premiers tests pour voir les erreurs
REM dans une console, remets-le une fois que tout fonctionne.
pyinstaller --noconfirm --onedir --windowed --name "MediaScenePlayer" ^
    --add-binary "%VLC_DIR%\libvlc.dll;." ^
    --add-binary "%VLC_DIR%\libvlccore.dll;." ^
    --add-data "%VLC_DIR%\plugins;plugins" ^
    media_scene_player.py

echo.
echo ============================================================
echo Termine. L'executable se trouve dans : dist\MediaScenePlayer\
echo Distribue TOUT le dossier MediaScenePlayer (pas juste le .exe).
echo ============================================================
pause
