# Copy the current hardware tree to a fresh remote run and retrieve simulation logs.
[CmdletBinding()]
param(
    [string]$HostName = $env:SSH_HOST,
    [string]$RemoteDir = '',
    [ValidateSet('xcelium', 'vcs')][string]$Simulator = 'xcelium',
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 120,
    [ValidateRange(1, 86400)][int]$CompileTimeoutSeconds = 600
)
$ErrorActionPreference = 'Stop'
$rootDir = (Resolve-Path (Join-Path $PSScriptRoot '../../../..')).Path
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$runName = "vcs_structure_$stamp"
$runRelative = "build/$runName"
$runDir = Join-Path $rootDir $runRelative
New-Item -ItemType Directory -Path $runDir | Out-Null
$consoleLog = Join-Path $runDir 'remote_console.log'

function Invoke-Native {
    param([string]$Program, [string[]]$Arguments, [string]$Stage, [string]$InputScript = '')
    # Bypass PowerShell 5.1's native argument marshalling and stderr ErrorRecords.
    # All arguments are simple tokens; shell code travels only through stdin.
    foreach ($argument in $Arguments) {
        if ($argument -notmatch '^[a-zA-Z0-9_@+:=./-]+$') { throw "Unsafe native argument: $argument" }
    }
    $executable = (Get-Command $Program -CommandType Application -ErrorAction Stop |
        Select-Object -First 1).Source
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $executable
    $info.Arguments = $Arguments -join ' '
    # Support command wrappers as well as executables (useful on Windows).
    if ([IO.Path]::GetExtension($executable) -in @('.cmd', '.bat')) {
        $info.FileName = $env:ComSpec
        $info.Arguments = '/d /s /c ""' + $executable + '" ' + ($Arguments -join ' ') + '"'
    }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $rootDir
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        # Drain both streams concurrently to avoid pipe-buffer deadlocks.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if ($InputScript) {
            $scriptText = $InputScript.Replace("`r`n", "`n") + "`n"
            [IO.File]::WriteAllText((Join-Path $runDir "$Stage.stdin.sh"), $scriptText,
                (New-Object System.Text.UTF8Encoding($false)))
            $process.StandardInput.Write($scriptText)
        }
        $process.StandardInput.Close()
        $process.WaitForExit()
        $LASTEXITCODE = $process.ExitCode
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $stdout | Out-File -Encoding utf8 (Join-Path $runDir "$Stage.stdout.log")
        $stderr | Out-File -Encoding utf8 (Join-Path $runDir "$Stage.stderr.log")
        @("[$Stage] exit=$LASTEXITCODE", $stdout, $stderr) |
            Out-File -Encoding utf8 -Append $consoleLog
        if ($stdout) { Write-Host $stdout.TrimEnd() }
        if ($stderr) { Write-Host $stderr.TrimEnd() }
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Stdout = $stdout }
    } finally {
        $process.Dispose()
    }
}
function Assert-Success($Result, [string]$Stage) {
    if ($Result.ExitCode -ne 0) { throw "$Stage exited with code $($Result.ExitCode); see $Stage.stderr.log" }
}

Push-Location $rootDir
try {
    if ($HostName -notmatch '^[a-zA-Z0-9_][a-zA-Z0-9_@.:-]*$') {
        throw 'Specify -HostName or set SSH_HOST to the SSH destination'
    }
    if (!$RemoteDir) { $RemoteDir = '~/chipdesign_vcs/' + (Split-Path $rootDir -Leaf) }
    # Remote paths are validated again after resolving symlinks on the host.
    if ($RemoteDir -notmatch '^(~/|/)[a-zA-Z0-9_./-]+$') {
        throw 'RemoteDir must be a ~/ or absolute path using letters, digits, _, ., / and -'
    }
    # Relative local paths also work with Git's GNU tar/scp on Windows, where
    # a drive-letter colon would otherwise be interpreted as a remote host.
    $archive = "$runRelative/hardware.tar.gz"
    $result = Invoke-Native -Program tar -Stage archive -Arguments @('-czf', $archive,
        '--exclude=build', '--exclude=.git', '--exclude=hardware/soc/sim/out', '-C', '.', 'hardware')
    Assert-Success $result archive

    $pathMode = 'absolute'
    $basePath = $RemoteDir
    if ($RemoteDir.StartsWith('~/')) { $pathMode = 'home'; $basePath = $RemoteDir.Substring(2) }
    $prepare = @'
set -eu
if [ "$1" = home ]; then base="$HOME/$2"; else base="$2"; fi
base=$(realpath -m -- "$base")
home=$(realpath -- "$HOME")
case "$base" in "$home"/*) ;; *) echo "RemoteDir must resolve inside home" >&2; exit 2;; esac
run="$base/$3"
mkdir -p -- "$base"
mkdir -- "$run"
printf '%s\n' "$run"
'@
    $result = Invoke-Native -Program ssh -Stage prepare -InputScript $prepare -Arguments @(
        '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', $HostName, 'bash', '-s', '--',
        $pathMode, $basePath, $runName)
    Assert-Success $result prepare
    $remoteRoot = $result.Stdout.Trim()
    if ($remoteRoot -notmatch '^/[a-zA-Z0-9_./-]+$') { throw "Unexpected remote path: $remoteRoot" }
    $result = Invoke-Native -Program scp -Stage upload -Arguments @('-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=15', $archive, "${HostName}:${remoteRoot}/hardware.tar.gz")
    Assert-Success $result upload
    $extract = @'
set -eu
cd -- "$1"
tar -xzf hardware.tar.gz
rm -- hardware.tar.gz
'@
    $result = Invoke-Native -Program ssh -Stage extract -InputScript $extract -Arguments @(
        '-o', 'BatchMode=yes', $HostName, 'bash', '-s', '--', $remoteRoot)
    Assert-Success $result extract
    $remoteOutput = "$remoteRoot/build/$runName"
    $command = @'
set -eu
cd -- "$1"
exec bash hardware/soc/sim/scripts/run_structure_regression_linux.sh --output "$2" --timeout "$3" --compile-timeout "$4" --sim "$5"
'@
    Write-Output "Running $Simulator on $HostName; local logs: $runDir"
    # Log retrieval is attempted even when compilation or simulation fails.
    $simExit = 1
    $copyExit = 1
    try {
        $result = Invoke-Native -Program ssh -Stage regression -InputScript $command -Arguments @(
            '-o', 'BatchMode=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            $HostName, 'bash', '-s', '--', $remoteRoot, $remoteOutput,
            [string]$TimeoutSeconds, [string]$CompileTimeoutSeconds, $Simulator)
        $simExit = $result.ExitCode
    } finally {
        $result = Invoke-Native -Program scp -Stage download -Arguments @('-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=15', '-r', "${HostName}:${remoteOutput}/.", $runRelative)
        $copyExit = $result.ExitCode
    }
    if ($copyExit -ne 0) { throw "Log retrieval failed (exit $copyExit); remote logs: $remoteOutput" }
    if ($simExit -ne 0) { throw "Remote regression failed (exit $simExit); logs: $runDir" }
    $regressionLog = Get-Content -LiteralPath (Join-Path $runDir 'regression.log') -Raw
    if ($regressionLog -notmatch '(?m)^PASS structure regression;') {
        throw "Remote regression did not report PASS; logs: $runDir"
    }
    Write-Output "PASS structure regression; logs: $runDir"
} catch {
    $failure = "FAIL structure regression: $_; local logs: $runDir"
    $failure | Out-File -Encoding utf8 -Append $consoleLog
    Write-Error $failure -ErrorAction Continue
    exit 1
} finally {
    Pop-Location
}
