[CmdletBinding()]
param([ValidateSet('asic', 'fpga')][string]$Config = 'asic')
$ErrorActionPreference = 'Stop'
$rootDir = (Resolve-Path (Join-Path $PSScriptRoot '../../../..')).Path
$runDir = Join-Path $rootDir ('build/structure_regression_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $runDir | Out-Null
$runPath = $runDir.Replace('\', '/')
$libraryPath = "$runPath/library"

function Run-Test {
    param([string]$Top, [string]$Name, [string]$PassMarker, [string[]]$Generics = @())
    $simArgs = @('-c', '-modelsimini', "$runPath/modelsim.ini", "verify.$Top")
    $simArgs += $Generics
    $simArgs += @('-l', "$runPath/$Name.log", '-do', 'run 0; vcd off; run -all; quit -f')
    & vsim @simArgs *> "$runPath/${Name}_console.log"
    $simExit = $LASTEXITCODE
    $simText = Get-Content "$runPath/$Name.log" -Raw
    if ($simExit -ne 0 -or
        $simText -notmatch [regex]::Escape($PassMarker) -or
        $simText -match '\*\* (Fatal|Error):|\bFAIL:|\bTIMEOUT|(?m)^# Errors: [1-9]') {
        Get-Content "$runPath/$Name.log" -Tail 25
        throw "$Name failed; see $runDir"
    }
    Select-String -Path "$runPath/$Name.log" -Pattern 'PASS|Errors:' |
        ForEach-Object { $_.Line }
}

Push-Location $runDir
try {
    & vlib $libraryPath
    if ($LASTEXITCODE -ne 0) { throw 'vlib failed' }
    & vmap -c
    if ($LASTEXITCODE -ne 0) { throw 'vmap -c failed' }
    & vmap verify $libraryPath
    if ($LASTEXITCODE -ne 0) { throw 'vmap failed' }
} finally {
    Pop-Location
}

Push-Location $rootDir
try {
    New-Item -ItemType Directory -Path 'hardware/soc/sim/out' -Force | Out-Null
    $defines = @()
    if ($Config -eq 'fpga') { $defines += '+define+CHIPDESIGN_FPGA_MEM' }
    & vlog @defines -sv -modelsimini "$runPath/modelsim.ini" -work verify `
        -f hardware/soc/sim/filelists/chipdesign_soc.f `
        hardware/npu/tb/tb_requant_unit.sv `
        hardware/npu/tb/tb_tinycnn8_capacity.sv `
        hardware/soc/sim/tb/tb_axi_sram_pipeline.sv *> "$runPath/compile.log"
    if ($LASTEXITCODE -ne 0) {
        Get-Content "$runPath/compile.log" -Tail 25
        throw 'vlog failed'
    }
    Run-Test 'tb_requant_unit' 'requant' 'ALL REQUANT UNIT TESTS PASSED'
    Run-Test 'tb_tinycnn8_capacity' 'capacity_asymmetric' 'ALL FLAT CAPACITY TESTS PASSED'
    Run-Test 'tb_tinycnn8_capacity' 'capacity_generic' 'ALL FLAT CAPACITY TESTS PASSED' `
        @('-gACT_BANK_BYTES=4096', '-gACT_A_BANK_BYTES=4096')
    Run-Test 'chipdesign_soc_tb' 'soc' 'PASS: integrated SoC test OK'
    Run-Test 'chipdesign_dma_tb' 'dma' 'PASS: NPU DMA test OK'
    Run-Test 'tb_axi_sram_pipeline' 'sram_pipeline' 'ALL AXI SRAM PIPELINE TESTS PASSED'
    Run-Test 'tb_axi_sram_pipeline' 'sram_legacy' 'ALL AXI SRAM PIPELINE TESTS PASSED' `
        @('-gREAD_LATENCY=1')
    Write-Output "PASS structure regression; logs: $runDir"
} finally {
    Pop-Location
}
