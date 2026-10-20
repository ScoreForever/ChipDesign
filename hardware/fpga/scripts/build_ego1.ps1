param(
    [string]$VivadoRoot = $env:XILINX_VIVADO,
    [int]$Jobs = 4,
    [ValidateRange(1, 500)][double]$FrequencyMHz = 50,
    [string]$OutputDir = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (!$OutputDir) { $OutputDir = Join-Path $repoRoot ('build/fpga_ego1_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff')) }
$outputDir = [IO.Path]::GetFullPath($OutputDir)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'build')) + [IO.Path]::DirectorySeparatorChar
if (!$outputDir.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'OutputDir must be inside build/' }
if (Test-Path -LiteralPath $outputDir) { throw 'OutputDir must be new' }
New-Item -ItemType Directory -Path $outputDir | Out-Null
$logPath = Join-Path $outputDir 'vivado.log'
$summaryPath = Join-Path $outputDir 'summary.md'
$savedTemp = $env:TEMP
$savedTmp = $env:TMP
$savedLocalData = $env:XILINX_LOCAL_USER_DATA
$exitCode = 1
$sourceHashes = @{}
Get-ChildItem -LiteralPath (Join-Path $repoRoot 'hardware') -Recurse -File |
    Where-Object { $_.Extension -in '.sv','.svh','.v','.xdc','.f','.tcl' } |
    ForEach-Object { $sourceHashes[$_.FullName.Substring($repoRoot.Length+1).Replace('\','/')] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
try {
    if (-not $VivadoRoot) { throw 'Set XILINX_VIVADO or pass -VivadoRoot to the Vivado installation directory.' }
    $vivado = Join-Path $VivadoRoot 'bin/vivado.bat'
    if (-not (Test-Path -LiteralPath $vivado)) { throw "Vivado not found: $vivado" }
    if ($Jobs -lt 1) { throw 'Jobs must be positive.' }
    $tempDir = Join-Path $outputDir 'tmp'
    New-Item -ItemType Directory -Path $tempDir | Out-Null
    $env:TEMP = $tempDir
    $env:TMP = $tempDir
    $env:XILINX_LOCAL_USER_DATA = 'no'
    Write-Host "Build output: $outputDir"
    Push-Location $outputDir
    try {
        $buildArgs = @($repoRoot, $outputDir, $Jobs)
        if ($PSBoundParameters.ContainsKey('FrequencyMHz')) {
            $buildArgs += $FrequencyMHz.ToString('0.000', [Globalization.CultureInfo]::InvariantCulture)
        }
        & $vivado -mode batch -source (Join-Path $PSScriptRoot 'build_ego1.tcl') -log $logPath -journal (Join-Path $outputDir 'vivado.jou') -tclargs @buildArgs
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            foreach ($name in $sourceHashes.Keys) {
                if ((Get-FileHash -LiteralPath (Join-Path $repoRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sourceHashes[$name]) {
                    throw "Hardware input changed during build: $name"
                }
            }
            @{
                schema=1; source_sha256=$sourceHashes
                bitstream_sha256=(Get-FileHash -LiteralPath (Join-Path $outputDir 'fpga_top.bit') -Algorithm SHA256).Hash.ToLowerInvariant()
                checkpoint_sha256=(Get-FileHash -LiteralPath (Join-Path $outputDir 'route.dcp') -Algorithm SHA256).Hash.ToLowerInvariant()
            } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $outputDir 'build_identity.json') -Encoding utf8
        }
    } finally { Pop-Location }
} catch {
    $exitCode = 1
    Write-Host "Build failed: $($_.Exception.Message)"
    if (-not (Test-Path -LiteralPath $summaryPath)) {
        @("# EGo1 build summary", "", "Result: FAILED (launcher)", "Reason: $($_.Exception.Message)", "Log: $logPath") | Set-Content -LiteralPath $summaryPath -Encoding utf8
    }
} finally {
    $env:TEMP = $savedTemp
    $env:TMP = $savedTmp
    $env:XILINX_LOCAL_USER_DATA = $savedLocalData
}
if (-not (Test-Path -LiteralPath $summaryPath)) {
    @("# EGo1 build summary", "", "Result: FAILED (Vivado ended without a summary)", "Exit code: $exitCode", "Log: $logPath") | Set-Content -LiteralPath $summaryPath -Encoding utf8
    if ($exitCode -eq 0) { $exitCode = 1 }
}
if (Test-Path -LiteralPath $logPath) {
    $warnings = @(Select-String -LiteralPath $logPath -Pattern '^CRITICAL WARNING:' | ForEach-Object { $_.Line } | Select-Object -Unique)
    Add-Content -LiteralPath $summaryPath -Encoding utf8 -Value "`n## Critical warnings (Vivado log)"
    if ($warnings.Count) { $warnings | Add-Content -LiteralPath $summaryPath -Encoding utf8 }
    else { Add-Content -LiteralPath $summaryPath -Encoding utf8 -Value 'None.' }
}
Write-Host "Summary: $summaryPath"
Write-Host "Log: $logPath"
exit $exitCode
