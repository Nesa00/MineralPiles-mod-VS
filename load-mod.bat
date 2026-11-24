@echo off
setlocal
cd /d "%~dp0"

if exist "MineralPiles-1.0.0.zip" del "MineralPiles-1.0.0.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'modinfo.json','modicon.png','assets' -DestinationPath 'MineralPiles-1.0.0.zip' -Force"
copy /Y "MineralPiles-1.0.0.zip" "%AppData%\VintagestoryData\Mods\"