$ErrorActionPreference = 'Stop'

$npuDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rtlDir = Join-Path $npuDir 'rtl'
$tbDir = Join-Path $npuDir 'tb'
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ('vector-unit-test-' + [guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null

try {
    foreach ($lanes in @(8, 1, 3, 16)) {
        $exhaustive = if ($lanes -eq 8) { 1 } else { 0 }
        $image = Join-Path $buildDir "tb_vector_unit_${lanes}.vvp"
        $compileArgs = @(
            '-g2012', '-Wall', '-s', 'tb_vector_unit',
            "-Ptb_vector_unit.LANES=$lanes",
            "-Ptb_vector_unit.EXHAUSTIVE=$exhaustive",
            '-o', $image,
            (Join-Path $rtlDir 'vector_unit.sv'),
            (Join-Path $tbDir 'tb_vector_unit.sv')
        )
        Write-Output ('iverilog ' + (($compileArgs | ForEach-Object { '"' + $_ + '"' }) -join ' '))
        & iverilog @compileArgs
        if ($LASTEXITCODE -ne 0) { throw "Vector Unit LANES=$lanes compile failed" }
        Write-Output "vvp `"$image`""
        & vvp $image
        if ($LASTEXITCODE -ne 0) { throw "Vector Unit LANES=$lanes simulation failed" }
    }
    Write-Output 'PASS vector unit regression: INT8, 8/1/3/16 lanes'
} catch {
    Write-Error "FAIL vector unit regression: $_"
    exit 1
} finally {
    if (!$buildDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
