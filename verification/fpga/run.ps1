[CmdletBinding()]
param(
    [ValidateSet('Prepare','Program','Gdb','Board','Recovery','Link','Rtl','Audit','Report','Run')]
    [string]$Stage = 'Run',
    [Parameter(Mandatory)][string]$Output,
    [string]$Bitstream = '',
    [string]$BitstreamSha256 = '',
    [string]$Checkpoint = '',
    [string]$CableSerial = '',
    [string]$Distro = 'Ubuntu',
    [string]$Vivado = 'D:/AMDDesignTools/2025.2/Vivado/bin/vivado.bat',
    [string]$Python = 'C:/Users/Lenovo/.conda/envs/ai_env/python.exe',
    [int]$Soak = 100,
    [int]$Resets = 6
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$out = [IO.Path]::GetFullPath((Join-Path $repo $Output))
$buildRoot = [IO.Path]::GetFullPath((Join-Path $repo 'build')) + [IO.Path]::DirectorySeparatorChar
if (!$out.StartsWith($buildRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be inside this repository build/.' }
if ($Soak -lt 1 -or $Resets -lt 2) { throw 'Soak >= 1 and Resets >= 2 required.' }
function Invoke-Checked([string]$Program,[string[]]$Arguments,[string]$Log) {
    if (Test-Path -LiteralPath $Log) { throw "Evidence exists; choose a fresh output directory: $Log" }
    & $Program @Arguments *> $Log
    $code = $LASTEXITCODE
    Get-Content -LiteralPath $Log -Tail 8 | Write-Output
    if ($code -ne 0) { throw "$Program exited $code; see $Log" }
}
function Linux-Path([string]$Path) {
    $value = & wsl.exe -d $Distro --exec wslpath -a -u $Path
    if ($LASTEXITCODE -ne 0) { throw "wslpath failed: $Path" }
    return ($value | Select-Object -Last 1).Trim()
}
function Sha([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Json([string]$Path,$Value) { $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Path -Encoding utf8 }
function Artifact-Identity {
    if (!$Bitstream -or $BitstreamSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Pass explicit -Bitstream and -BitstreamSha256.' }
    $script:bit = (Resolve-Path -LiteralPath $Bitstream).Path
    if ((Sha $bit) -ne $BitstreamSha256.ToLowerInvariant()) { throw 'Bitstream hash mismatch.' }
    $script:buildIdentityPath = Join-Path (Split-Path $bit) 'build_identity.json'
    $script:buildIdentity = Get-Content -LiteralPath $buildIdentityPath -Raw | ConvertFrom-Json
    if ($buildIdentity.bitstream_sha256 -ne (Sha $bit)) { throw 'Bitstream does not match build identity.' }
    $manifest = Get-Content -LiteralPath (Join-Path $out 'manifest.json') -Raw | ConvertFrom-Json
    foreach ($entry in $buildIdentity.source_sha256.PSObject.Properties) {
        $recorded = $manifest.source_sha256.PSObject.Properties[$entry.Name]
        if (!$recorded -or $recorded.Value -ne $entry.Value -or (Sha (Join-Path $repo $entry.Name)) -ne $entry.Value) {
            throw "Build/manifest/live source mismatch: $($entry.Name)"
        }
    }
}
function Deployment-Identity {
    Artifact-Identity
    $receipt = Get-Content -LiteralPath (Join-Path $out 'deployment.json') -Raw | ConvertFrom-Json
    if ($receipt.status -ne 'PASS' -or $receipt.bitstream_sha256 -ne (Sha $bit)) { throw 'Program receipt does not match the requested bitstream.' }
    if ((Sha (Join-Path $out 'program/console.log')) -ne $receipt.program_log_sha256) { throw 'Program log hash mismatch.' }
    if ($receipt.manifest_sha256 -ne (Sha (Join-Path $out 'manifest.json'))) { throw 'Program receipt belongs to another manifest.' }
}
function Do-Stage([string]$Name) {
    Write-Output "FPGA stage: $Name"
    switch ($Name) {
        Prepare {
            if (Test-Path -LiteralPath $out) { throw 'Prepare requires a fresh output directory.' }
            $linuxRepo = Linux-Path $repo; $linuxOut = Linux-Path $out
            # prepare creates the directory itself; log outside it until it exists.
            & wsl.exe -d $Distro --exec python3 "$linuxRepo/verification/fpga/workflow.py" prepare --output $linuxOut --soak $Soak
            if ($LASTEXITCODE -ne 0) { throw 'Prepare failed.' }
        }
        Program {
            Artifact-Identity
            $program = Join-Path $out 'program'
            if (Test-Path -LiteralPath $program) { throw 'Program evidence already exists.' }
            if (!$CableSerial) { throw 'Pass the board programming cable serial explicitly.' }
            if (!(Test-Path -LiteralPath (Join-Path $out 'manifest.json'))) { throw 'Prepare first.' }
            New-Item -ItemType Directory -Path $program | Out-Null
            Copy-Item -LiteralPath (Join-Path (Split-Path $bit) 'summary.md') -Destination (Join-Path $program 'summary.md')
            Copy-Item -LiteralPath $buildIdentityPath -Destination (Join-Path $program 'build_identity.json')
            Invoke-Checked $Vivado @('-mode','batch','-nolog','-nojournal','-source',"$repo/scripts/lab6/program_board.tcl",'-tclargs',$bit,$CableSerial,$program) (Join-Path $program 'console.log')
            $text = Get-Content -LiteralPath (Join-Path $program 'console.log') -Raw
            if ($text -notmatch 'LAB6_PROGRAM_COMMAND=PASS' -or $text -notmatch 'LAB6_FPGA_DONE=1') { throw 'Missing programming success markers.' }
            Json (Join-Path $out 'deployment.json') @{
                status='PASS'; programmed_utc=[DateTime]::UtcNow.ToString('o'); bitstream=$bit; bitstream_sha256=(Sha $bit)
                cable_serial=$CableSerial; program_log_sha256=(Sha (Join-Path $program 'console.log'))
                manifest_sha256=(Sha (Join-Path $out 'manifest.json'))
                build_identity_sha256=(Sha (Join-Path $program 'build_identity.json'))
                identity_scope='Programming command/CRC/DONE plus artifact hash; not configuration readback equivalence proof'
            }
        }
        Gdb {
            Deployment-Identity
            $linuxRepo=Linux-Path $repo
            Invoke-Checked 'wsl.exe' @('-d',$Distro,'--exec','/opt/xpack-riscv-none-elf-gcc-13.2.0-2/bin/riscv-none-elf-gdb','-q','-batch','-x',"$linuxRepo/verification/fpga/gdb_check.gdb") (Join-Path $out 'gdb.log')
            if ((Get-Content -LiteralPath (Join-Path $out 'gdb.log') -Raw) -notmatch 'FPGA_GDB_CHECK=PASS') { throw 'GDB pass marker missing.' }
        }
        Board {
            Deployment-Identity
            $linuxRepo=Linux-Path $repo; $linuxOut=Linux-Path $out; $linuxBit=Linux-Path $bit
            Invoke-Checked 'wsl.exe' @('-d',$Distro,'--exec','python3',"$linuxRepo/verification/fpga/workflow.py",'board','--output',$linuxOut,'--bitstream',$linuxBit,'--bitstream-sha256',$BitstreamSha256) (Join-Path $out 'board_console.log')
        }
        Recovery {
            Deployment-Identity
            $linuxRepo=Linux-Path $repo; $linuxOut=Linux-Path $out
            Invoke-Checked 'wsl.exe' @('-d',$Distro,'--exec','python3',"$linuxRepo/verification/fpga/workflow.py",'recovery','--output',$linuxOut,'--resets',"$Resets") (Join-Path $out 'recovery_console.log')
        }
        Link {
            Deployment-Identity
            $linuxRepo=Linux-Path $repo; $linuxOut=Linux-Path $out
            Invoke-Checked 'wsl.exe' @('-d',$Distro,'--exec','python3',"$linuxRepo/verification/fpga/link_check.py",'--output',$linuxOut) (Join-Path $out 'link_console.log')
        }
        Rtl {
            $manifest=Join-Path $out 'manifest.json'
            if (!(Test-Path -LiteralPath $manifest)) { throw 'Prepare first.' }
            Invoke-Checked $Python @("$repo/hardware/npu/scripts/run_tinycnn8_fused_test.py",'--summary',"$out/rtl_fused_summary.json") (Join-Path $out 'rtl_fused.log')
            # Invoke through the current PowerShell executable to capture a native exit code.
            Invoke-Checked (Get-Process -Id $PID).Path @('-NoProfile','-File',"$repo/hardware/soc/sim/scripts/run_structure_regression.ps1",'-Config','fpga') (Join-Path $out 'rtl_soc.log')
            Invoke-Checked $Python @("$repo/verification/fpga/test_report.py") (Join-Path $out 'evidence_tests.log')
            Invoke-Checked (Get-Process -Id $PID).Path @('-NoProfile','-File',"$repo/verification/fpga/run_cdc_reset.ps1",'-Output',"$out/cdc_reset") (Join-Path $out 'cdc_reset.log')
            Json (Join-Path $out 'rtl_receipt.json') @{
                status='PASS'; manifest_sha256=(Sha $manifest); completed_utc=[DateTime]::UtcNow.ToString('o')
                fused_log_sha256=(Sha "$out/rtl_fused.log"); soc_log_sha256=(Sha "$out/rtl_soc.log")
                fused_summary_sha256=(Sha "$out/rtl_fused_summary.json")
                evidence_tests_sha256=(Sha "$out/evidence_tests.log"); cdc_reset_sha256=(Sha "$out/cdc_reset.log")
                cdc_sim_sha256=(Sha "$out/cdc_reset/sim.log")
            }
        }
        Audit {
            Artifact-Identity
            if (!$Checkpoint) { throw 'Pass -Checkpoint for the routed design corresponding to the bitstream.' }
            $dcp=(Resolve-Path -LiteralPath $Checkpoint).Path
            if ((Sha $dcp) -ne $buildIdentity.checkpoint_sha256) { throw 'Checkpoint does not match the bitstream build.' }
            $audit=Join-Path $out 'audit'; New-Item -ItemType Directory -Path $audit -ErrorAction Stop | Out-Null
            Invoke-Checked $Vivado @('-mode','batch','-nolog','-nojournal','-source',"$repo/verification/fpga/audit_cdc.tcl",'-tclargs',$dcp,$audit) (Join-Path $audit 'console.log')
            if ((Get-Content -LiteralPath "$audit/console.log" -Raw) -notmatch 'FPGA_AUDIT_COLLECTED=PASS') { throw 'Audit collection incomplete.' }
            Json (Join-Path $audit 'receipt.json') @{
                status='COLLECTED'; checkpoint=$dcp; checkpoint_sha256=(Sha $dcp); bitstream_sha256=(Sha $bit)
                manifest_sha256=(Sha "$out/manifest.json"); cdc_sha256=(Sha "$audit/cdc_details.rpt")
                timing_sha256=(Sha "$audit/route_timing_summary.rpt"); methodology_sha256=(Sha "$audit/route_methodology.rpt")
                drc_sha256=(Sha "$audit/route_drc.rpt")
                timing_json_sha256=(Sha "$audit/timing.json"); dmi_constraints_sha256=(Sha "$audit/dmi_constraints.tsv")
                clock_interaction_sha256=(Sha "$audit/clock_interaction.rpt"); exceptions_sha256=(Sha "$audit/exceptions_coverage.rpt")
                jtag_io_sha256=(Sha "$audit/io_audit/jtag_io.tsv")
                build_identity_sha256=(Sha $buildIdentityPath)
                completed_utc=[DateTime]::UtcNow.ToString('o')
            }
        }
        Report {
            & $Python "$repo/verification/fpga/report.py" --output $out
            if ($LASTEXITCODE -ne 0) { throw 'Verification report is not fully signed off; inspect report.md/report.json.' }
        }
    }
}
Push-Location $repo
try {
    if ($Stage -eq 'Run') {
        foreach ($step in @('Gdb','Rtl','Board','Recovery','Link','Audit','Report')) { Do-Stage $step }
    } else { Do-Stage $Stage }
} finally { Pop-Location }
