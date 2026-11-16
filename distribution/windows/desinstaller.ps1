#Requires -Version 5.1
# Désinstalle « Atelier partitions » pour l'utilisateur courant.
# Python, Tesseract et Audiveris restent installés : d'autres programmes peuvent s'en servir.
$ErrorActionPreference = 'Continue'
$Dest = Join-Path $env:LOCALAPPDATA 'AtelierPartitions'
$Program = Join-Path $Dest 'programme'
Set-Location $env:TEMP   # ne pas rester dans le dossier que l'on supprime

$venvPython = Join-Path $Program '.venv\Scripts\python.exe'
if (Test-Path $venvPython) { & $venvPython (Join-Path $Program 'lanceur.py') --arreter | Out-Null }
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $folder 'Atelier partitions.lnk')
}
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AtelierPartitions'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Program
Write-Host 'Le programme est désinstallé.'

if ((Test-Path (Join-Path $Dest 'data')) -or (Test-Path (Join-Path $Dest 'private'))) {
    $answer = if ($args -contains '--tout') { 'o' } else { Read-Host "Supprimer aussi vos conversions et réglages ($Dest) ? Cette action est définitive. [o/N]" }
    if ($answer -match '^(o|oui)$') {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Dest
        Write-Host 'Conversions et réglages supprimés.'
    } else {
        Write-Host "Vos conversions et réglages sont conservés dans : $Dest"
    }
}
Write-Host 'Python, Tesseract et Audiveris restent installés ; ils se désinstallent depuis Paramètres > Applications.'
Read-Host 'Appuyez sur Entrée pour fermer cette fenêtre' | Out-Null
