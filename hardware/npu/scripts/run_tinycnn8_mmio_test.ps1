$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$tmp=Join-Path ([System.IO.Path]::GetTempPath()) ("tinycnn8-mmio-test-"+[guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $rtl=Join-Path $root 'hardware\npu\rtl'
    $soc=Join-Path $root 'hardware\soc\rtl\npu'
    $tb=Join-Path $root 'hardware\npu\tb\tb_tinycnn8_npu_mmio_wrapper.sv'
    $out=Join-Path $tmp 'tb_tinycnn8_npu_mmio_wrapper.vvp'
    & iverilog -g2012 -Wall -s tb_tinycnn8_npu_mmio_wrapper -o $out `
        (Join-Path $rtl 'ws_pe.sv') `
        (Join-Path $rtl 'ws_systolic_array.sv') `
        (Join-Path $rtl 'matrix_unit.sv') `
        (Join-Path $rtl 'vector_unit.sv') `
        (Join-Path $rtl 'requant_unit.sv') `
        (Join-Path $rtl 'conv_window_addr_gen.sv') `
        (Join-Path $rtl 'conv2d_engine.sv') `
        (Join-Path $rtl 'maxpool2x2_engine.sv') `
        (Join-Path $rtl 'tinycnn8_npu_top.sv') `
        (Join-Path $soc 'tinycnn8_npu_mmio_wrapper.sv') $tb
    if($LASTEXITCODE-ne 0){throw 'iverilog compile failed'}
    & vvp $out
    if($LASTEXITCODE-ne 0){throw 'simulation failed'}
} finally {
    if(Test-Path $tmp){Remove-Item -LiteralPath $tmp -Recurse -Force}
}
