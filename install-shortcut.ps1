<#
.SYNOPSIS
  Creates (or refreshes) the one Start menu shortcut, "App Launcher".

.DESCRIPTION
  Target:      pythonw.exe (so no console window appears)
  Argument:    launcher.pyw in this folder
  Start in:    this folder
  Icon:        launcher.ico in this folder
  Also stamps the shortcut with the launcher's AppUserModelID so the pinned icon and the
  running window share one taskbar button.

  After running it: press Start, find "App Launcher", right-click > Pin to taskbar.
  Re-run this script if Python is ever upgraded or the folder is moved.

  -Remove   deletes the shortcut instead.
  -Folder   create it in this folder instead of the Start menu Programs folder (used by the tests).
#>
param([switch]$Remove, [string]$Folder)

$ErrorActionPreference = 'Stop'
$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$programs = if ($Folder) { $Folder } else { [Environment]::GetFolderPath('Programs') }
$lnkPath  = Join-Path $programs 'App Launcher.lnk'

if ($Remove) {
    if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force; "Removed $lnkPath" } else { "No shortcut at $lnkPath" }
    return
}

# The real pythonw.exe of the Python that `python` resolves to (not the WindowsApps alias,
# which does not work reliably as a shortcut target).
$prefix = (& python -c "import sys; print(sys.base_prefix)").Trim()
$pythonw = Join-Path $prefix 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found at $pythonw" }

$launcher = Join-Path $here 'launcher.pyw'
$icon     = Join-Path $here 'launcher.ico'
foreach ($f in @($launcher, $icon)) { if (-not (Test-Path $f)) { throw "Missing $f" } }

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath       = $pythonw
$lnk.Arguments        = '"' + $launcher + '"'
$lnk.WorkingDirectory = $here
$lnk.IconLocation     = "$icon,0"
$lnk.Description      = 'One-click launcher for my tools'
$lnk.WindowStyle      = 1
$lnk.Save()

Push-Location $here
try { & python -m applauncher.aumid set $lnkPath } finally { Pop-Location }

"Created $lnkPath"
"  target : $pythonw"
"  args   : $($lnk.Arguments)"
"  start  : $here"
"  icon   : $icon"
"Now: Start > App Launcher > right-click > Pin to taskbar."
