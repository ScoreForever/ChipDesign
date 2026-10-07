$ErrorActionPreference = 'Stop'

$npuDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rtlDir = Join-Path $npuDir 'rtl'
$tbDir = Join-Path $npuDir 'tb'
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ('requant-unit-test-' + [guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null

try {
    foreach ($lanes in @(8, 1, 3)) {
        $image = Join-Path $buildDir "tb_requant_unit_${lanes}.vvp"
        & iverilog -g2012 -Wall -s tb_requant_unit "-Ptb_requant_unit.LANES=$lanes" `
            -o $image (Join-Path $rtlDir 'requant_unit.sv') (Join-Path $tbDir 'tb_requant_unit.sv')
        if ($LASTEXITCODE -ne 0) { throw "Requant Unit LANES=$lanes compile failed" }
        $simOutput = (& vvp $image 2>&1 | Out-String)
        Write-Output $simOutput.TrimEnd()
        if ($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL REQUANT UNIT TESTS PASSED') {
            throw "Requant Unit LANES=$lanes simulation failed"
        }
    }
    Write-Output 'PASS requant unit regression: TFLite double rounding, 8/1/3 lanes'
} catch {
    Write-Error "FAIL requant unit regression: $_"
    exit 1
} finally {
    if (!$buildDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
