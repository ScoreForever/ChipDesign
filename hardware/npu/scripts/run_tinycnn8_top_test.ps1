$ErrorActionPreference='Stop'
$npuDir=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tempRoot=[System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir=[System.IO.Path]::GetFullPath((Join-Path $tempRoot ('tinycnn-top-'+[guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null
try{
    $rtl=@('ws_pe.sv','ws_systolic_array.sv','matrix_unit.sv','vector_unit.sv',
        'requant_unit.sv','reduction_sum_unit.sv','conv_window_addr_gen.sv',
        'conv2d_engine.sv','maxpool2x2_engine.sv','global_sum_pool_engine.sv',
        'global_avg_pool_engine.sv','tinycnn8_npu_top.sv') | ForEach-Object {Join-Path $npuDir "rtl/$_"}
    foreach($cfg in @(@(4,8),@(4,4))){
        $rows=$cfg[0];$cols=$cfg[1];$image=Join-Path $buildDir "tb_top_${rows}_${cols}.vvp"
        & iverilog -g2012 -Wall -s tb_tinycnn8_npu_top `
            "-Ptb_tinycnn8_npu_top.ARRAY_ROWS=$rows" `
            "-Ptb_tinycnn8_npu_top.ARRAY_COLS=$cols" -o $image @rtl `
            (Join-Path $npuDir 'tb/tb_tinycnn8_npu_top.sv')
        if($LASTEXITCODE -ne 0){throw "TinyCNN top ${rows}x${cols} compile failed"}
        $simOutput=(& vvp $image 2>&1 | Out-String);Write-Output $simOutput.TrimEnd()
        if($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL TINYCNN8 TOP TESTS PASSED'){
            throw "TinyCNN top ${rows}x${cols} simulation failed"
        }
    }
    Write-Output 'PASS TinyCNN-8 end-to-end RTL regression: 4x8 and 4x4 arrays'
}catch{Write-Error "FAIL TinyCNN-8 top regression: $_";exit 1}
finally{
    if(!$buildDir.StartsWith($tempRoot,[System.StringComparison]::OrdinalIgnoreCase)){
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
