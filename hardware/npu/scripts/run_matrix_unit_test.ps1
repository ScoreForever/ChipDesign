$ErrorActionPreference = 'Stop'

$npuDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rtlDir = Join-Path $npuDir 'rtl'
$tbDir = Join-Path $npuDir 'tb'
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ('matrix-unit-test-' + [guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null

try {
    $rtl = @(
        (Join-Path $rtlDir 'ws_pe.sv'),
        (Join-Path $rtlDir 'ws_systolic_array.sv'),
        (Join-Path $rtlDir 'matrix_unit.sv')
    )

    $peImage = Join-Path $buildDir 'tb_ws_pe.vvp'
    & iverilog -g2012 -Wall -s tb_ws_pe -o $peImage @rtl (Join-Path $tbDir 'tb_ws_pe.sv')
    if ($LASTEXITCODE -ne 0) { throw 'PE compile failed' }
    & vvp $peImage
    if ($LASTEXITCODE -ne 0) { throw 'PE simulation failed' }

    foreach ($config in @(@(4, 4), @(4, 8), @(8, 8))) {
        $rows = $config[0]
        $cols = $config[1]
        $image = Join-Path $buildDir "tb_matrix_unit_${rows}x${cols}.vvp"
        & iverilog -g2012 -Wall -s tb_matrix_unit "-Ptb_matrix_unit.ARRAY_ROWS=$rows" "-Ptb_matrix_unit.ARRAY_COLS=$cols" -o $image @rtl (Join-Path $tbDir 'tb_matrix_unit.sv')
        if ($LASTEXITCODE -ne 0) { throw "Matrix Unit ${rows}x${cols} compile failed" }
        & vvp $image
        if ($LASTEXITCODE -ne 0) { throw "Matrix Unit ${rows}x${cols} simulation failed" }
    }
    Write-Output 'PASS matrix unit regression: PE, 4x4, 4x8, 8x8'
} catch {
    Write-Error "FAIL matrix unit regression: $_"
    exit 1
} finally {
    if (!$buildDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
