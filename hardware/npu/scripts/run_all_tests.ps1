$ErrorActionPreference='Stop'
$scripts=@(
    'run_matrix_unit_test.ps1',
    'run_vector_unit_test.ps1',
    'run_requant_unit_test.ps1',
    'run_reduction_sum_test.ps1',
    'run_conv_window_test.ps1',
    'run_conv2d_engine_test.ps1',
    'run_maxpool_test.ps1',
    'run_global_sum_pool_test.ps1',
    'run_global_avg_pool_test.ps1',
    'run_fc_engine_test.ps1',
    'run_tinycnn8_top_test.ps1',
    'run_tinycnn8_mmio_test.ps1'
)
foreach($script in $scripts){
    Write-Output "=== $script ==="
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot $script)
    if($LASTEXITCODE -ne 0){throw "$script failed"}
}
Write-Output 'ALL NPU RTL REGRESSIONS PASSED'
