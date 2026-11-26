@echo off
setlocal
cd /d "%~dp0"

for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "(Get-Content modinfo.json -Raw | ConvertFrom-Json).version"`) do set "VERSION=%%A"
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "(Get-Content modinfo.json -Raw | ConvertFrom-Json).name"`) do set "NAME=%%A"

set "NAME=%NAME: =-%"
set "ZIPNAME=%NAME%-%VERSION%.zip"

if exist "%ZIPNAME%" del "%ZIPNAME%"
7z a -tzip %ZIPNAME% modinfo.json modicon.png assets

del /Q "%AppData%\VintagestoryData\Mods\%ZIPNAME%" 2>nul
copy /Y "%ZIPNAME%" "%AppData%\VintagestoryData\Mods\"