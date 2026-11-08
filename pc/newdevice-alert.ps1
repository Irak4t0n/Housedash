# HouseDash alert - a system-tray app, no console window.
# Polls the house dashboard every 30 s. New MAC on the network -> Leeroy + toast.
# Known device back after >2 h away -> chime (silent 22:00-07:00) + toast.
# Tray menu: status, mute for 1 h, test sounds, open dashboard, quit.
#
# Install (once, no admin): double-click "Install HouseDash alert.cmd" next to this file.
# It drops a tiny launcher into your Startup folder and starts the tray icon now.
param([switch]$Install, [switch]$Uninstall)
$Pi   = 'housedash.local'   # <- your Pi's hostname or IP
$Url  = "http://${Pi}:8088/state.json"
$Dash = "http://${Pi}:8088/"
$Snd  = Join-Path $PSScriptRoot 'sounds\newdevice.mp3'     # new device: Leeroy
$Snd2 = Join-Path $PSScriptRoot 'sounds\returned.mp3'      # returning device: chime
$Seen = Join-Path $env:LOCALAPPDATA 'housedash-seen.txt'
$Startup = [Environment]::GetFolderPath('Startup')
$Launcher = Join-Path $Startup 'HouseDash alert.vbs'

if ($Uninstall) {
    Remove-Item $Launcher -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName 'HouseDash alert' -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "name='powershell.exe'" | Where-Object { $_.CommandLine -like '*newdevice-alert.ps1*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host 'removed'; return
}
if ($Install) {
    # zero-window launcher: wscript runs powershell with window style 0 (nothing flashes)
    $vbs = 'CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File ""' + $PSCommandPath + '""", 0, False'
    Set-Content -Path $Launcher -Value $vbs -Encoding ASCII
    Unregister-ScheduledTask -TaskName 'HouseDash alert' -Confirm:$false -ErrorAction SilentlyContinue   # old approach
    Get-CimInstance Win32_Process -Filter "name='powershell.exe'" | Where-Object { $_.CommandLine -like '*newdevice-alert.ps1*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Process wscript.exe -ArgumentList "`"$Launcher`""
    Write-Host "installed: $Launcher (runs at logon, tray icon is up now)"
    return
}

# ---- tray app ----
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, PresentationCore
[System.Windows.Forms.Application]::EnableVisualStyles()
$player = New-Object System.Windows.Media.MediaPlayer
function Play($file) { $player.Open([uri]$file); $player.Volume = 1.0; $player.Play() }
function Toast($title, $text) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $n = $xml.GetElementsByTagName('text'); $n.Item(0).AppendChild($xml.CreateTextNode($title)) | Out-Null; $n.Item(1).AppendChild($xml.CreateTextNode($text)) | Out-Null
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('House Dashboard').Show([Windows.UI.Notifications.ToastNotification]::new($xml))
    } catch { $tray.ShowBalloonTip(5000, $title, $text, 'Info') }
}
function Icon($color) {
    $b = New-Object System.Drawing.Bitmap 16, 16
    $g = [System.Drawing.Graphics]::FromImage($b); $g.SmoothingMode = 'AntiAlias'
    $g.FillEllipse((New-Object System.Drawing.SolidBrush $color), 1, 1, 14, 14)
    $g.FillRectangle([System.Drawing.Brushes]::White, 5, 5, 6, 6)   # a little "house" window
    $g.Dispose()
    [System.Drawing.Icon]::FromHandle($b.GetHicon())
}
$icoOk = Icon ([System.Drawing.Color]::FromArgb(63, 185, 80)); $icoMute = Icon ([System.Drawing.Color]::Gray); $icoDown = Icon ([System.Drawing.Color]::FromArgb(248, 81, 73))

$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = $icoOk; $tray.Text = 'HouseDash alert - starting'; $tray.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$mStatus = $menu.Items.Add('starting...'); $mStatus.Enabled = $false
$menu.Items.Add('-') | Out-Null
$mMute = $menu.Items.Add('Mute for 1 hour'); $mMute.CheckOnClick = $true
$mOpen = $menu.Items.Add('Open dashboard')
$mTest = $menu.Items.Add('Test: new device (Leeroy)')
$mTest2 = $menu.Items.Add('Test: device returned (chime)')
$menu.Items.Add('-') | Out-Null
$mQuit = $menu.Items.Add('Quit')
$tray.ContextMenuStrip = $menu

$script:muteUntil = [datetime]::MinValue
$mMute.Add_Click({ $script:muteUntil = if ($mMute.Checked) { (Get-Date).AddHours(1) } else { [datetime]::MinValue } })
$mOpen.Add_Click({ Start-Process $Dash })
$tray.Add_DoubleClick({ Start-Process $Dash })
$mTest.Add_Click({ Play $Snd; Toast 'New device on the network' '(test)' })
$mTest2.Add_Click({ Play $Snd2; Toast 'Device is back' '(test)' })
$mQuit.Add_Click({ $tray.Visible = $false; [System.Windows.Forms.Application]::Exit() })

$seen = @{}
if (Test-Path $Seen) { Get-Content $Seen | ForEach-Object { $seen[$_] = 1 } }
$script:first = -not (Test-Path $Seen)
$back = @{}
function Muted { (Get-Date) -lt $script:muteUntil }

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 30000
$timer.Add_Tick({
    if ($mMute.Checked -and -not (Muted)) { $mMute.Checked = $false }   # mute expired
    try {
        $s = Invoke-RestMethod -Uri $Url -TimeoutSec 8
        foreach ($d in @($s.lan.recent_new)) {
            if (-not $d.mac -or $seen.ContainsKey($d.mac)) { continue }
            $seen[$d.mac] = 1; Add-Content $Seen $d.mac
            if (-not $script:first) { if (-not (Muted)) { Play $Snd }; Toast 'New device on the network' "$($d.ip)  $($d.vendor)  $($d.mac)" }
        }
        foreach ($d in @($s.lan.returned)) {
            $k = "$($d.mac)|$($d.ts)"
            if (-not $d.mac -or $back.ContainsKey($k)) { continue }
            $back[$k] = 1
            $h = [math]::Round($d.away / 3600, 1)
            if (-not $script:first) { if (-not $s.lan.quiet -and -not (Muted)) { Play $Snd2 }; Toast "$($d.name) is back" "away $h h  |  $($d.ip)" }
        }
        $script:first = $false
        $q = if ($s.lan.quiet) { ' | quiet hours' } else { '' }
        $mStatus.Text = "$($s.lan.active) devices online | $($s.lan.known) known$q"
        $tray.Text = "HouseDash alert - $($s.lan.active) online"
        $tray.Icon = if (Muted) { $icoMute } else { $icoOk }
    } catch {
        $mStatus.Text = 'dashboard unreachable'; $tray.Text = 'HouseDash alert - Pi unreachable'; $tray.Icon = $icoDown
    }
})
$timer.Start()
$timer.GetType().GetMethod('OnTick', [System.Reflection.BindingFlags]'NonPublic,Instance').Invoke($timer, @([EventArgs]::Empty))  # poll right away
[System.Windows.Forms.Application]::Run()
$tray.Dispose()
