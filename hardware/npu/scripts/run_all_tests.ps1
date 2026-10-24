$ErrorActionPreference='Stop'
& python (Join-Path $PSScriptRoot 'run_program_regression.py') @args
if($LASTEXITCODE -ne 0){throw 'programmable NPU regression failed'}
