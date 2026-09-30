$ErrorActionPreference = 'Stop'

$npuDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ('conv2d-engine-test-' + [guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null

try {
    $rtl = @(
        (Join-Path $npuDir 'rtl/ws_pe.sv'),
        (Join-Path $npuDir 'rtl/ws_systolic_array.sv'),
        (Join-Path $npuDir 'rtl/matrix_unit.sv'),
        (Join-Path $npuDir 'rtl/requant_unit.sv'),
        (Join-Path $npuDir 'rtl/conv_window_addr_gen.sv'),
        (Join-Path $npuDir 'rtl/conv2d_engine.sv')
    )
    foreach ($config in @(@(1, 4, 8), @(1, 4, 4), @(2, 4, 8), @(2, 4, 4))) {
        $layer = $config[0]
        $rows = $config[1]
        $cols = $config[2]
        $image = Join-Path $buildDir "tb_conv2d_engine_l${layer}_${rows}x${cols}.vvp"
        & iverilog -g2012 -Wall -s tb_conv2d_engine `
            "-Ptb_conv2d_engine.TEST_LAYER=$layer" `
            "-Ptb_conv2d_engine.ARRAY_ROWS=$rows" `
            "-Ptb_conv2d_engine.ARRAY_COLS=$cols" -o $image @rtl `
            (Join-Path $npuDir 'tb/tb_conv2d_engine.sv')
        if ($LASTEXITCODE -ne 0) { throw "Conv2D engine layer $layer ${rows}x${cols} compile failed" }
        $simOutput = (& vvp $image 2>&1 | Out-String)
        Write-Output $simOutput.TrimEnd()
        if ($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL CONV2D ENGINE TESTS PASSED') {
            throw "Conv2D engine layer $layer ${rows}x${cols} simulation failed"
        }
    }
    Write-Output 'PASS Conv2D engine regression: TinyCNN Conv1/Conv2 on 4x8 and 4x4 arrays'
} catch {
    Write-Error "FAIL Conv2D engine regression: $_"
    exit 1
} finally {
    if (!$buildDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
