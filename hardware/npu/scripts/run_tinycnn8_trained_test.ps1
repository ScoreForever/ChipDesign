param(
    [Parameter(Mandatory=$true)][string]$VectorDir
)
$ErrorActionPreference='Stop'
$npuDir=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$vectors=(Resolve-Path $VectorDir).Path.Replace('\','/')
$tempRoot=[System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$buildDir=[System.IO.Path]::GetFullPath((Join-Path $tempRoot ('tinycnn-trained-'+[guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $buildDir | Out-Null
try{
    $rtl=@('ws_pe.sv','ws_systolic_array.sv','matrix_unit.sv','vector_unit.sv',
        'requant_unit.sv','conv_window_addr_gen.sv',
        'conv2d_engine.sv','maxpool2x2_engine.sv','tinycnn8_npu_top.sv') |
        ForEach-Object {Join-Path $npuDir "rtl/$_"}
    $image=Join-Path $buildDir 'tb_trained.vvp'
    & iverilog -g2012 -Wall -s tb_tinycnn8_trained_model -o $image @rtl `
        (Join-Path $npuDir 'tb/tb_tinycnn8_trained_model.sv')
    if($LASTEXITCODE-ne 0){throw 'trained-model RTL compile failed'}
    & vvp $image "+VECTOR_DIR=$vectors"
    if($LASTEXITCODE-ne 0){throw 'trained-model RTL simulation failed'}
}finally{
    if(Test-Path $buildDir){Remove-Item -LiteralPath $buildDir -Recurse -Force}
}
