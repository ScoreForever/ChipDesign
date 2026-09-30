$ErrorActionPreference='Stop'
$npuDir=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tempRoot=[System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir=[System.IO.Path]::GetFullPath((Join-Path $tempRoot ('maxpool-test-'+[guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null
try {
    foreach($config in @(@(1,8),@(1,4),@(2,8),@(2,4))) {
        $pool=$config[0]; $lanes=$config[1]
        $image=Join-Path $buildDir "tb_pool_${pool}_${lanes}.vvp"
        & iverilog -g2012 -Wall -s tb_maxpool2x2_engine `
            "-Ptb_maxpool2x2_engine.TEST_POOL=$pool" `
            "-Ptb_maxpool2x2_engine.LANES=$lanes" -o $image `
            (Join-Path $npuDir 'rtl/vector_unit.sv') `
            (Join-Path $npuDir 'rtl/maxpool2x2_engine.sv') `
            (Join-Path $npuDir 'tb/tb_maxpool2x2_engine.sv')
        if($LASTEXITCODE -ne 0){throw "MaxPool pool=$pool LANES=$lanes compile failed"}
        $simOutput=(& vvp $image 2>&1 | Out-String)
        Write-Output $simOutput.TrimEnd()
        if($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL MAXPOOL2X2 TESTS PASSED'){
            throw "MaxPool pool=$pool LANES=$lanes simulation failed"
        }
    }
    Write-Output 'PASS MaxPool regression: both TinyCNN pools, 8/4 lanes'
} catch { Write-Error "FAIL MaxPool regression: $_"; exit 1 }
finally {
    if(!$buildDir.StartsWith($tempRoot,[System.StringComparison]::OrdinalIgnoreCase)){
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
