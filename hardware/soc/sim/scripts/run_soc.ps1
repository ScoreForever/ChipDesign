$ErrorActionPreference = 'Stop'

# Run the integrated ChipDesign SoC regression with ModelSim.
# Usage (from ChipDesign root):
#   powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
#
# Environment variables:
#   SSH_HOST       - if set, copy the tree to the remote host and run there
#   REMOTE_WORKDIR - remote working directory (default: ~/chipdesign_soc_run)

$socDir      = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rootDir     = (Resolve-Path (Join-Path $socDir '..\..\..')).Path
$filelist    = Join-Path $socDir 'filelists\chipdesign_soc.f'
$testbench   = 'chipdesign_soc_tb'
$vcdDir      = Join-Path $socDir 'out'

function Run-Local {
    Set-Location $rootDir

    # Create ModelSim work library
    $workDir = Join-Path $rootDir 'work'
    if (Test-Path $workDir) {
        Remove-Item -LiteralPath $workDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $workDir | Out-Null
    & vlib work
    if ($LASTEXITCODE -ne 0) { throw 'vlib failed' }

    # Compile
    Write-Output "Compiling with vlog -sv -f $filelist ..."
    & vlog -sv -f $filelist
    if ($LASTEXITCODE -ne 0) { throw 'vlog compile failed' }

    # Simulate
    Write-Output "Running simulation: vsim -c $testbench -do 'run -all; exit'"
    $simLines = & vsim -c $testbench -do "run -all; exit" 2>&1
    $simExit = $LASTEXITCODE
    $simOutput = $simLines -join [Environment]::NewLine
    Write-Output $simOutput
    if ($simExit -ne 0 -or
        $simOutput -match '(?m)^# .*\b(?:FAIL|TIMEOUT)\b' -or
        $simOutput -match '(?m)^# Errors: [1-9]' -or
        $simOutput -notmatch '(?m)^# .*PASS: integrated SoC test OK') {
        throw 'vsim simulation failed or did not report the expected PASS marker'
    }
}

function Run-Remote {
    param([string]$HostName, [string]$RemoteDir = '~/chipdesign_soc_run')

    $sshHost = $HostName
    $remoteFilelist = ($filelist -replace [regex]::Escape($rootDir), $RemoteDir).Replace('\', '/')

    Write-Output "Deploying to ${sshHost}:${RemoteDir} ..."
    # Use rsync if available, otherwise scp the whole directory
    $rsync = Get-Command rsync -ErrorAction SilentlyContinue
    if ($rsync) {
        & rsync -az --delete "$($rootDir)/" "${sshHost}:${RemoteDir}/"
    } else {
        # Fallback: tar + ssh
        $tar = (Get-Command tar).Source
        & $tar -czf "$env:TEMP\chipdesign_soc_run.tar.gz" -C $rootDir .
        & scp "$env:TEMP\chipdesign_soc_run.tar.gz" "${sshHost}:${RemoteDir}.tar.gz"
        & ssh $sshHost "mkdir -p $RemoteDir && tar -xzf ${RemoteDir}.tar.gz -C $RemoteDir && rm ${RemoteDir}.tar.gz"
    }
    if ($LASTEXITCODE -ne 0) { throw 'Remote deployment failed' }

    $remoteCmd = @(
        "cd $RemoteDir",
        'rm -rf work && vlib work',
        "vlog -sv -f $remoteFilelist",
        "vsim -c $testbench -do 'run -all; exit'"
    ) -join ' && '

    Write-Output "Running on ${sshHost} ..."
    $remoteLines = & ssh $sshHost $remoteCmd 2>&1
    $remoteExit = $LASTEXITCODE
    $remoteOutput = $remoteLines -join [Environment]::NewLine
    Write-Output $remoteOutput
    if ($remoteExit -ne 0 -or
        $remoteOutput -match '(?m)^# .*\b(?:FAIL|TIMEOUT)\b' -or
        $remoteOutput -match '(?m)^# Errors: [1-9]' -or
        $remoteOutput -notmatch '(?m)^# .*PASS: integrated SoC test OK') {
        throw 'Remote simulation failed or did not report the expected PASS marker'
    }
}

try {
    if ($env:SSH_HOST) {
        Run-Remote -HostName $env:SSH_HOST -RemoteDir ($env:REMOTE_WORKDIR, '~/chipdesign_soc_run' -ne $null | Select-Object -First 1)
    } else {
        Run-Local
    }
    Write-Output "PASS integrated SoC regression"
} catch {
    Write-Error "FAIL integrated SoC regression: $_"
    exit 1
}
