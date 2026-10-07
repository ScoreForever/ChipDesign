$ErrorActionPreference='Stop'
$npuDir=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tempRoot=[System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir=[System.IO.Path]::GetFullPath((Join-Path $tempRoot ('fc-test-'+[guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null
try{
    $rtl=@((Join-Path $npuDir 'rtl/ws_pe.sv'),(Join-Path $npuDir 'rtl/ws_systolic_array.sv'),
        (Join-Path $npuDir 'rtl/matrix_unit.sv'),(Join-Path $npuDir 'rtl/fc_engine.sv'))
    foreach($cfg in @(@(4,8,4),@(4,8,6),@(4,4,4),@(4,4,6))){
        $rows=$cfg[0];$cols=$cfg[1];$outputs=$cfg[2]
        $image=Join-Path $buildDir "tb_fc_${rows}_${cols}_${outputs}.vvp"
        & iverilog -g2012 -Wall -s tb_fc_engine `
            "-Ptb_fc_engine.ARRAY_ROWS=$rows" "-Ptb_fc_engine.ARRAY_COLS=$cols" `
            "-Ptb_fc_engine.OUTPUT_CHANNELS=$outputs" -o $image @rtl `
            (Join-Path $npuDir 'tb/tb_fc_engine.sv')
        if($LASTEXITCODE -ne 0){throw "FC ${rows}x${cols} outputs=$outputs compile failed"}
        $simOutput=(& vvp $image 2>&1 | Out-String);Write-Output $simOutput.TrimEnd()
        if($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL FC ENGINE TESTS PASSED'){
            throw "FC ${rows}x${cols} outputs=$outputs simulation failed"
        }
    }
    Write-Output 'PASS FC engine regression: 4/6 classes on 4x8 and 4x4 arrays'
}catch{Write-Error "FAIL FC engine regression: $_";exit 1}
finally{
    if(!$buildDir.StartsWith($tempRoot,[System.StringComparison]::OrdinalIgnoreCase)){
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
