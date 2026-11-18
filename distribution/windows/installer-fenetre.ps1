#Requires -Version 5.1
# Fenêtre d'installation d'« Atelier partitions ».
# L'installation elle-même est faite par installer.ps1, lancé sans console ; il écrit son avancement
# dans un fichier que cette fenêtre relit : chaque étape terminée ajoute une note sur la portée.
param([switch]$SansConsole)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$PowerShell = Join-Path $PSHOME 'powershell.exe'

function Start-Hidden([string]$Arguments) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $PowerShell
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    return [System.Diagnostics.Process]::Start($info)
}

# Lancé depuis installer.cmd : on se relance sans console, et la fenêtre noire se ferme.
if (-not $SansConsole) {
    Start-Hidden ('-NoProfile -ExecutionPolicy Bypass -File "' + $MyInvocation.MyCommand.Path + '" -SansConsole') | Out-Null
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

try {
    $Version = (Get-Content -Raw (Join-Path $Here 'programme\VERSION')).Trim()
    $Etat = Join-Path $env:TEMP 'atelier-partitions-installation.txt'
    $Journal = $Etat + '.journal.txt'
    Remove-Item -Force -ErrorAction SilentlyContinue $Etat, $Journal
    $Program = Join-Path $env:LOCALAPPDATA 'AtelierPartitions\programme'

    # Une note par étape, sur la gamme de do : les « paroles » sous la portée nomment les étapes.
    $Syllabes = @('Python', 'Tesseract', 'Audiveris', 'Langues', 'Copie', 'Modules', 'Icônes', 'Vérif.')
    $State = @{ Step = 0; Title = 'Préparation…'; Info = ''; Results = @(); Error = ''; Done = $false; Shortcuts = ''; Pulse = $false; Lines = 0 }
    $Started = Get-Date

    $Green = [System.Drawing.Color]::FromArgb(61, 92, 61)
    $Paper = [System.Drawing.Color]::FromArgb(250, 248, 242)
    $Ink = [System.Drawing.Color]::FromArgb(40, 40, 40)
    $Faint = [System.Drawing.Color]::FromArgb(214, 210, 196)
    $Live = [System.Drawing.Color]::FromArgb(70, 140, 70)
    $Red = [System.Drawing.Color]::FromArgb(192, 57, 43)
    $Grey = [System.Drawing.Color]::FromArgb(95, 95, 95)

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Installation d''Atelier partitions'
    $form.ClientSize = New-Object System.Drawing.Size(640, 440)
    $form.FormBorderStyle = 'FixedSingle'
    $form.MaximizeBox = $false
    $form.StartPosition = 'CenterScreen'
    $form.BackColor = [System.Drawing.Color]::White
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $iconPath = Join-Path $Here 'programme\static\icone.ico'
    if (Test-Path $iconPath) { $form.Icon = New-Object System.Drawing.Icon($iconPath) }

    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = 'Top'
    $header.Height = 84
    $header.BackColor = $Green
    $form.Controls.Add($header)
    if (Test-Path $iconPath) {
        $picture = New-Object System.Windows.Forms.PictureBox
        $picture.Image = (New-Object System.Drawing.Icon($iconPath, 48, 48)).ToBitmap()
        $picture.SetBounds(20, 18, 48, 48)
        $picture.SizeMode = 'Zoom'
        $header.Controls.Add($picture)
    }
    $title = New-Object System.Windows.Forms.Label
    $title.Text = 'Atelier partitions'
    $title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 18)
    $title.ForeColor = [System.Drawing.Color]::White
    $title.SetBounds(82, 12, 520, 38)
    $header.Controls.Add($title)
    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Text = "Installation de la version $Version  ·  partitions PDF vers MusicXML, paroles"
    $subtitle.ForeColor = [System.Drawing.Color]::FromArgb(230, 238, 223)
    $subtitle.SetBounds(84, 50, 540, 22)
    $header.Controls.Add($subtitle)

    $staff = New-Object System.Windows.Forms.Panel
    $staff.SetBounds(20, 100, 600, 150)
    $staff.BackColor = $Paper
    $staff.GetType().GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'NonPublic,Instance').SetValue($staff, $true, $null)
    $form.Controls.Add($staff)

    $stepLabel = New-Object System.Windows.Forms.Label
    $stepLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 13)
    $stepLabel.ForeColor = $Ink
    $stepLabel.SetBounds(20, 262, 600, 30)
    $form.Controls.Add($stepLabel)
    $infoLabel = New-Object System.Windows.Forms.Label
    $infoLabel.ForeColor = $Grey
    $infoLabel.SetBounds(20, 294, 600, 90)
    $form.Controls.Add($infoLabel)

    $rule = New-Object System.Windows.Forms.Label
    $rule.BorderStyle = 'Fixed3D'
    $rule.SetBounds(0, 388, 640, 2)
    $form.Controls.Add($rule)
    $details = New-Object System.Windows.Forms.Button
    $details.Text = 'Voir le détail'
    $details.SetBounds(20, 398, 130, 32)
    $form.Controls.Add($details)
    $clock = New-Object System.Windows.Forms.Label
    $clock.ForeColor = $Grey
    $clock.SetBounds(160, 405, 220, 22)
    $form.Controls.Add($clock)
    $secondary = New-Object System.Windows.Forms.Button
    $secondary.Text = 'Fermer'
    $secondary.SetBounds(390, 398, 90, 32)
    $secondary.Visible = $false
    $form.Controls.Add($secondary)
    $primary = New-Object System.Windows.Forms.Button
    $primary.Text = 'Annuler'
    $primary.SetBounds(490, 398, 130, 32)
    $form.Controls.Add($primary)

    # Portée : 5 lignes espacées de 14 px ; la note k (0 = do grave) est à 7 px au-dessus de la précédente.
    $staff.Add_Paint({
        param($sender, $e)
        $g = $e.Graphics
        $g.SmoothingMode = 'AntiAlias'
        $g.TextRenderingHint = 'AntiAlias'
        $top = 38; $gap = 14
        $linePen = New-Object System.Drawing.Pen($Grey, 1.2)
        for ($i = 0; $i -lt 5; $i++) { $g.DrawLine($linePen, 14, $top + $i * $gap, 586, $top + $i * $gap) }
        $g.DrawLine($linePen, 14, $top, 14, $top + 4 * $gap)
        $clefFont = New-Object System.Drawing.Font('Segoe UI Symbol', 46)
        $g.DrawString([char]::ConvertFromUtf32(0x1D11E), $clefFont, (New-Object System.Drawing.SolidBrush($Ink)), 14, $top - 30)
        $lyricFont = New-Object System.Drawing.Font('Segoe UI', 8.5)
        $bottom = $top + 4 * $gap
        for ($k = 0; $k -lt 8; $k++) {
            $x = 118 + $k * 60
            $y = $bottom + 2 * 7 - $k * 7
            $color = $Faint
            if ($k + 1 -lt $State.Step -or $State.Done) { $color = $Ink }
            elseif ($k + 1 -eq $State.Step) {
                if ($State.Error) { $color = $Red } elseif ($State.Pulse) { $color = $Live } else { $color = $Green }
            }
            $pen = New-Object System.Drawing.Pen($color, 1.6)
            if ($k -eq 0) { $g.DrawLine($pen, $x - 13, $y, $x + 13, $y) }   # ligne supplémentaire du do
            $saved = $g.Save()
            $g.TranslateTransform($x, $y)
            $g.RotateTransform(-20)
            if ($color -eq $Faint) { $g.DrawEllipse($pen, -8, -5.5, 16, 11) }
            else { $g.FillEllipse((New-Object System.Drawing.SolidBrush($color)), -8, -5.5, 16, 11) }
            $g.Restore($saved)
            if ($k -lt 6) { $g.DrawLine($pen, $x + 7.2, $y - 2, $x + 7.2, $y - 44) } else { $g.DrawLine($pen, $x - 7.2, $y + 2, $x - 7.2, $y + 44) }
            $text = $Syllabes[$k]
            $size = $g.MeasureString($text, $lyricFont)
            $lyricColor = if ($color -eq $Faint) { $Faint } else { $Grey }
            $g.DrawString($text, $lyricFont, (New-Object System.Drawing.SolidBrush($lyricColor)), $x - $size.Width / 2, 128)
        }
        if ($State.Done) {   # double barre finale
            $g.DrawLine((New-Object System.Drawing.Pen($Ink, 1.2)), 578, $top, 578, $bottom)
            $g.DrawLine((New-Object System.Drawing.Pen($Ink, 4)), 584, $top, 584, $bottom)
        }
    })

    function Read-State {
        if (-not (Test-Path $Etat)) { return }
        try {
            $stream = New-Object System.IO.FileStream($Etat, 'Open', 'Read', 'ReadWrite')
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $lines = $reader.ReadToEnd() -split "`r?`n"
            $reader.Close()
        } catch { return }
        foreach ($line in $lines) {
            $parts = $line -split '\|', 3
            switch ($parts[0]) {
                'etape' { $State.Step = [int]$parts[1]; $State.Title = $parts[2]; $State.Info = '' }
                'info' { $State.Info = ($parts[1..2] -join '|').TrimEnd('|') }
                'erreur' { $State.Error = ($parts[1..2] -join '|').TrimEnd('|') }
                'resultat' { if (-not ($State.Results -contains $line)) { $State.Results += $line } }
                'fin' { $State.Done = $true; $State.Shortcuts = $parts[1] }
            }
        }
    }

    function Show-Journal {
        if (-not (Test-Path $Journal)) { return }
        $copy = Join-Path $env:TEMP 'atelier-partitions-detail.txt'
        try {
            $stream = New-Object System.IO.FileStream($Journal, 'Open', 'Read', 'ReadWrite')
            $reader = New-Object System.IO.StreamReader($stream)
            [System.IO.File]::WriteAllText($copy, $reader.ReadToEnd(), [System.Text.Encoding]::UTF8)
            $reader.Close()
            Start-Process notepad.exe $copy
        } catch {}
    }

    $script:Finished = $false
    function Update-View {
        Read-State
        $State.Pulse = -not $State.Pulse
        $minutes = [int][Math]::Floor(((Get-Date) - $Started).TotalMinutes)
        if (-not $script:Finished) { $clock.Text = "Temps écoulé : $minutes min" }
        if (-not $script:Finished -and $process.HasExited -and -not $State.Done -and -not $State.Error) {
            Start-Sleep -Milliseconds 300; Read-State
            if (-not $State.Done -and -not $State.Error) { $State.Error = 'L''installation s''est arrêtée sans message.' }
        }
        if ($State.Error -and -not $script:Finished) {
            $script:Finished = $true
            $stepLabel.ForeColor = $Red
            $stepLabel.Text = 'L''installation n''a pas abouti'
            $infoLabel.ForeColor = $Ink
            $infoLabel.Text = $State.Error + "`r`n`r`nCliquez sur « Voir le détail » et envoyez une capture de ce texte."
            $primary.Text = 'Fermer'
        } elseif ($State.Done -and -not $script:Finished) {
            $script:Finished = $true
            $stepLabel.ForeColor = $Green
            $stepLabel.Text = 'Atelier partitions est installé'
            $lines = foreach ($result in $State.Results) {
                $parts = $result -split '\|', 3
                $(if ($parts[1] -eq 'ok') { [string][char]0x2714 + '  ' } else { [string][char]0x26A0 + '  ' }) + $parts[2] + $(if ($parts[1] -ne 'ok') { ' : non disponible' } else { '' })
            }
            $where = if ($State.Shortcuts) { "Icône créée : $($State.Shortcuts)." } else { "Ouvrez-le avec « Atelier partitions.cmd » dans $Program." }
            $infoLabel.ForeColor = $Ink
            $infoLabel.Text = (@($lines) -join "`r`n") + "`r`n" + $where
            $primary.Text = 'Ouvrir l''atelier'
            $secondary.Visible = $true
            $clock.Text = "Installé en $minutes min"
        } elseif (-not $script:Finished) {
            $stepLabel.Text = $(if ($State.Step -gt 0) { "Étape $($State.Step) sur 8 : $($State.Title)" } else { $State.Title })
            $infoLabel.Text = $State.Info
            if ($State.Info -like '*autorisation*') {
                $infoLabel.Text += "`r`nSi la demande n'apparaît pas, cliquez sur l'icône qui clignote dans la barre des tâches."
            }
        }
        $staff.Invalidate()
    }

    $installer = Join-Path $Here 'installer.ps1'
    $process = Start-Hidden ('-NoProfile -ExecutionPolicy Bypass -File "' + $installer + '" -Etat "' + $Etat + '"')

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 500
    $timer.Add_Tick({ Update-View })
    $timer.Start()

    $details.Add_Click({ Show-Journal })
    $secondary.Add_Click({ $form.Close() })
    $primary.Add_Click({
        if ($State.Done) {
            $pythonw = Join-Path $Program '.venv\Scripts\pythonw.exe'
            Start-Process -FilePath $pythonw -ArgumentList ('"' + (Join-Path $Program 'lanceur.py') + '"') -WorkingDirectory $Program
        }
        $form.Close()
    })
    $form.Add_FormClosing({
        param($sender, $e)
        if (-not $script:Finished -and -not $process.HasExited) {
            $answer = [System.Windows.Forms.MessageBox]::Show('Arrêter l''installation en cours ?', 'Atelier partitions', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
            if ($answer -ne 'Yes') { $e.Cancel = $true; return }
            & taskkill.exe /PID $process.Id /T /F | Out-Null
        }
        $timer.Stop()
    })
    Update-View
    [System.Windows.Forms.Application]::Run($form)
} catch {
    [System.Windows.Forms.MessageBox]::Show("La fenêtre d'installation n'a pas pu s'afficher :`r`n$($_.Exception.Message)`r`n`r`nPour installer sans elle : ouvrez une invite de commandes dans ce dossier et tapez  installer.cmd /texte", 'Atelier partitions', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
}
