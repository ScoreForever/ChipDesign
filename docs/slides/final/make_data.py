#!/usr/bin/env python3
"""Generate every number used by main.tex from verified repository records.

Pure standard library (json, csv, shutil, pathlib, re, statistics); no plotting
package is needed because all charts are drawn by pgfplots from data/*.dat.

Outputs:
  data/numbers.tex   \\newcommand macros (letters-only names)
  data/*.dat         pgfplots tables
  data/*.tex         small, complete LaTeX tabular snippets
  figs/*.png         layout / Calibre images copied from the EDA archive
"""
from __future__ import annotations

import csv
import subprocess
import json
import re
import shutil
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
EDA = REPO / 'docs/validation/eda_final_coopt_1980_20261121'
FPGA = REPO / 'docs/validation/fpga_final_live_20261121'
RTL_CORE = REPO / 'hardware/npu/rtl/npu_program_core.sv'
RTL_MAC = REPO / 'hardware/npu/rtl/npu_mac_array.sv'
RTL_JTAG = REPO / 'hardware/soc/rtl/control/npu_jtag_transport.sv'
HOST = REPO / 'software/host/nocpu_jtag.py'
HOST_ABI = REPO / 'software/host/npu_program.py'
TILER = REPO / 'software/compiler/tiler.py'
DATA = HERE / 'data'
FIGS = HERE / 'figs'

IMAGES = ['1_placement.png', '2_power.png', '8_lab5_sealring_corner.png',
          '9_lab5_dummy_fill_zoom.png', '10_lab5_wirebond_keepout_zoom.png',
          '11_lab5_cor4_and_erc_audit.png']

MACROS: dict[str, str] = {}


# ----------------------------------------------------------------- helpers
def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path: Path, delim=','):
    with path.open(newline='', encoding='utf-8') as fh:
        return list(csv.DictReader(fh, delimiter=delim))


def grab(pattern: str, text: str, group=1, flags=0) -> str:
    m = re.search(pattern, text, flags)
    if not m:
        raise SystemExit(f'pattern not found: {pattern!r}')
    return m.group(group)


def tex(s) -> str:
    """Escape a plain string for LaTeX text mode."""
    s = str(s)
    rep = {'\\': r'\textbackslash{}', '_': r'\_', '%': r'\%', '&': r'\&', '#': r'\#',
           '$': r'\$', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\^{}'}
    return ''.join(rep.get(c, c) for c in s)


def i(n) -> str:
    return f'{int(round(n)):,}'


def f(x, d=2) -> str:
    return f'{x:,.{d}f}'


def pct(a, b, d=2) -> str:
    return f(100.0 * a / b, d)


def M(name: str, value) -> None:
    if not re.fullmatch(r'[A-Za-z]+', name):
        raise SystemExit(f'illegal macro name {name!r}')
    if name in MACROS:
        raise SystemExit(f'duplicate macro {name!r}')
    MACROS[name] = str(value)


def B(flag: bool) -> str:
    return 'true' if flag else 'false'


def write(name: str, text: str) -> None:
    (DATA / name).write_text(text, encoding='utf-8')


def tabular(spec: str, header: list[str], body: list[list[str]], extra_mid=None) -> str:
    out = [r'\begin{tabular}{' + spec + '}', r'\toprule', ' & '.join(header) + r' \\', r'\midrule']
    for k, r in enumerate(body):
        if extra_mid and k in extra_mid:
            out.append(r'\midrule')
        out.append(' & '.join(r) + r' \\')
    out += [r'\bottomrule', r'\end{tabular}']
    return '\n'.join(out) + '\n'


# ----------------------------------------------------------------- RTL / host sources
def sources():
    core = RTL_CORE.read_text(encoding='utf-8')
    mac = RTL_MAC.read_text(encoding='utf-8')
    jt = RTL_JTAG.read_text(encoding='utf-8')
    host = HOST.read_text(encoding='utf-8')
    tiler = TILER.read_text(encoding='utf-8')
    abi = int(grab(r'^ABI\s*=\s*(0x[0-9a-fA-F]+)', HOST_ABI.read_text(encoding='utf-8'), flags=re.M), 16)
    M('NAbi', f'{abi >> 16}.{(abi >> 8) & 0xff}')

    M('NOpAdd', grab(r'\bADD\s*=\s*(\d+)\s*;', core))
    M('NRows', grab(r'\bROWS\s*=\s*(\d+)', core))
    M('NPsumBits', grab(r'PSUM_BITS\s*=\s*(\d+)', core))
    M('NWriteDepth', grab(r'localparam\s+WRITE_DEPTH\s*=\s*(\d+)', core))
    reset_line = grab(r'(ret2_valid<=0;[^\n]*ret2_pos_addr<=0;[^\n]*)', core)
    M('NRetTwoResetRegs', len(re.findall(r'ret2_\w+<=0', reset_line)))
    M('NMacDspFpga', B('use_dsp = "yes"' in mac and 'CHIPDESIGN_FPGA_MEM' in mac))
    M('NMacProdBits', grab(r'wire signed \[(\d+):0\] product', mac, 1))

    M('NJtagPacketBits', grab(r'IR=12h shifts (\d+)-bit packets', jt))
    M('NSyncStages', int(grab(r'reg \[(\d+):0\] req_sync', jt)) + 1)
    M('NJtagIdcode', str(int(grab(r"IDCODE\s*=\s*32'h([0-9A-Fa-f_]+)", jt).replace('_', ''), 16)))

    tight = float(grab(r'_TIGHT_POLL_S\s*=\s*([0-9.]+)', host))
    sleep = float(grab(r'_POLL_SLEEP_S\s*=\s*([0-9.]+)', host))
    M('NTightPollMs', f'{tight * 1e3:g}')
    M('NPollSleepMs', f'{sleep * 1e3:g}')
    wchunk = int(grab(r'write_chunk_words=(\d+)', host))
    rchunk = int(grab(r'read_chunk_words=(\d+)', host))
    fifo = int(grab(r'FT232H has a (\d+) B hardware RX FIFO', host))
    bpw = int(grab(r'queues (\d+) RX bytes per', host))
    # Pre-fix default comes from the parent of the FIFO-fix commit recorded in the FPGA archive.
    fix = grab(r"`(?:resnet20|resnet18)/`[^\n]*?commit `([0-9a-f]+)` \(after PR #31", (FPGA / 'README.md').read_text(encoding='utf-8'))
    old_src = subprocess.run(['git', '-C', str(REPO), 'show', f'{fix}^:software/host/nocpu_jtag.py'],
                             check=True, capture_output=True, text=True).stdout
    old_r = int(grab(r'read_chunk_words=(\d+)', old_src))
    M('NFixCommit', fix)
    M('NOldReadChunk', old_r)
    M('NOldReadChunkBytes', i(old_r * bpw))
    M('NOldReadChunkBytesRaw', old_r * bpw)
    M('NWriteChunk', wchunk)
    M('NReadChunk', rchunk)
    M('NFifoBytes', i(fifo))
    M('NFifoBytesRaw', fifo)
    M('NBytesPerWord', bpw)
    M('NReadChunkBytes', rchunk * bpw)
    M('NSafeMaxWords', fifo // bpw)
    M('NFifoHeadroom', pct(rchunk * bpw, fifo, 1))
    geom = grab(r'_IO_GEOMETRY_REGS\s*=\s*\(([^)]*)\)', host)
    M('NGeomRegs', len([g for g in geom.split(',') if g.strip()]))
    M('NIdleTckMin', grab(r'self\.idle_tcks = max\((\d+),', host))
    M('NBurstSysCycles', grab(r'burst_read_sys_cycles=(\d+)', host))
    sram_bytes = int(grab(r'^SRAM_BYTES\s*=\s*(\d+)', tiler, flags=re.M))
    M('NSramBytes', i(sram_bytes))
    M('NSramKB', sram_bytes // 1024)


# ----------------------------------------------------------------- ASIC archive
def asic():
    met = load(EDA / 'metrics.json')
    syn = load(EDA / 'synth_area.json')
    cells = load(EDA / 'final_cells.json')
    ab = load(EDA / 'area_breakdown.json')
    lab5 = load(EDA / 'lab5_summary.json')
    erc = load(EDA / 'final_erc_geometry.json')
    so = load(EDA / 'signoff_summary.json')
    sdf = load(EDA / 'dual_sdf_summary.json')
    pw = load(EDA / 'power_summary.json')
    man = load(EDA / 'delivery_manifest.json')
    readme = (EDA / 'README.md').read_text(encoding='utf-8')

    clk = grab(r'`(\d+) MHz` core / `([0-9.]+) ns` period', readme)
    M('NAsicClk', clk)
    M('NAsicPeriod', grab(r'`(\d+) MHz` core / `([0-9.]+) ns` period', readme, 2))
    M('NAsicCommit', grab(r'Source RTL Commit\*\*: `([0-9a-f]+)`', readme))
    asic_hz = float(clk) * 1e6

    # Architecture parameters from the synthesized module names.
    mods = ab['comb_by_module']
    core_key = next(k for k in mods if k.startswith('npu_program_core_'))
    M('NCols', grab(r'COLS(\d+)', core_key))
    M('NActBytes', i(int(grab(r'ACT_BYTES(\d+)', core_key))))
    M('NModelBytes', i(int(grab(r'MODEL_BYTES(\d+)', core_key))))
    M('NMaxOps', grab(r'MAX_OPS(\d+)', core_key))
    M('NMaxK', i(int(grab(r'MAX_K(\d+)', core_key))))

    # Die geometry
    raw = lab5['raw_die_bbox_um']
    sealed = lab5['actual_postdummy_size_um']
    M('NActiveW', f(raw[2] - raw[0], 2))
    M('NActiveH', f(raw[3] - raw[1], 2))
    M('NSealW', f(sealed[0], 1))
    M('NSealH', f(sealed[1], 1))
    M('NSealMax', f(lab5['max_allowed_sealed_die_um'], 1))
    M('NSealInt', i(sealed[0]))
    M('NDbu', i(lab5['dbu_per_um']))
    M('NDieMm', f(met['die_area_mm2'], 3))
    M('NCoreMm', f(met['core_area_mm2'], 3))
    M('NStdCells', i(cells['active_standard_instances']))
    M('NStdArea', f(cells['active_standard_area_um2'] / 1e6, 3))
    M('NMacroArea', f(cells['macro_area_um2'] / 1e6, 3))
    M('NMacroCount', cells['macro_count'])
    M('NSynthTotal', f(syn['total_area_um2'] / 1e6, 3))
    M('NSynthStd', f(syn['standard_cell_area_um2'] / 1e6, 3))
    M('NCtsRepair', f(met['cts_and_repair_area_change_um2'] / 1e3, 1))
    M('NCtsRepairPct', pct(cells['active_standard_area_um2'] - syn['standard_cell_area_um2'],
                           syn['standard_cell_area_um2'], 1))
    placed = cells['active_standard_area_um2'] + cells['macro_area_um2']
    core_um2 = met['core_area_mm2'] * 1e6
    M('NCoreUtil', pct(placed, core_um2, 1))
    M('NMacroShareCore', pct(cells['macro_area_um2'], core_um2, 1))
    M('NStdShareCore', pct(cells['active_standard_area_um2'], core_um2, 1))
    sram = lab5['checks']['lvs']['sram_cell']
    M('NSramMacro', tex(sram))
    words, bits = (int(x) for x in re.match(r'RA1SHD_(\d+)x(\d+)', sram).groups())
    M('NSramMacroKB', words * bits // 8 // 1024)

    # Synthesis breakdown (flop/comb/macro) + top modules
    synth_std = ab['flop_area_um2'] + ab['comb_area_um2']
    M('NFlopArea', f(ab['flop_area_um2'] / 1e6, 3))
    M('NCombArea', f(ab['comb_area_um2'] / 1e6, 3))
    tot = synth_std + ab['macro_area_um2']
    M('NFlopPct', pct(ab['flop_area_um2'], tot, 1))
    M('NCombPct', pct(ab['comb_area_um2'], tot, 1))
    M('NMacroPct', pct(ab['macro_area_um2'], tot, 1))
    write('area_split.dat', 'idx part area\n'
          f"0 Macro {ab['macro_area_um2'] / 1e6:.4f}\n"
          f"1 Flop {ab['flop_area_um2'] / 1e6:.4f}\n"
          f"2 Comb {ab['comb_area_um2'] / 1e6:.4f}\n")
    top = sorted(mods.items(), key=lambda kv: -kv[1])[:4]
    body = []
    for name, area in top:
        short = re.sub(r'_[A-Z].*$', '', name)
        body.append([r'\texttt{' + tex(short) + '}', f(area / 1e3, 1), pct(area, ab['comb_area_um2'], 1) + r'\%'])
    write('area_modules.tex', tabular('@{}lrr@{}', [r'\textbf{Module (comb.)}', r'\textbf{k\textmu m²}', r'\textbf{share}'], body))
    flops = ab['flops_by_register']
    psum = next(v for k, v in flops.items() if k.endswith(':psum'))
    M('NPsumFlops', i(psum['count']))
    M('NPsumFlopPct', pct(psum['area_um2'], ab['flop_area_um2'], 1))
    tag = next(v for k, v in flops.items() if k.endswith('cache_tag'))
    M('NCacheTagFlops', tag['count'])
    ret2 = next(v for k, v in flops.items() if k.endswith(':ret2_reduced'))
    M('NRetTwoFlops', ret2['count'])

    # MMMC corners
    corners = rows(EDA / 'mmmc_corners.csv')
    order = ['ss125', 'tt25', 'ff0', 'ffm40']
    by = {(r['corner'], r['mode']): r for r in corners}
    lines = ['idx corner setup hold']
    for k, c in enumerate(order):
        lines.append(f"{k} {c} {float(by[(c, 'setup')]['wns']):.3f} {float(by[(c, 'hold')]['wns']):.3f}")
    write('mmmc.dat', '\n'.join(lines) + '\n')
    for c, nm in zip(order, ['SS', 'TT', 'FF', 'FFM']):
        M(f'N{nm}Setup', f(float(by[(c, 'setup')]['wns']), 3))
        M(f'N{nm}Hold', f(float(by[(c, 'hold')]['wns']), 3))
    viol = sum(int(r['violating_endpoints']) for r in corners)
    tns = sum(abs(float(r['tns'])) for r in corners)
    M('NMmmcViol', viol)
    M('NMmmcTns', f(tns, 1))
    M('NMmmcViews', len(corners))
    M('NCornerCount', len(order))
    ss_fmax = 1e3 / (1e3 / float(clk) - float(by[('ss125', 'setup')]['wns']))
    M('NSSFmax', f(ss_fmax, 2))
    M('NSSPathStart', tex(by[('ss125', 'setup')]['start'].split('/')[-2]))

    rc = rows(EDA / 'rc_sensitivity.tsv', '\t')
    body = []
    for r in rc:
        body.append([r'\texttt{' + r['corner'] + '}', r['mode'], f"{float(r['rc_scale']):.2f}×",
                     f(float(r['wns_ns']), 3), r['violating_endpoints']])
    write('rc_table.tex', tabular('@{}llrrr@{}', [r'\textbf{Corner}', r'\textbf{Check}', r'\textbf{RC}',
                                                 r'\textbf{WNS (ns)}', r'\textbf{Viol.}'], body))
    M('NRcViol', sum(int(r['violating_endpoints']) for r in rc))
    scales = [float(r['rc_scale']) for r in rc]
    M('NRcMax', f'{max(scales):.2f}')
    M('NRcMin', f'{min(scales):.2f}')
    M('NRcPct', f'{round((max(scales) - 1) * 100):d}')
    ssr = {float(r['rc_scale']): float(r['wns_ns']) for r in rc if r['corner'] == 'ss125'}
    fmr = {float(r['rc_scale']): float(r['wns_ns']) for r in rc if r['corner'] == 'ffm40'}
    M('NRcSSWorst', f(ssr[max(ssr)], 3))
    M('NRcFFMWorst', f(fmr[min(fmr)], 3))

    st = rows(EDA / 'sram_interface_timing.csv')
    body = []
    for r in st:
        body.append([r'\texttt{' + r['corner'] + '}', r['role'], f(float(r['setup_to']), 3), f(float(r['hold_to']), 3),
                     f(float(r['setup_from']), 3), f(float(r['hold_from']), 3)])
    write('sram_table.tex', tabular('@{}llrrrr@{}', [r'\textbf{Corner}', r'\textbf{Bank}', r'\textbf{Su→M}', r'\textbf{Ho→M}',
                                                    r'\textbf{Su M→}', r'\textbf{Ho M→}'], body))
    allv = [float(r[k]) for r in st for k in ('setup_to', 'hold_to', 'setup_from', 'hold_from')]
    M('NSramMinSlack', f(min(allv), 3))
    M('NSramPaths', len(st))

    # PrimeTime / Formality
    sta = so['results']['sta']
    lec = so['results']['lec']
    M('NStaSetup', f(sta['setup_wns_ns'], 3))
    M('NStaHold', f(sta['hold_wns_ns'], 3))
    M('NStaPass', B(sta['passed']))
    M('NStaUnannot', B(sta['unannotated']))
    M('NLecPass', 'PASS' if lec['passed'] and lec['verified'] else 'FAIL')
    M('NStaBlackBox', tex(sta['black_boxes'][0].rstrip('.')))

    # Dual-corner SDF
    cs = sdf['corners']
    ss, fm = cs['ss125_max'], cs['ffm40_min']
    M('NSdfPass', 'PASS' if sdf['passed'] and ss['passed'] and fm['passed'] else 'FAIL')
    M('NSdfJtag', ss['jtag_commands'])
    M('NSdfJtagFm', fm['jtag_commands'])
    M('NSdfTimingDiag', B(ss['timing_diagnostics'] or fm['timing_diagnostics']))
    M('NSdfClampFm', i(fm['sdfndp_clamped_negative_iopaths']))
    M('NSdfClampSs', i(ss['sdfndp_clamped_negative_iopaths']))
    M('NSdfSramUnq', ss['sram_clk_q_iopaths_unqualified'])
    M('NSdfIfnone', i(ss['diagnostics']['accepted_ifnone_paths']['total']))
    M('NSdfNegedge', ss['diagnostics']['accepted_sram_negedge_period'])
    M('NSdfTimeZero', ss['accepted_time0_input_pad_conflicts'])
    M('NSdfFatal', len(ss['diagnostics']['fatal']) + len(fm['diagnostics']['fatal']))
    M('NSdfMtmSs', tex(ss['mtm_control']))
    M('NSdfMtmFm', tex(fm['mtm_control']))

    # Calibre
    ck = lab5['checks']
    drc = ck['drc']
    M('NDrcRules', drc['rules_executed'])
    M('NDrcBlocking', len(drc['blocking']))
    M('NDrcRawHier', i(drc['raw_total_hier']))
    M('NDrcRawExp', i(drc['raw_total_expanded']))
    M('NDrcTeachCats', len(drc['accepted_teaching_categories']))
    M('NAntRules', ck['antenna']['rules_executed'])
    M('NAntViol', ck['antenna']['total_expanded'])
    M('NWbRules', ck['wirebond']['rules_executed'])
    M('NWbViol', ck['wirebond']['total_expanded'])
    lvs = ck['lvs']
    tol = lvs['pr_3_48pct']
    zero = lvs['pr_0pct']
    M('NLvsStatus', tol['status'])
    M('NLvsTol', f(tol['r_pr_tolerance_pct'], 2))
    M('NLvsZeroStatus', zero['status'])
    M('NLvsZeroErr', zero['property_error_count'])
    M('NLvsMaxDev', f(zero['max_io_resistor_deviation_pct'], 2))
    M('NLvsPorts', len(lvs['top_ports']))
    M('NLvsSramBlack', B(lvs['sram_blackboxed']))
    cor = lab5['cor4_pimp_patch']
    M('NCorCell', tex(cor['cell']))
    M('NCorPatch', ', '.join(f(v, 3) for v in cor['patch_pp_um']))
    M('NCorNpWall', f(cor['adjacent_np_wall_um'], 2))
    M('NFillLayers', len(lab5['dummy_config']['fill_layers']))
    M('NFillLayerList', tex('/'.join(lab5['dummy_config']['fill_layers'])))
    e = ck['erc']
    M('NErcHier', e['total_hier'])
    M('NErcExp', i(e['total_expanded']))
    M('NErcRules', e['rules_executed'])
    M('NErcStatus', tex(e['status']))
    M('NErcMarkerRules', len(erc['rules']))
    M('NFormalTapeout', B(lab5['formal_tapeout_signoff']))

    # Power
    v = pw['vectorless']
    M('NVecPower', f(v['total_mw'], 2))
    wl = {w['name']: w for w in pw['workloads']}
    order = ['cnn_f1', 'cnn_f0', 'ecg', 'har', 'mnist_l']
    lines = ['idx name internal switching leakage total energy cycles']
    for k, n in enumerate(order):
        w = wl[n]
        lines.append(f"{k} {{\\texttt{{{tex(n)}}}}} {w['internal_mw']:.4f} {w['switching_mw']:.4f} {w['leakage_mw']:.4f} "
                     f"{w['total_mw']:.4f} {w['energy_uJ']:.5f} {w['npu_cycles']}")
    write('power.dat', '\n'.join(lines) + '\n')
    M('NPowerLabels', ','.join(r'\texttt{' + tex(n) + '}' for n in order))
    M('NFuseSave', f(pw['cnn_energy_savings_percent'], 2))
    f1, f0 = wl['cnn_f1'], wl['cnn_f0']
    M('NFOneE', f(f1['energy_uJ'], 3))
    M('NFZeroE', f(f0['energy_uJ'], 3))
    M('NFOneP', f(f1['total_mw'], 2))
    M('NFZeroP', f(f0['total_mw'], 2))
    M('NFOneCyc', i(f1['npu_cycles']))
    M('NFZeroCyc', i(f0['npu_cycles']))
    M('NFuseCycPct', pct(f0['npu_cycles'] - f1['npu_cycles'], f0['npu_cycles'], 1))
    M('NVcdAct', f(min(w['activity_percent'] for w in pw['workloads']), 0))
    M('NVcdCount', len(pw['workloads']))
    tots = [w['total_mw'] for w in pw['workloads']]
    M('NPowMin', f(min(tots), 2))
    M('NPowMax', f(max(tots), 2))
    M('NLeakUw', f(statistics.mean(w['leakage_mw'] for w in pw['workloads']) * 1e3, 1))
    for n, nm in (('ecg', 'Ecg'), ('har', 'Har'), ('mnist_l', 'Mnist')):
        M(f'NAsic{nm}P', f(wl[n]['total_mw'], 2))
        M(f'NAsic{nm}E', f(wl[n]['energy_uJ'], 2))
        M(f'NAsic{nm}Us', f(wl[n]['npu_cycles'] / asic_hz * 1e6, 1))

    # Delivery manifest
    files = {x['filename']: x for x in man['files']}
    M('NGdsMB', f(files['postDummy.gds']['bytes'] / 1e6, 1))
    M('NDeliverFiles', len(files))
    M('NDeliverOk', B(all(man['verification'].values())))
    return asic_hz


# ----------------------------------------------------------------- FPGA archive
def fpga(asic_hz: float):
    co = load(FPGA / 'coopt_summary.json')
    msum = load(FPGA / 'models/summary.json')
    mod = load(FPGA / 'models/models.json')
    scn = load(FPGA / 'scenes/scenes.json')
    ssum = load(FPGA / 'scenes/summary.json')
    r20 = load(FPGA / 'resnet20/resnet.json')
    r20s = load(FPGA / 'resnet20/summary.json')
    r18 = load(FPGA / 'resnet18/resnet.json')
    r18s = load(FPGA / 'resnet18/summary.json')
    aud = load(FPGA / 'rtl_audit/model_audit.json')
    auds = load(FPGA / 'rtl_audit/summary.json')
    sys_mhz = msum['sys_clk_mhz']
    M('NSysClk', f'{sys_mhz:g}')
    M('NBoardCommit', tex(msum['commit']))
    M('NResCommit', tex(r20s['commit']))
    M('NFpgaPass', B(all(s['passed'] and s['fpga_validated'] for s in (msum, ssum, r20s, r18s))))

    # ------------------------------------------------ compiler co-optimization
    rt, re_ = co['resnet20'], co['resnet18']
    keys = [('program_groups', 'Groups'), ('tile_calls', 'Tiles'), ('fused_subgraphs', 'Fused'),
            ('host_post_ops', 'HostOps'), ('jtag_total_bytes', 'Bytes')]
    for pre, blk in (('RTw', rt), ('REi', re_)):
        for var, vn in (('baseline', 'Base'), ('coopt_software_fusion_only', 'Sw'), ('coopt_hw_add', 'Hw')):
            for k, kn in keys:
                M(f'N{pre}{vn}{kn}', i(blk[var][k]))
    rb, rh = rt['baseline'], rt['coopt_hw_add']
    M('NRTwBytesPct', pct(rh['jtag_total_bytes'] - rb['jtag_total_bytes'], rb['jtag_total_bytes'], 1))
    rtl_commit = grab(r'frame0_rtl_([0-9a-f]+)_cycles', ' '.join(rt))
    M('NProbeCommit', rtl_commit)
    M('NRTwRtlCyc', i(rt[f'frame0_rtl_{rtl_commit}_cycles']))
    M('NRTwRtlExact', B(rt[f'frame0_rtl_{rtl_commit}_exact']))
    M('NRTwBaseCyc', i(rt['frame0_baseline_cycles']))
    M('NRTwCycPct', pct(rt[f'frame0_rtl_{rtl_commit}_cycles'] - rt['frame0_baseline_cycles'], rt['frame0_baseline_cycles'], 1))
    eb, es, eh = re_['baseline'], re_['coopt_software_fusion_only'], re_['coopt_hw_add']
    M('NREiTilePct', pct(eb['tile_calls'] - eh['tile_calls'], eb['tile_calls'], 1))
    M('NREiGroupPct', pct(eb['program_groups'] - eh['program_groups'], eb['program_groups'], 1))
    M('NREiBytesPct', pct(eb['jtag_total_bytes'] - eh['jtag_total_bytes'], eb['jtag_total_bytes'], 1))
    M('NREiInPct', pct(eb['jtag_input_bytes'] - eh['jtag_input_bytes'], eb['jtag_input_bytes'], 1))
    M('NREiSwTilePct', pct(eb['tile_calls'] - es['tile_calls'], eb['tile_calls'], 1))
    lines = ['idx variant groups tiles mbytes']
    for k, (var, lab) in enumerate((('baseline', 'Baseline'), ('coopt_software_fusion_only', 'SwFusion'),
                                    ('coopt_hw_add', 'HwAdd'))):
        b = re_[var]
        lines.append(f"{k} {lab} {b['program_groups']} {b['tile_calls']} {b['jtag_total_bytes'] / 1e6:.4f}")
    write('resnet18_coopt.dat', '\n'.join(lines) + '\n')
    probes = re_[f'rtl_probes_{rtl_commit}']
    body = []
    for p in probes.values():
        body.append([r'\texttt{' + p['program_id'] + '}', r'\texttt{' + tex(p['layer_name']) + '}',
                     '+'.join(p['ops']), '×'.join(map(str, p['input_shape'])) + '→' + '×'.join(map(str, p['output_shape'])),
                     i(p['rtl_npu_cycles']), r'\ok' if p['rtl_bit_exact'] else 'FAIL'])
    write('probes.tex', tabular('@{}lllrrc@{}', [r'\textbf{ID}', r'\textbf{Layer}', r'\textbf{Ops}', r'\textbf{Shape (HWC)}',
                                                r'\textbf{Cycles}', r'\textbf{0-bit}'], body))
    M('NProbeCount', len(probes))
    M('NProbeExact', sum(p['rtl_bit_exact'] for p in probes.values()))

    # ------------------------------------------------ zoo sweep
    runs = mod['runs']
    names = list(runs[0]['models'])
    tcks = [r['tck_mhz'] for r in runs]
    M('NZooModels', len(names))
    M('NTckCount', len(tcks))
    M('NTckList', ', '.join(f'{t:g}' for t in tcks))
    M('NTckMin', f'{min(tcks):g}')
    M('NTckMax', f'{max(tcks):g}')
    per = {r['models'][n]['frames_checked'] for r in runs for n in names}
    M('NZooFramesPer', '/'.join(str(p) for p in sorted(per)))
    total = sum(r['models'][n]['frames_checked'] for r in runs for n in names)
    exact = sum(r['models'][n]['frames_checked'] for r in runs for n in names
                if r['models'][n]['golden_match'] and not r['models'][n]['mismatches'])
    M('NZooRuns', i(total))
    M('NZooExact', i(exact))
    M('NZooWarm', runs[0]['models'][names[0]]['warm']['count'])
    load_lines = ['tck ' + ' '.join(n.replace('_', '') for n in names)]
    warm_lines = list(load_lines)
    for r in runs:
        load_lines.append(f"{r['tck_mhz']:g} " + ' '.join(f"{r['models'][n]['cold']['load_model_ms']:.3f}" for n in names))
        warm_lines.append(f"{r['tck_mhz']:g} " + ' '.join(f"{r['models'][n]['warm']['median_ms']:.3f}" for n in names))
    write('tck_load.dat', '\n'.join(load_lines) + '\n')
    write('tck_warm.dat', '\n'.join(warm_lines) + '\n')
    M('NZooCols', ','.join(n.replace('_', '') for n in names))
    M('NZooLegend', ','.join(r'\texttt{' + tex(n) + '}' for n in names))
    lo, hi = runs[tcks.index(min(tcks))], runs[tcks.index(max(tcks))]
    g = lambda r, k: statistics.geometric_mean(r['models'][n]['cold'][k] for n in names)
    gw = lambda r: statistics.geometric_mean(r['models'][n]['warm']['median_ms'] for n in names)
    M('NLoadGeoLo', f(g(lo, 'load_model_ms'), 1))
    M('NLoadGeoHi', f(g(hi, 'load_model_ms'), 1))
    M('NLoadSpeedup', f(g(lo, 'load_model_ms') / g(hi, 'load_model_ms'), 2))
    M('NWarmGeoLo', f(gw(lo), 1))
    M('NWarmGeoHi', f(gw(hi), 1))
    ten = next(r for r in runs if r['tck_mhz'] == 10.0)
    M('NWarmGeoTen', f(gw(ten), 1))
    M('NLoadGeoTen', f(g(ten, 'load_model_ms'), 1))

    # FPGA BRAM vs behavioral ASIC SRAM cycle delta
    body, deltas = [], []
    for n in names:
        fc = runs[0]['models'][n]['npu_cycles']['median']
        rc = aud[n]['npu_cycles']
        rc_v = min(rc) if isinstance(rc, list) else rc
        rc_s = '/'.join(i(x) for x in rc) if isinstance(rc, list) else i(rc)
        d = fc - rc_v
        deltas.append(100.0 * d / rc_v)
        body.append([r'\texttt{' + tex(n) + '}', rc_s, i(fc), '+' + i(d), '+' + f(100.0 * d / rc_v, 2) + r'\%'])
    write('cycle_delta.tex', tabular('@{}lrrrr@{}', [r'\textbf{Model}', r'\textbf{RTL beh.}', r'\textbf{FPGA}',
                                                    r'\textbf{Δ cyc}', r'\textbf{Δ\%}'], body))
    M('NCycDeltaMin', f(min(deltas), 2))
    M('NCycDeltaMax', f(max(deltas), 2))
    M('NAuditFrames', i(sum(a['vectors_checked'] for a in aud.values())))
    M('NAuditExact', i(sum(a['vectors_checked'] for a in aud.values() if a['bit_exact'])))
    M('NAuditPass', B(auds['passed']))
    M('NMaxZooOut', max(a['output_bytes'] for a in aud.values()))
    M('NAuditVectors', '/'.join(sorted({str(a['vectors_checked']) for a in aud.values()})))

    # ------------------------------------------------ six scenes
    srun = scn['runs'][0]['scenes']
    body, med, fps = [], [], []
    for n, s in srun.items():
        lat = s['latency']
        med.append(lat['median_ms'])
        fps.append(lat['fps'])
        body.append([r'\texttt{' + tex(n) + '}', r'\texttt{' + tex(s['model']) + '}', f"{s['golden_exact']}/{s['frames']}",
                     i(s['npu_cycles_median']), f(s['npu_cycles_median'] / (sys_mhz * 1e3), 3), f(lat['median_ms'], 2),
                     f(lat['p95_ms'], 2), f(s['transfer_ms_median'], 2), f(lat['fps'], 1)])
    write('scenes.tex', tabular('@{}llrrrrrrr@{}', [r'\textbf{Scene}', r'\textbf{Model}', r'\textbf{Exact}', r'\textbf{Cycles}',
                                                   r'\textbf{NPU ms}', r'\textbf{Med. ms}', r'\textbf{p95 ms}',
                                                   r'\textbf{Xfer ms}', r'\textbf{fps}'], body))
    M('NSceneCount', len(srun))
    M('NSceneFrames', scn['frames_checked'])
    M('NSceneExact', scn['frames_exact'])
    M('NSceneAgree', f"{scn['agreement_rate']:.1f}")
    M('NSceneTck', f"{scn['runs'][0]['tck_mhz']:g}")
    M('NSceneMedMin', f(min(med), 2))
    M('NSceneMedMax', f(max(med), 2))
    M('NSceneFpsMin', f(min(fps), 1))
    M('NSceneFpsMax', f(max(fps), 1))
    M('NSceneScope', tex(scn['scope_note'].split(':')[0]))
    M('NZooGolden', tex(grab(r'(\d+/\d+) zoo golden frames', scn['scope_note'])))

    # ------------------------------------------------ ResNet-20
    m20 = r20['runs'][0]['models']['resnet20']
    bd = m20['breakdown_median_ms']
    M('NRTwSamples', m20['samples_checked'])
    M('NRTwExact', B(m20['bit_exact']))
    M('NRTwLabel', m20['label_correct'])
    M('NRTwMaxDiff', max(r['max_abs_diff'] for r in m20['rows']))
    M('NRTwCycles', i(m20['npu_cycles_median']))
    M('NRTwCompute', f(bd['npu_compute'], 2))
    M('NRTwComputeShort', f(bd['npu_compute'], 1))
    M('NRTwWall', f(bd['wall'], 2))
    M('NRTwHost', f(bd['host'], 2))
    M('NRTwJtag', f(bd['jtag_transfer'], 2))
    M('NRTwJtagPct', pct(bd['jtag_transfer'], bd['wall'], 2))
    M('NRTwComputePct', pct(bd['npu_compute'], bd['wall'], 2))
    M('NRTwTck', f"{r20['runs'][0]['tck_mhz']:g}")
    tel = m20['rows'][0]['telemetry']
    rt_bytes = tel['jtag_model_bytes'] + tel['jtag_input_bytes'] + tel['jtag_output_bytes']
    M('NRTwLoads', tel['model_loads'])
    M('NRTwTileInf', tel['tile_infers'])
    M('NRTwMeasBytes', i(rt_bytes))
    M('NRTwBw', f(rt_bytes / (bd['jtag_transfer'] / 1e3) / 1e3, 1))
    M('NRTwAsicMs', f(rt[f'frame0_rtl_{rtl_commit}_cycles'] / asic_hz * 1e3, 2))
    walls = [r['wall_ms'] for r in m20['rows']]
    M('NRTwWallMin', f(min(walls), 1))
    M('NRTwWallMax', f(max(walls), 1))
    body = []
    for r in m20['rows']:
        body.append([str(r['index']), str(r['label']), str(r['predicted']), r'\ok' if r['bit_exact'] else 'FAIL',
                     str(r['max_abs_diff']), i(r['npu_cycles']), f(r['npu_compute_ms'], 2), f(r['wall_ms'], 1)])
    write('resnet20_rows.tex', tabular('@{}rrrcrrrr@{}', [r'\textbf{\#}', r'\textbf{Label}', r'\textbf{Pred.}', r'\textbf{Exact}',
                                                         r'\textbf{max|Δ|}', r'\textbf{Cycles}', r'\textbf{NPU ms}', r'\textbf{Wall ms}'], body))
    write('resnet20_bd.dat', 'idx part ms\n'
          f"0 Host {bd['host']:.3f}\n1 NPU {bd['npu_compute']:.3f}\n2 JTAG {bd['jtag_transfer']:.3f}\n")

    # ------------------------------------------------ ResNet-18
    m18 = r18['runs'][0]['models']['resnet18']
    bd = m18['breakdown_median_ms']
    row = m18['rows'][0]
    tel = row['telemetry']
    plan = m18['plan_summary']
    meas = tel['jtag_model_bytes'] + tel['jtag_input_bytes'] + tel['jtag_output_bytes']
    M('NREiSamples', m18['samples_checked'])
    M('NREiExact', B(m18['bit_exact']))
    M('NREiLabel', m18['label_correct'])
    M('NREiMaxDiff', max(r['max_abs_diff'] for r in m18['rows']))
    M('NREiCycles', i(m18['npu_cycles_median']))
    M('NREiCompute', f(bd['npu_compute'], 2))
    M('NREiComputeShort', f(bd['npu_compute'], 0))
    M('NREiWallS', f(bd['wall'] / 1e3, 2))
    M('NREiHost', f(bd['host'], 2))
    M('NREiJtagS', f(bd['jtag_transfer'] / 1e3, 2))
    M('NREiJtagPct', pct(bd['jtag_transfer'], bd['wall'], 2))
    M('NREiComputePct', pct(bd['npu_compute'], bd['wall'], 2))
    M('NREiRatio', f(bd['jtag_transfer'] / bd['npu_compute'], 1))
    M('NREiLoads', i(tel['model_loads']))
    M('NREiTileInf', i(tel['tile_infers']))
    M('NREiPlanGroups', i(plan['program_groups']))
    M('NREiPlanBytes', i(plan['jtag_total_bytes']))
    M('NREiMeasBytes', i(meas))
    M('NREiMeasMB', f(meas / 1e6, 2))
    M('NREiModelMeas', i(tel['jtag_model_bytes']))
    M('NREiModelPlan', i(plan['jtag_model_bytes']))
    M('NREiModelSaved', i(plan['jtag_model_bytes'] - tel['jtag_model_bytes']))
    M('NREiInBytes', i(tel['jtag_input_bytes']))
    M('NREiOutBytes', i(tel['jtag_output_bytes']))
    M('NREiBw', f(meas / (bd['jtag_transfer'] / 1e3) / 1e3, 1))
    M('NREiTck', f"{r18['runs'][0]['tck_mhz']:g}")
    M('NREiAsicMs', f(m18['npu_cycles_median'] / asic_hz * 1e3, 1))
    M('NREiLabelTrue', row['label'])
    M('NREiPred', row['predicted'])
    # Bytes that would have to move to hide transport under compute at this frame size:
    M('NREiNeedBw', f(meas / (bd['npu_compute'] / 1e3) / 1e6, 1))
    M('NRTwNeedBw', f(rt_bytes / (m20['breakdown_median_ms']['npu_compute'] / 1e3) / 1e6, 1))
    write('resnet18_bd.dat', 'idx part s\n'
          f"0 Host {bd['host'] / 1e3:.4f}\n1 NPU {bd['npu_compute'] / 1e3:.4f}\n2 JTAG {bd['jtag_transfer'] / 1e3:.4f}\n")

    # Cross-platform comparison rows (ResNet-20 cycles)
    M('NRTwFpgaMs', f(m20['npu_cycles_median'] / (sys_mhz * 1e3), 2))
    M('NRTwCycFpgaDelta', pct(m20['npu_cycles_median'] - rt[f'frame0_rtl_{rtl_commit}_cycles'],
                              rt[f'frame0_rtl_{rtl_commit}_cycles'], 2))
    ecg_f = runs[0]['models']['ecg']['npu_cycles']['median']
    M('NEcgRtlCyc', i(aud['ecg']['npu_cycles']))
    M('NEcgFpgaCyc', i(ecg_f))
    M('NEcgFpgaUs', f(ecg_f / sys_mhz, 1))
    M('NEcgSceneMs', f(srun['ecg']['latency']['median_ms'], 2))
    M('NMnistRtlCyc', i(aud['mnist_l']['npu_cycles']))
    miss = [str(r['index']) for r in m20['rows'] if r['label'] != r['predicted']]
    M('NRTwMissIdx', ', '.join(miss) if miss else '--')
    M('NSceneNpuMax', f(max(s['npu_cycles_median'] for s in srun.values()) / (sys_mhz * 1e3), 2))


def main():
    DATA.mkdir(exist_ok=True)
    FIGS.mkdir(exist_ok=True)
    for name in IMAGES:
        shutil.copy2(EDA / 'images' / name, FIGS / name)
    sources()
    asic_hz = asic()
    fpga(asic_hz)
    lines = ['% Auto-generated by make_data.py from docs/validation records. Do not edit.']
    lines += [f'\\providecommand{{\\{k}}}{{}}\\renewcommand{{\\{k}}}{{{v}}}' for k, v in MACROS.items()]
    write('numbers.tex', '\n'.join(lines) + '\n')
    print(f'data/numbers.tex: {len(MACROS)} macros; figs/: {len(IMAGES)} images')


if __name__ == '__main__':
    main()
