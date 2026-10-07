$ErrorActionPreference='Stop'
$npuDir=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tempRoot=[System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir=[System.IO.Path]::GetFullPath((Join-Path $tempRoot ('gap-test-'+[guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null
try{
    foreach($lanes in @(8,4,3)){
        $image=Join-Path $buildDir "tb_gap_${lanes}.vvp"
        & iverilog -g2012 -Wall -s tb_global_sum_pool_engine `
            "-Ptb_global_sum_pool_engine.LANES=$lanes" -o $image `
            (Join-Path $npuDir 'rtl/reduction_sum_unit.sv') `
            (Join-Path $npuDir 'rtl/global_sum_pool_engine.sv') `
            (Join-Path $npuDir 'tb/tb_global_sum_pool_engine.sv')
        if($LASTEXITCODE -ne 0){throw "GAP LANES=$lanes compile failed"}
        $simOutput=(& vvp $image 2>&1 | Out-String)
        Write-Output $simOutput.TrimEnd()
        if($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL GLOBAL SUM POOL TESTS PASSED'){
            throw "GAP LANES=$lanes simulation failed"
        }
    }
    Write-Output 'PASS global sum pool regression: TinyCNN 5x4x8, 8/4/3 lanes'
}catch{Write-Error "FAIL global sum pool regression: $_";exit 1}
finally{
    if(!$buildDir.StartsWith($tempRoot,[System.StringComparison]::OrdinalIgnoreCase)){
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
