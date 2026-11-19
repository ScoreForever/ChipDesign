#!/usr/bin/env python3
"""Fixed 1980.000 um sealed-die True-MMMC Innovus + dual-corner SDF controller."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
OPT = HERE.parent / 'optimize'
for p in (HERE, OPT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tapeout_1980_geometry import compute_fixed_die_geometry, flow  # noqa: E402
import postlayout  # noqa: E402

DEFAULT_PDK = Path('/lec/pdk/tsmc180')
DEFAULT_COURSE = Path('/lec/lab4/soc_cv32e40p_template_pnr-stu')


def write_config_tcl(path: Path, paths: dict, sizes: dict, **options) -> dict:
    """Compute strict 1980 um geometry and emit Innovus config.tcl."""
    target_sealed_um = float(options.pop('target_sealed_um', 1980.0))
    geo = compute_fixed_die_geometry(sizes, target_sealed_um=target_sealed_um)
    values = dict(paths, **{k: v for k, v in geo.items() if k not in ('macros', 'pads')})
    values.update(
        threads=8,
        congestion=1,
        setup_target=0.45,
        hold_target=0.15,
        rc_setup_scale=1.15,
        rc_hold_scale=0.80,
    )
    values.update(options)
    text = ''.join(
        f'set {k} {v if isinstance(v, (int, float)) else flow.tcl(v)}\n'
        for k, v in values.items()
    )
    text += (
        'set macro_boxes {\n'
        + '\n'.join(' {' + ' '.join(map(str, m)) + '}' for m in geo['macros'])
        + '\n}\nset pads {\n'
    )
    for p in geo['pads']:
        fields = [p['name'], p['cell'], p['port'] or '-', p['direction'], p['side'], *p['loc'], p['orient']]
        text += ' {' + ' '.join(map(str, fields)) + '}\n'
    Path(path).write_text(text + '}\n')
    return geo


def write_artifact_hashes(pnr_dir: Path) -> dict[str, str]:
    """Record SHA-256 hashes of primary exported physical artifacts."""
    rel_paths = [
        'outputs/physical.v',
        'outputs/simulation.v',
        'outputs/mapped.sdc',
        'outputs/routed.spef.gz',
        'outputs/routed.sdf.gz',
        'outputs/routed.def',
        'outputs/chip.lef',
        'outputs/chip.gds.gz',
    ]
    hashes = {rel: flow.digest(pnr_dir / rel) for rel in rel_paths if (pnr_dir / rel).is_file()}
    flow.save(pnr_dir / 'artifact_sha256.json', hashes)
    return hashes


def run_dual_corner_sdf(pnr_dir: Path, libs: dict, *, array_cols: int = 8) -> dict:
    """Run gate-level SDF simulation at both SS 125C MAX and FF -40C MIN corners."""
    pnr_dir = pnr_dir.resolve()
    sim_v = pnr_dir / 'outputs' / 'simulation.v'
    testbench = OPT / 'tb_fullchip.sv'
    netlist = sim_v.read_text()
    cdc = postlayout.cdc_capture_paths(netlist)
    models = '\n'.join(Path(libs[k]).read_text(errors='replace') for k in ('std_verilog', 'io_verilog', 'macro_verilog'))
    ifnone = postlayout.ifnone_arcs(models)
    macro_text = Path(libs['macro_verilog']).read_text(errors='replace')
    sram_ok = postlayout.sram_period_ok(macro_text)

    corners = [
        ('ss125_max', pnr_dir / 'lab4_exports' / 'my_soc_top_ss125_max.sdf.gz', 'MAXIMUM'),
        ('ffm40_min', pnr_dir / 'lab4_exports' / 'my_soc_top_ffm40_min.sdf.gz', 'MINIMUM'),
    ]
    results = {}
    for label, sdf_gz, mtm in corners:
        if not sdf_gz.is_file():
            sdf_gz = pnr_dir / 'outputs' / 'routed.sdf.gz'
        out = pnr_dir / f'sdf_{label}'
        out.mkdir(parents=True, exist_ok=True)
        with gzip.open(sdf_gz, 'rt') as src:
            rewritten_sdf, n_rewritten = postlayout.sram_clk_q_noedge(src.read(), macro_text)
        sdf_path = out / f'{label}.sdf'
        sdf_path.write_text(rewritten_sdf)
        subprocess.run(
            [sys.executable, str(OPT / 'make_trace.py'), str(out / 'commands.txt'), str(array_cols)],
            check=True,
            cwd=flow.ROOT,
        )
        (out / 'cdc.tfile').write_text(''.join(f'PATH tb_fullchip.dut.{name}  -tcheck\n' for name in cdc))
        (out / 'sdf.cmd').write_text(
            f'SDF_FILE = "{sdf_path}",\nSCOPE = tb_fullchip.dut,\n'
            f'LOG_FILE = "sdf_annotation.log",\nMTM_CONTROL = "{mtm}";\n'
        )
        command = [
            'xrun', '-64bit', '-sv', '-timescale', '1ns/1ps', '-access', '+rwc', '-top', 'tb_fullchip',
            '-sdf_cmd_file', str(out / 'sdf.cmd'), '-tfile', str(out / 'cdc.tfile'),
            '+define+NTC', '+define+RECREM', '+neg_tchk',
            '-ignr_warnmax', 'SDFNEP', '-ignr_warnmax', 'SDFNET', '-ignr_warnmax', 'SDFNDP',
            '-sdfstats', 'sdf_stats.log',
            str(libs['std_verilog']), str(libs['io_verilog']), str(libs['macro_verilog']),
            str(sim_v), str(testbench),
            f'+SDF={sdf_path}', f'+TRACE={out / "commands.txt"}', '-l', 'simulation.log',
        ]
        state = flow.launch(command, out, 'PNR_OPT_JTAG_PASS', timeout=14400)
        state, time0_conflicts = postlayout.accept_time0_pad_conflicts(state)
        logs = '\n'.join(p.read_text(errors='replace') for p in out.glob('*.log'))
        clean, diagnostics = postlayout.classify(logs, ifnone, sram_ok)
        sdfndp_count = len(re.findall(r'(?m)^.*\*W,SDFNDP:.*$', logs))
        annotation = out / 'sdf_annotation.log'
        annotated = annotation.is_file() and bool(re.search(r'(?i)annotat', annotation.read_text(errors='replace')))
        jtag = re.search(r'PNR_OPT_JTAG_PASS commands=(\d+)', logs)
        corner_res = dict(
            corner=label,
            mtm_control=mtm,
            passed=state['status'] == 'complete' and clean and annotated and bool(jtag),
            jtag_commands=int(jtag[1]) if jtag else None,
            sram_clk_q_iopaths_unqualified=n_rewritten,
            accepted_time0_input_pad_conflicts=time0_conflicts,
            sdfndp_clamped_negative_iopaths=sdfndp_count,
            timing_diagnostics=not clean,
            diagnostics=diagnostics,
        )
        flow.save(out / 'result.json', corner_res)
        results[label] = corner_res
        if not corner_res['passed']:
            raise RuntimeError(f'Dual-corner SDF failed at {label}')

    summary = dict(passed=all(r['passed'] for r in results.values()), corners=results)
    flow.save(pnr_dir / 'dual_sdf_summary.json', summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pnr-dir', '--out-dir', dest='pnr_dir', type=Path, required=True)
    ap.add_argument('--netlist', type=Path, required=True)
    ap.add_argument('--sdc', type=Path, required=True)
    ap.add_argument('--pdk', type=Path, default=DEFAULT_PDK)
    ap.add_argument('--course', type=Path, default=DEFAULT_COURSE)
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--target-sealed-um', type=float, default=1980.0)
    ap.add_argument('--setup-target', type=float, default=0.45)
    ap.add_argument('--hold-target', type=float, default=0.15)
    ap.add_argument('--rc-setup-scale', type=float, default=1.15)
    ap.add_argument('--rc-hold-scale', type=float, default=0.80)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--only-dual-sdf', action='store_true')
    ap.add_argument('--run-dual-sdf', action='store_true')
    args = ap.parse_args()

    libs = flow.libraries(args.pdk, args.course)
    pnr_dir = args.pnr_dir.resolve()
    pnr_dir.mkdir(parents=True, exist_ok=True)

    if not args.only_dual_sdf:
        paths = dict(
            libs,
            opt_dir=OPT.resolve(),
            flow_dir=(HERE.parent / 'flow').resolve(),
            source_root=HERE.parents[2].resolve(),
            netlist=args.netlist.resolve(),
            sdc=args.sdc.resolve(),
            std_dir=libs['std_lib'].parent,
            io_dir=libs['io_lib'].parent,
            sram_dir=libs['macro_lib'].parent,
        )
        sizes = flow.lef_sizes([libs[k] for k in ('macro_lef', 'io_lef', 'bond_lef')])
        write_config_tcl(
            pnr_dir / 'config.tcl',
            paths,
            sizes,
            threads=args.threads,
            target_sealed_um=args.target_sealed_um,
            setup_target=args.setup_target,
            hold_target=args.hold_target,
            rc_setup_scale=args.rc_setup_scale,
            rc_hold_scale=args.rc_hold_scale,
        )
        if args.dry_run:
            return
        with (pnr_dir / 'console.log').open('w') as log:
            subprocess.run(
                ['innovus', '-no_gui', '-files', str(HERE / 'tapeout_1980_pnr.tcl')],
                cwd=pnr_dir,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        console_txt = (pnr_dir / 'console.log').read_text(errors='replace')
        if 'STAGE_TAPEOUT_1980_COMPLETE' not in console_txt:
            raise RuntimeError('Missing STAGE_TAPEOUT_1980_COMPLETE marker in console.log')
        for export_dir in (pnr_dir / 'outputs', pnr_dir / 'lab4_exports'):
            if export_dir.is_dir():
                for gz_path in sorted(export_dir.glob('*.gz')):
                    subprocess.run(['gzip', '-t', str(gz_path)], check=True)
        write_artifact_hashes(pnr_dir)

    if args.run_dual_sdf or args.only_dual_sdf:
        for export_dir in (pnr_dir / 'outputs', pnr_dir / 'lab4_exports'):
            if export_dir.is_dir():
                for gz_path in sorted(export_dir.glob('*.gz')):
                    subprocess.run(['gzip', '-t', str(gz_path)], check=True)
        if not (pnr_dir / 'artifact_sha256.json').is_file():
            write_artifact_hashes(pnr_dir)
        run_dual_corner_sdf(pnr_dir, libs)


if __name__ == '__main__':
    main()
