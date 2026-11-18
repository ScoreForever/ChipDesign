#!/usr/bin/env python3
"""Measure inference-window gate activity and TT Innovus power on private copies."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE.parent/'optimize'), str(ROOT/'hardware/npu/scripts'), str(ROOT/'software/host')]
from flow import libraries, tcl
from run_program_regression import fixtures, Recorder, encoded
from npu_program import compile_model, reference, Package, ABI
from nocpu_jtag import Npu


def sha256_file(path):
    """Compute hex SHA-256 without relying on Python 3.11's hashlib.file_digest."""
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run(command, directory, name):
    """Retain tool stdout and require a successful process exit."""
    with (directory/name).open('w') as log:
        subprocess.run(list(map(str, command)), cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, check=True)
    return (directory/name).read_text(errors='replace')


def power(path):
    """Parse total power and Innovus Group breakdown values, normalized to mW."""
    text = path.read_text(errors='replace')
    unit_match = re.search(r'Power Units\s*=\s*1\s*(W|mW|uW|nW)', text)
    scale_map = {'W': 1000.0, 'mW': 1.0, 'uW': 0.001, 'nW': 1e-6}
    default_scale = scale_map.get(unit_match[1], 1.0) if unit_match else 1.0
    result = {}
    num_re = r'([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)'
    for key, label in [('internal_mw','Total Internal Power'), ('switching_mw','Total Switching Power'), ('leakage_mw','Total Leakage Power'), ('total_mw','Total Power')]:
        match = re.search(re.escape(label)+r'\s*[:=]\s*'+num_re+r'\s*(mW|uW|nW|W)?', text)
        if match:
            result[key] = float(match[1])*(scale_map[match[2]] if match[2] else default_scale)
    groups = {}
    for label, key in [('Macro','macro_mw'), ('Sequential','sequential_mw'), ('Combinational','combinational_mw'), ('IO','io_mw'), ('Clock (Combinational)','clock_comb_mw'), ('Clock (Sequential)','clock_seq_mw')]:
        m = re.search(r'(?m)^' + re.escape(label) + r'\s+' + num_re + r'\s+' + num_re + r'\s+' + num_re + r'\s+' + num_re, text)
        if m:
            groups[key] = float(m[4]) * default_scale
    if groups:
        result['macro_mw'] = groups.get('macro_mw', 0.0)
        result['sequential_mw'] = groups.get('sequential_mw', 0.0)
        result['combinational_mw'] = groups.get('combinational_mw', 0.0)
        result['io_mw'] = groups.get('io_mw', 0.0)
        result['clock_mw'] = groups.get('clock_comb_mw', 0.0) + groups.get('clock_seq_mw', 0.0)
    result['activity_percent'] = None
    if 'total_mw' not in result:
        raise ValueError('Unrecognized power report: '+str(path))
    return result


def resolve_busy_probe(netlist):
    """Resolve NPU busy signal path across flat or hierarchical simulation netlists."""
    for n in ('i_control_busy', 'i_control_running'):
        if re.search(r'\b' + n + r'\b', netlist):
            return n
    if re.search(r'\bnpu_control_subsystem\w*\s+i_control\s*\(', netlist) and re.search(r'\.busy\(\s*busy\s*\)', netlist):
        return 'i_control.busy'
    raise RuntimeError('Cannot resolve busy probe; inspect simulation.v')


def workload(name):
    """Use the fixture oracle or a packaged zoo model's first golden frame."""
    if name in ('cnn_f1','cnn_f0'):
        _, model, frame = next(x for x in fixtures() if x[0]=='cnn')
        package = compile_model(model, cols=8, fuse=name=='cnn_f1')
    else:
        if name not in ('ecg','har','mnist_l','fashion_cnn','kws_m','kws_l','gtsrb_l'):
            raise ValueError('Unknown workload '+name)
        directory = ROOT/'software/zoo/pretrained'/name
        model = json.loads((directory/'model.json').read_text())
        frame = json.loads((directory/'golden.json').read_text())['frames'][0]['input']
        package = Package.load(directory/'c8')
    expected = encoded(reference(model, frame)[model['nodes'][-1]['output']], package.manifest['output_dtype'])
    recorder = Recorder(package, expected)
    host = Npu(recorder)
    host.load_model(package)
    assert host.infer(encoded(frame, model['input'].get('dtype','i4')))==expected
    recorder.rows += ['01 00000038 0 0 0 0', '09 0 0 0 0 0', f'01 0 0 0 ffffffff {ABI:08x}']
    return '\n'.join(recorder.rows)+'\n'


def main():
    """Compile once, simulate serially, then run one Innovus power session."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidate', required=True, type=Path)
    ap.add_argument('--out-dir', required=True, type=Path)
    ap.add_argument('--models', default='cnn_f1,cnn_f0,ecg,har,mnist_l')
    ap.add_argument('--keep-vcd', action='store_true')
    ap.add_argument('--summarize-existing', action='store_true')
    args = ap.parse_args()
    cand, out = args.candidate.resolve(), args.out_dir.resolve()
    if json.loads((cand/'sdf/result.json').read_text()).get('passed') is not True:
        raise RuntimeError('Accepted SDF run required')
    models = args.models.split(',')
    if not args.summarize_existing:
        out.mkdir(parents=True, exist_ok=False)
        (out/'work').mkdir()
        shutil.copytree(cand/'final.enc.dat', out/'work/final.enc.dat')
        netlist = (cand/'outputs/simulation.v').read_text()
        busy = resolve_busy_probe(netlist)
        (out/'sdf_power.cmd').write_text(f'SDF_FILE = "{cand}/sdf/routed.sdf",\nSCOPE = tb_fullchip_power.dut,\nLOG_FILE = "sdf_annotation.log",\nMTM_CONTROL = "MAXIMUM";\n')
        (out/'cdc_power.tfile').write_text((cand/'sdf/cdc.tfile').read_text().replace('tb_fullchip.', 'tb_fullchip_power.'))
        libs = libraries()
        cache = out/'xcelium.d'
        run(['xrun','-64bit','-elaborate','-sv','-timescale','1ns/1ps','-access','+rwc','-top','tb_fullchip_power','-xmlibdirname',cache,
             '-sdf_cmd_file',out/'sdf_power.cmd','-tfile',out/'cdc_power.tfile','+define+NTC','+define+RECREM',
             '+define+BUSY_PROBE=dut.'+busy,'+neg_tchk','-ignr_warnmax','SDFNEP','-ignr_warnmax','SDFNET',
             *[libs[k] for k in ('std_verilog','io_verilog','macro_verilog')],
             cand/'outputs/simulation.v',HERE/'tb_fullchip_power.sv'],out,'elaboration.log')
    prior_hashes = {}
    prior_path = out/'power_summary.json'
    if args.summarize_existing and prior_path.is_file():
        prior_data = json.loads(prior_path.read_text())
        for item in prior_data.get('workloads', []):
            if item.get('name') and item.get('vcd_sha256'):
                prior_hashes[item['name']] = item['vcd_sha256']
    rows = []
    for name in models:
        directory = out/name
        vcd = directory/'activity.vcd'
        hash_file = directory/'vcd.sha256'
        if not args.summarize_existing:
            directory.mkdir()
            (directory/'commands.txt').write_text(workload(name))
            log = run(['xrun','-64bit','-R','-xmlibdirname',cache,'+neg_tchk',f'+VCD={vcd}', '+TARGET_START=1',
                       f'+SDF={cand}/sdf/routed.sdf',f'+TRACE={directory}/commands.txt'],directory,'simulation.log')
        else:
            log = (directory/'simulation.log').read_text(errors='replace')
        window = re.search(r'WEEK3_POWER_WINDOW busy_ns=([\d.]+) irq_ns=([\d.]+) dt_ns=([\d.]+) core_edges=(\d+) nominal_ns=([\d.]+)',log)
        cycles = re.search(r'WEEK3_CYCLES cycles=(\d+)',log)
        if not window or not cycles or 'PNR_OPT_JTAG_PASS' not in log:
            raise RuntimeError('Missing simulation evidence: '+name)
        row = dict(zip(('busy_ns','irq_ns','dt_ns','core_edges','nominal_ns'),map(float,window.groups())))
        if vcd.is_file():
            vcd_hash = sha256_file(vcd)
            hash_file.write_text(vcd_hash + '\n')
        elif args.summarize_existing and hash_file.is_file() and hash_file.read_text().strip():
            vcd_hash = hash_file.read_text().strip()
        elif args.summarize_existing and name in prior_hashes:
            vcd_hash = prior_hashes[name]
        else:
            raise RuntimeError(f'Missing VCD and recorded SHA-256 for {name}; refusing to overwrite provenance')
        row.update(name=name,npu_cycles=int(cycles[1]),vcd_sha256=vcd_hash)
        if row['core_edges']<row['npu_cycles'] or row['dt_ns']<=0:
            raise RuntimeError('Invalid inference window')
        rows.append(row)
    inv = out/'innovus'
    if not args.summarize_existing:
        inv.mkdir()
        script = [f'restoreDesign {tcl(out/"work/final.enc.dat")} my_soc_top',
                  'set_analysis_view -setup {func_tt_io} -hold {func_tt_io}', 'extractRC',
                  'set_power_analysis_mode -reset',
                  'set_power_analysis_mode -method static -corner max -create_binary_db false -write_static_currents false -ignore_control_signals true']
        for name, row in [('vectorless',None)]+[(r['name'],r) for r in rows]:
            if row:
                script += ['read_activity_file -reset',f'read_activity_file -format VCD -scope tb_fullchip_power.dut -start {row["busy_ns"]}ns -end {row["irq_ns"]}ns {tcl(out/name/"activity.vcd")}']
            script += [f'report_power -view func_tt_io -outfile power_{name}.rpt']
        script += ['puts WEEK3_POWER_DONE','exit']
        (inv/'power.tcl').write_text('\n'.join(script)+'\n')
        log = run(['innovus','-no_gui','-files',inv/'power.tcl'],inv,'console.log')
    else:
        log = (inv/'console.log').read_text(errors='replace')
    if 'WEEK3_POWER_DONE' not in log:
        raise RuntimeError('Innovus did not finish')
    baseline = power(inv/'power_vectorless.rpt')
    if 'macro_mw' not in baseline:
        raise RuntimeError('Missing macro power breakdown for vectorless baseline')
    inv_log = (inv/'console.log').read_text(errors='replace')
    for row in rows:
        parsed = power(inv/f'power_{row["name"]}.rpt')
        if parsed.get('activity_percent') is None:
            blk = re.search(re.escape(f'{row["name"]}/activity.vcd') + r'(.*?)(?=Using Power View:|report_power\b|$)', inv_log, re.S)
            if blk:
                am = re.search(r'(?:Total annotation coverage for all files of type VCD\s*:\s*\d+/\d+\s*=\s*([\d.]+)%|Total Nets\s*:\s*\d+/\d+\s*=\s*([\d.]+)%)', blk[1], re.I)
                if am:
                    parsed['activity_percent'] = float(next(g for g in am.groups() if g is not None))
                zm = re.search(r'Percent of VCD annotated nets with zero toggles\s*:\s*\d+/\d+\s*=\s*([\d.]+)%', blk[1], re.I)
                if zm:
                    parsed['zero_toggle_percent'] = float(zm[1])
        macro_rpt = inv/f'power_{row["name"]}_macro.rpt'
        if 'macro_mw' not in parsed:
            if macro_rpt.is_file():
                parsed['macro_mw'] = power(macro_rpt)['total_mw']
            else:
                raise RuntimeError(f'Missing macro power breakdown for {row["name"]}')
        row.update(parsed)
        row['energy_uJ'] = row['total_mw']*row['dt_ns']*1e-6
        if not args.keep_vcd and (out/row['name']/'activity.vcd').is_file():
            (out/row['name']/'activity.vcd').unlink()
    energies = {r['name']:r['energy_uJ'] for r in rows}
    savings = 100*(1-energies['cnn_f1']/energies['cnn_f0']) if energies.get('cnn_f0') else None
    (out/'power_summary.json').write_text(json.dumps(dict(vectorless=baseline,workloads=rows,cnn_energy_savings_percent=savings),indent=2)+'\n')
    with (out/'power_workloads.csv').open('w') as f:
        writer = csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


if __name__=='__main__':
    main()
