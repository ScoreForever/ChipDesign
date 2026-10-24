param(
    [ValidateSet('Check', 'OpenOcd', 'Gdb', 'UsbList', 'Attach', 'Build')]
    [string]$Action = 'Check',
    [ValidateSet('nocpu', 'cpu')][string]$Target = 'cpu',
    [string]$Distro = 'Ubuntu',
    [string]$BusId = '',
    [string]$VivadoRoot = $env:XILINX_VIVADO
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (!$VivadoRoot) {
    $command = Get-Command vivado.bat -ErrorAction SilentlyContinue
    if ($command) { $VivadoRoot = Split-Path (Split-Path $command.Source -Parent) -Parent }
}
if ($VivadoRoot) { $env:XILINX_VIVADO = $VivadoRoot }

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
function Get-Usbipd {
    $command = Get-Command usbipd.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $installed = Join-Path $env:ProgramFiles 'usbipd-win/usbipd.exe'
    if (Test-Path -LiteralPath $installed) { return $installed }
    throw 'usbipd-win is missing. Install dorssel.usbipd-win via winget.'
}
function Invoke-Linux {
    param([string]$Command)
    $converted = & wsl.exe -d $Distro --exec wslpath -a -u $repo
    if ($LASTEXITCODE -ne 0 -or !$converted) { throw 'wslpath failed' }
    $linuxRepo = ($converted | Select-Object -Last 1).Trim()
    if (!$linuxRepo.StartsWith('/')) { throw 'wslpath returned an invalid path' }
    Invoke-Checked -Program wsl.exe -Arguments @('-d', $Distro, '--exec', 'bash', "$linuxRepo/scripts/lab6/run.sh", $Command, $Target)
}
switch ($Action) {
    Check {
        if (!$VivadoRoot) { throw 'Vivado not found on PATH; pass -VivadoRoot.' }
        # Vivado 2025.2 on this machine returns 1 for a successful -version.
        $version = & (Join-Path $VivadoRoot 'bin/vivado.bat') -version
        $version | Write-Output
        if (($version -join "`n") -notmatch '(?m)^vivado v\d+\.') { throw 'Vivado version query failed' }
        Invoke-Checked -Program vsim.exe -Arguments @('-version')
        Invoke-Checked -Program (Get-Usbipd) -Arguments @('--version')
        Invoke-Linux check
    }
    UsbList { Invoke-Checked -Program (Get-Usbipd) -Arguments @('list') }
    Attach {
        if ($BusId -notmatch '^\d+-\d+$') { throw 'Pass the FT232H BUSID from UsbList using -BusId.' }
        $usbipd = Get-Usbipd
        $devices = (& $usbipd state | ConvertFrom-Json).Devices
        if ($LASTEXITCODE -ne 0) { throw 'usbipd state failed' }
        $device = @($devices | Where-Object { $_.BusId -eq $BusId })
        if ($device.Count -ne 1 -or $device[0].InstanceId -notmatch 'VID_0403&PID_6014') {
            throw 'BUSID must identify the external FT232H (0403:6014).'
        }
        # bind requires an Administrator PowerShell; attach does not.
        if (!$device[0].PersistedGuid) {
            Invoke-Checked -Program $usbipd -Arguments @('bind', '--busid', $BusId)
        }
        Invoke-Checked -Program wsl.exe -Arguments @('-d', $Distro, '--exec', 'true')
        # Ensure non-systemd WSL also applies the FT232H plugdev permission rule.
        Invoke-Checked -Program wsl.exe -Arguments @('-d', $Distro, '-u', 'root', '--exec', 'bash', '-c', 'udevadm control --reload-rules 2>/dev/null || /usr/lib/systemd/systemd-udevd --daemon')
        Invoke-Checked -Program $usbipd -Arguments @('attach', '--wsl', $Distro, '--busid', $BusId)
        # usbipd can return before the kernel finishes USB enumeration.
        $enumerated = $false
        for ($attempt = 0; $attempt -lt 15; $attempt++) {
            $usbInfo = & wsl.exe -d $Distro --exec lsusb -d '0403:6014'
            if ($LASTEXITCODE -eq 0) {
                $usbInfo | Write-Output
                $enumerated = $true
                break
            }
            Start-Sleep -Milliseconds 300
        }
        if (!$enumerated) { throw 'FT232H attach completed, but WSL did not enumerate it; check usbipd list.' }
    }
    OpenOcd { Invoke-Linux openocd }
    Gdb { Invoke-Linux gdb }
    Build {
        if (!$VivadoRoot) { throw 'Vivado not found; pass -VivadoRoot.' }
        & (Join-Path $repo 'hardware/fpga/scripts/build_ego1.ps1') -VivadoRoot $VivadoRoot
        exit $LASTEXITCODE
    }
}
