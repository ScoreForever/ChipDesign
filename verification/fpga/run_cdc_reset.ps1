param([Parameter(Mandatory)][string]$Output)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$out=[IO.Path]::GetFullPath($Output)
if (!$out.StartsWith((Join-Path $repo 'build')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {throw 'Output must be inside build/'}
$out=$out.Replace('\','/')
$repo=$repo.Replace('\','/')
if (Test-Path -LiteralPath $out) {throw 'CDC evidence already exists'}
New-Item -ItemType Directory -Path $out | Out-Null
Push-Location $out
try {
    & vlib work *> vlib.log
    if ($LASTEXITCODE -ne 0) {throw 'vlib failed'}
    & vmap -c *> vmap.log
    if ($LASTEXITCODE -ne 0) {throw 'vmap -c failed'}
    & vmap work "$out/work" *>> vmap.log
    if ($LASTEXITCODE -ne 0) {throw 'vmap work failed'}
    & vlog -sv -modelsimini "$out/modelsim.ini" -work work `
        "$repo/hardware/soc/rtl/clk_rst/rstgen_bypass.sv" `
        "$repo/hardware/soc/rtl/clk_rst/rstgen.sv" `
        "$repo/hardware/soc/rtl/common_cells/cdc_2phase.sv" `
        "$repo/verification/fpga/tb_cdc_reset.sv" *> compile.log
    if ($LASTEXITCODE -ne 0) {throw 'CDC compile failed'}
    & vsim -c -modelsimini "$out/modelsim.ini" work.tb_cdc_reset -l "$out/sim.log" -do 'run -all; quit -f' *> console.log
    $code=$LASTEXITCODE
    $log=Get-Content -LiteralPath "$out/sim.log" -Raw
    if ($code -ne 0 -or $log -notmatch 'PASS CDC reset:' -or $log -match '\*\* (Fatal|Error):|(?m)^# Errors: [1-9]') {throw 'CDC reset test failed'}
    Write-Output 'CDC_RESET_REGRESSION=PASS'
} finally {Pop-Location}
