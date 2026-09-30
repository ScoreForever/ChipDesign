$ErrorActionPreference = 'Stop'

$npuDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ('conv-window-test-' + [guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null

try {
    $image = Join-Path $buildDir 'tb_conv_window_addr_gen.vvp'
    & iverilog -g2012 -Wall -s tb_conv_window_addr_gen -o $image `
        (Join-Path $npuDir 'rtl/conv_window_addr_gen.sv') `
        (Join-Path $npuDir 'tb/tb_conv_window_addr_gen.sv')
    if ($LASTEXITCODE -ne 0) { throw 'Conv window address generator compile failed' }
    $simOutput = (& vvp $image 2>&1 | Out-String)
    Write-Output $simOutput.TrimEnd()
    if ($LASTEXITCODE -ne 0 -or $simOutput -notmatch 'ALL CONV WINDOW ADDRESS TESTS PASSED') {
        throw 'Conv window address generator simulation failed'
    }
    Write-Output 'PASS conv window address generator regression'
} catch {
    Write-Error "FAIL conv window address generator regression: $_"
    exit 1
} finally {
    if (!$buildDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build directory outside temp: $buildDir"
    }
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
