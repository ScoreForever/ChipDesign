"""Compare baseline Git ref and current SoC synthesis with identical constraints.

Uses a disposable ASCII snapshot for Vivado Windows; reports remain in work/.
This measures synthesis, not placement/routing, bitstream readiness or power.
"""
import argparse
from datetime import datetime
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--vivado', required=True, help='path to vivado.bat/executable')
    parser.add_argument('--baseline-ref', default='HEAD')
    parser.add_argument('--output', default='work/picorv32_area_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT) or output == ROOT or output.exists():
        raise ValueError('--output must be a new directory inside the repository')
    output.mkdir(parents=True)
    baseline = subprocess.check_output(['git', 'rev-parse', args.baseline_ref], cwd=ROOT, text=True).strip()
    (output / 'baseline_ref.txt').write_text(baseline + '\n')
    archive = subprocess.check_output(['git', 'archive', '--format=zip', baseline, 'hardware'], cwd=ROOT)
    for variant in ['baseline', 'picorv32']:
        destination = output / variant
        destination.mkdir()
        with tempfile.TemporaryDirectory(prefix='chipdesign-area-') as temporary:
            snapshot = Path(temporary)
            if variant == 'baseline':
                with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                    zipped.extractall(snapshot)
            else:
                # Only source files from this task; no generated data or Git internals.
                shutil.copytree(ROOT / 'hardware', snapshot / 'hardware',
                                ignore=shutil.ignore_patterns('__pycache__', 'out', 'sim_build', '*.vcd'))
            script = snapshot / 'synth.tcl'
            script.write_text('''set_param general.maxThreads 4
create_project -in_memory -part xc7a35tcsg324-1
set_property include_dirs {hardware/soc/rtl/include} [current_fileset]
set_property verilog_define {SYNTHESIS CHIPDESIGN_FPGA_MEM} [current_fileset]
set f [open hardware/soc/sim/filelists/chipdesign_soc.f r]
set sources {}
foreach line [split [read $f] "\\n"] {
    set line [string trim $line]
    if {$line eq "" || [string match "+*" $line] || [string match "hardware/soc/sim/*" $line]} {continue}
    lappend sources $line
}
close $f
read_verilog -sv $sources
synth_design -top my_soc_top -part xc7a35tcsg324-1
create_clock -period 20 [get_ports clk_i]
report_utilization -file utilization.rpt
report_utilization -hierarchical -file hierarchical.rpt
report_timing_summary -file timing.rpt
write_checkpoint -force synth.dcp
puts "PASS CPU MIGRATION SYNTHESIS"
exit
''')
            print(f'Synthesizing {variant}...', flush=True)
            try:
                result = subprocess.run([args.vivado, '-mode', 'batch', '-source', 'synth.tcl',
                    '-log', 'vivado.log', '-journal', 'vivado.jou'], cwd=snapshot,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', timeout=900)
                (destination / 'console.log').write_text(result.stdout, encoding='utf-8')
                if result.returncode or 'PASS CPU MIGRATION SYNTHESIS' not in result.stdout:
                    raise RuntimeError(f'{variant} synthesis failed; see {destination}')
            finally:
                for pattern in ['*.rpt', '*.log', '*.jou', '*.tcl', '*.dcp']:
                    for item in snapshot.glob(pattern):
                        shutil.copyfile(item, destination / item.name)
            print(f'PASS {variant} synthesis; reports: {destination}', flush=True)


if __name__ == '__main__':
    main()
