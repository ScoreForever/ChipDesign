$ErrorActionPreference = 'Stop'

$npuDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rtlDir = Join-Path $npuDir 'rtl'
$tbDir = Join-Path $npuDir 'tb'
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ('reduction-sum-test-' + [guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null

try {
    foreach ($lanes in @(8, 1, 3)) {
        $image = Join-Path $buildDir "tb_reduction_sum_${lanes}.vvp"
        & iverilog -g2012 -Wall -s tb_reduction_sum_unit `
            "-Ptb_reduction_sum_unit.LANES=$lanes" -o $image `
            (Join-Path $rtlDir 'reduction_sum_unit.sv') `
            (Join-Path $tbDir 'tb_reduction_sum_unit.sv')
        if ($LASTEXITCODE -ne 0) { throw "Reduction Sum LANES=$lanes compile failed" }
        $simOutput = (& vvp $image 2>&1 | Out-String)
        Write-Output $simOutput.TrimEnd()
        if ($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL REDUCTION SUM TESTS PASSED') {
            throw "Reduction Sum LANES=$lanes simulation failed"
        }
    }
    Write-Output 'PASS reduction sum regression: signed INT8 to INT32, 8/1/3 lanes'
} catch {
    Write-Error "FAIL reduction sum regression: $_"
    exit 1
} finally {
    if (!$buildDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
