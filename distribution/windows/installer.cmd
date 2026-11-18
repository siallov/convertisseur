@echo off
rem Double-cliquez sur ce fichier pour installer Atelier partitions.
rem "installer.cmd /texte" : installation dans cette fenetre, sans affichage graphique.
if /i "%~1"=="/texte" goto texte
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer-fenetre.ps1"
if errorlevel 1 (
    echo.
    echo La fenetre d'installation n'a pas pu s'ouvrir. Faites une capture de cette fenetre.
    pause
)
exit /b

:texte
echo Installation d'Atelier partitions : demarrage de PowerShell...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer.ps1"
if errorlevel 1 (
    echo.
    echo L'installation s'est arretee. Faites une capture de cette fenetre.
    pause
)
