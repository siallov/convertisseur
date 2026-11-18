#Requires -Version 5.1
# Installe « Atelier partitions » pour l'utilisateur courant (Windows 10 ou 11, 64 bits).
#
# Internet est nécessaire pendant l'installation (environ 300 Mo) ; ensuite tout fonctionne hors ligne.
# Windows demande une autorisation (contrôle de compte) seulement pour installer les logiciels manquants :
# Tesseract (lecture du texte) et Audiveris (lecture des notes). Python s'installe pour l'utilisateur seul.
# Les erreurs sont vérifiées une à une : sous Windows PowerShell 5.1, « Stop » ferait échouer
# l'installation dès qu'un programme externe (pip, winget) écrit un simple avertissement.
#
# -Etat <fichier> : utilisé par installer-fenetre.ps1. L'avancement y est écrit ligne par ligne
# (etape|n|titre, info|texte, resultat|ok|libellé, erreur|texte, fin|raccourcis) et rien n'est demandé au clavier.
param([string]$Etat = '')
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'   # sinon les téléchargements sont très lents
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$AudiverisVersion = '5.11.0'
$AudiverisMsi = 'Audiveris-5.11.0-windows-x86_64.msi'
$AudiverisSha256 = 'ac221b0d39a90e32b7f43dbf9f5d5a45ff8b5424076af21b65278955688dac71'
$AudiverisUrl = "https://github.com/Audiveris/audiveris/releases/download/$AudiverisVersion/$AudiverisMsi"
$TessdataBase = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata/4.1.0'
$Languages = [ordered]@{
    eng = 'daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047'
    fra = 'eac01c1d72540d6090facb7b2f42dd0a2ee8fc57c5be1b20548ae668e2761913'
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $Here 'programme'
$Dest = Join-Path $env:LOCALAPPDATA 'AtelierPartitions'
$Program = Join-Path $Dest 'programme'
$AudiverisTessdata = Join-Path $env:APPDATA 'AudiverisLtd\audiveris\tessdata'
$Port = if ($env:ATELIER_PORT) { $env:ATELIER_PORT } else { '8765' }

function Report([string]$Line) {
    if (-not $Etat) { return }
    for ($try = 0; $try -lt 20; $try++) {
        try { [IO.File]::AppendAllText($Etat, $Line + "`r`n", [Text.Encoding]::UTF8); return } catch { Start-Sleep -Milliseconds 50 }
    }
}
function Say([string]$Text) { Write-Host ''; Write-Host $Text -ForegroundColor Cyan }
function Step([int]$Number, [string]$Title) { Say "[$Number/8] $Title"; Report "etape|$Number|$Title" }
function Info([string]$Text) { Say $Text; Report "info|$Text" }
function Fail([string]$Text) {
    Write-Host ''
    Write-Host "Erreur : $Text" -ForegroundColor Red
    Report "erreur|$Text"
    if (-not $Etat) { Read-Host 'Appuyez sur Entrée pour fermer cette fenêtre' | Out-Null }
    exit 1
}
function Get-Verified([string]$Url, [string]$File, [string]$Sha256) {
    Invoke-WebRequest -Uri $Url -OutFile $File -UseBasicParsing -ErrorAction Stop
    $actual = (Get-FileHash -Path $File -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) {
        Remove-Item -Force $File
        Fail "le fichier téléchargé ($Url) ne correspond pas à l'original publié."
    }
}
function Install-WithWinget([string]$Id, [string]$Scope) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    $arguments = @('install', '--exact', '--id', $Id, '--source', 'winget', '--silent', '--accept-package-agreements', '--accept-source-agreements')
    if ($Scope) { $arguments += @('--scope', $Scope) }
    & winget @arguments | Out-Host
    return ($LASTEXITCODE -eq 0)
}
function Find-Python {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = & py -3 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $candidates.Add($found.Trim()) }
    }
    foreach ($pattern in @("$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe")) {
        Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $candidates.Add($_.FullName) }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\WindowsApps\*') { $candidates.Add($command.Source) }  # pas le raccourci du Store
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    return $null
}
function Find-Program([string]$Folder, [string]$Exe) {
    foreach ($base in @($env:ProgramFiles, $env:ProgramW6432, ${env:ProgramFiles(x86)}, "$env:LOCALAPPDATA\Programs")) {
        if ($base) {
            $path = Join-Path $base "$Folder\$Exe"
            if (Test-Path $path) { return $path }
        }
    }
    return $null
}

if (-not (Test-Path (Join-Path $Source 'app.py'))) { Fail 'dossier « programme » introuvable à côté de ce script.' }
if (-not [Environment]::Is64BitOperatingSystem) { Fail 'Audiveris ne fonctionne que sur un Windows 64 bits.' }

# 1. Python ------------------------------------------------------------------------
if ($Etat) { Start-Transcript -Path ($Etat + '.journal.txt') -Force | Out-Null }
Step 1 'Python'
$Python = Find-Python
if (-not $Python) {
    Info 'Téléchargement et installation de Python 3.12, pour votre compte uniquement…'
    Install-WithWinget 'Python.Python.3.12' 'user' | Out-Null
    $Python = Find-Python
    if (-not $Python) {
        Fail "Python n'a pas pu être installé automatiquement. Installez Python 3.12 depuis https://www.python.org/downloads/windows/ (cochez « Add python.exe to PATH »), puis relancez cet installateur."
    }
}
Say "Python : $Python"
Report 'info|Python est prêt.'

# 2. Tesseract (texte des partitions scannées dans l'onglet Paroles) --------------------
Step 2 'Tesseract (lecture du texte des scans)'
if (-not ((Get-Command tesseract -ErrorAction SilentlyContinue) -or (Find-Program 'Tesseract-OCR' 'tesseract.exe'))) {
    Info 'Installation de Tesseract. Windows va demander une autorisation.'
    if (-not (Install-WithWinget 'UB-Mannheim.TesseractOCR' '')) {
        Write-Host "Tesseract n'a pas pu être installé : le texte des partitions scannées ne sera pas lu (le reste fonctionne)." -ForegroundColor Yellow
        Report "info|Tesseract n'a pas pu être installé : on continue sans lui."

    }
} else {
    Info 'Tesseract est déjà installé.'
}

# 3. Audiveris -----------------------------------------------------------------------
Step 3 'Audiveris (lecture des notes)'
if (Find-Program 'Audiveris' 'Audiveris.exe') {
    Info 'Audiveris est déjà installé.'
} else {
    Info "Téléchargement d'Audiveris $AudiverisVersion (environ 85 Mo)…"
    $msi = Join-Path $env:TEMP $AudiverisMsi
    try { Get-Verified $AudiverisUrl $msi $AudiverisSha256 } catch { Fail "téléchargement d'Audiveris impossible. Vérifiez la connexion internet. ($_)" }
    Info "Installation d'Audiveris. Windows va demander une autorisation."
    $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', "`"$msi`"", '/passive', '/norestart') -Wait -PassThru
    Remove-Item -Force $msi -ErrorAction SilentlyContinue
    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) { Fail "l'installation d'Audiveris a échoué (code $($process.ExitCode))." }
    if (-not (Find-Program 'Audiveris' 'Audiveris.exe')) { Fail "Audiveris reste introuvable après son installation." }
}

# 4. Français et anglais pour qu'Audiveris lise les paroles ---------------------------
Step 4 'Langues française et anglaise'
New-Item -ItemType Directory -Force -Path $AudiverisTessdata | Out-Null
foreach ($language in $Languages.Keys) {
    $target = Join-Path $AudiverisTessdata "$language.traineddata"
    if ((Test-Path $target) -and ((Get-FileHash $target -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Languages[$language])) { continue }
    Info "Téléchargement de la langue « $language » pour la lecture des paroles…"
    $part = "$target.part"
    try { Get-Verified "$TessdataBase/$language.traineddata" $part $Languages[$language] } catch { Fail "téléchargement de la langue $language impossible. ($_)" }
    Move-Item -Force $part $target -ErrorAction Stop
}

# 5. Programme et bibliothèques Python ----------------------------------------------
Step 5 'Copie du programme'
Info "Dossier : $Program"
$venvPython = Join-Path $Program '.venv\Scripts\python.exe'
if (Test-Path $venvPython) { & $venvPython (Join-Path $Program 'lanceur.py') --arreter 2>$null | Out-Null }
try {
    if (Test-Path $Program) { Remove-Item -Recurse -Force $Program -ErrorAction Stop }
    New-Item -ItemType Directory -Force -Path $Dest -ErrorAction Stop | Out-Null
    Copy-Item -Recurse -Path $Source -Destination $Program -ErrorAction Stop
} catch { Fail "copie du programme impossible : $_" }
& $Python -m venv (Join-Path $Program '.venv') 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "création de l'environnement Python impossible." }
Step 6 'Bibliothèques Python'
Info 'Téléchargement de PyMuPDF, numpy, SciPy, Pillow et Verovio : cela peut prendre quelques minutes…'
& $venvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $Program 'requirements-paroles.txt') 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { Fail 'installation des bibliothèques Python impossible. Vérifiez la connexion internet.' }

# 6. Raccourcis (Bureau et menu Démarrer) et désinstallation -------------------------
$pythonw = Join-Path $Program '.venv\Scripts\pythonw.exe'
Step 7 'Raccourcis du Bureau et du menu Démarrer'
$shell = New-Object -ComObject WScript.Shell
# Bureau : celui de Windows (éventuellement déplacé dans OneDrive), sinon le dossier Bureau du profil.
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }
$shortcuts = [ordered]@{ 'Bureau' = $desktop; 'menu Démarrer' = [Environment]::GetFolderPath('Programs') }
$Created = @()
foreach ($place in $shortcuts.Keys) {
    $folder = $shortcuts[$place]
    $path = Join-Path $folder 'Atelier partitions.lnk'
    try {
        New-Item -ItemType Directory -Force -Path $folder -ErrorAction Stop | Out-Null
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $pythonw
        $link.Arguments = '"' + (Join-Path $Program 'lanceur.py') + '"'
        $link.WorkingDirectory = $Program
        $link.IconLocation = (Join-Path $Program 'static\icone.ico')
        $link.Description = 'Convertir des partitions PDF en MusicXML et extraire les paroles'
        $link.Save()
    } catch { Write-Host "  $($_.Exception.Message)" -ForegroundColor Yellow }
    if (Test-Path -LiteralPath $path) {
        Write-Host "  Raccourci ($place) : $path"
        $Created += $place
    } else {
        Write-Host "  Raccourci ($place) NON CRÉÉ dans : $folder" -ForegroundColor Yellow
    }
}
$version = (Get-Content -Raw (Join-Path $Program 'VERSION')).Trim()
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AtelierPartitions'
New-Item -Force -Path $key | Out-Null
$uninstall = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Program 'desinstaller.ps1') + '"'
$values = @{ DisplayName = 'Atelier partitions'; DisplayVersion = $version; Publisher = 'Atelier partitions'
             InstallLocation = $Program; DisplayIcon = (Join-Path $Program 'static\icone.ico'); UninstallString = $uninstall }
foreach ($name in $values.Keys) { Set-ItemProperty -Path $key -Name $name -Value $values[$name] }
Set-ItemProperty -Path $key -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord

# 7. Vérification -------------------------------------------------------------------
Step 8 'Vérification'
$env:ATELIER_PORT = $Port
& $venvPython (Join-Path $Program 'lanceur.py') --sans-navigateur
if ($LASTEXITCODE -ne 0) { Fail "l'application ne démarre pas. Détails dans $Dest\atelier.log" }
$base = "http://127.0.0.1:$Port"
try {
    $health = Invoke-RestMethod "$base/api/health" -ErrorAction Stop
    $lyrics = Invoke-RestMethod "$base/api/paroles/statut" -ErrorAction Stop
} catch { Fail "l'application ne répond pas. Détails dans $Dest\atelier.log" }
$checks = [ordered]@{
    'Lecture des notes (Audiveris)' = [bool]$health.engine
    'Extraction des paroles' = [bool]$lyrics.available
    'Texte des scans (Tesseract)' = [bool]$lyrics.french
    'Paroles lues par Audiveris (français)' = [bool](Test-Path (Join-Path $AudiverisTessdata 'fra.traineddata'))
}
foreach ($label in $checks.Keys) {
    Write-Host ('  ' + $label.PadRight(40) + ': ' + $(if ($checks[$label]) { 'prêt' } else { 'NON' }))
    Report ('resultat|' + $(if ($checks[$label]) { 'ok' } else { 'non' }) + '|' + $label)
}

Say 'Atelier partitions est installé.'
if ($Created) {
    Write-Host ("  • Ouvrez-le avec l'icône « Atelier partitions » (" + ($Created -join ', ') + ').')
} else {
    Write-Host "  • Ouvrez-le en double-cliquant sur : $Program\Atelier partitions.cmd"
}
Write-Host "  • Vos conversions sont rangées dans : $Dest\data"
Write-Host '  • Pour désinstaller : Paramètres > Applications > Atelier partitions.'
Report ('fin|' + ($Created -join ', '))
if ($Etat) { Stop-Transcript | Out-Null } else { Read-Host 'Appuyez sur Entrée pour fermer cette fenêtre' | Out-Null }
