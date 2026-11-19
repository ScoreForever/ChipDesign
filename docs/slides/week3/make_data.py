#!/usr/bin/env python3
r"""Generate English LaTeX data macros and pgfplots snippets for the Week 3 deck.

All metric numbers and plot coordinates are read directly from repository evidence:
  docs/validation/eda_latest_20261111/       (FF-lookup synthesis/PnR baseline)
  docs/validation/eda_week3_lab5_20261119/   (unified single-GDS Lab 4 + full physical signoff)
  docs/validation/rtl_lookup_20261118/       (matched seven-model RTL cycle comparison)
  docs/validation/jtag_rpc_20261118/         (measured 1 MHz RTL wire sim + live TclRpc TCP+tclsh sim)
  docs/validation/demo_software_20261116/ + software/host/demos/models/*/training_result.json
  verification/fpga/results/2026-11-11-jtag-frequency/  (prior EGo1 8x8 @ 50 MHz latency breakdown)
  hardware/npu/scripts/mutation_test.py and local git log 44a76fc / 781afc2 (regression/mutant counts)

Output: data/numbers.tex (\N... macros) and data/*.tex tables/plots.
"""
from __future__ import annotations

import csv
import json
import math
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / 'data'
VAL = ROOT / 'docs/validation'
OLD = VAL / 'eda_latest_20261111'
W3 = VAL / 'eda_week3_lab5_20261119'
RTL = VAL / 'rtl_lookup_20261118'
JT = VAL / 'jtag_rpc_20261118'
DEMO = VAL / 'demo_software_20261116'
FPGA = ROOT / 'verification/fpga/results/2026-11-11-jtag-frequency'
MODELS = ROOT / 'software/host/demos/models'
LOOKUP_COMMIT, MUTANT_COMMIT = '44a76fc', '781afc2'
BS = chr(92)


def jload(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def rows(p, delim=','):
    with open(p, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter=delim))


def git_msg(commit):
    out = subprocess.run(
        ['git', 'log', '-1', '--format=%B', commit],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    return ' '.join(out.split())


def grab(pattern, text, what):
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f'pattern not found in {what}: {pattern}')
    return m


def pct(new, old):
    return 100.0 * (new - old) / old


def sgn(x, nd=3):
    return f'{x:+.{nd}f}'


def esc(s):
    return (
        str(s)
        .replace('\\', '\\textbackslash{}')
        .replace('_', '\\_')
        .replace('&', '\\&')
        .replace('%', '\\%')
    )


def lookup_area(sb):
    ff = [v for k, v in sb['flops_by_register'].items() if k.startswith('npu_tensor_lookup')]
    comb = sum(v for k, v in sb['comb_by_module'].items() if k.startswith('npu_tensor_lookup'))
    return sum(v['count'] for v in ff), sum(v['area_um2'] for v in ff) + comb


# <<SECTION:asic>>
def sec_asic(N):
    om = jload(OLD / 'metrics.json')
    op = om['physical']
    nm = jload(W3 / 'metrics.json')
    osb, nsb = jload(OLD / 'synthesis_area_breakdown.json'), jload(W3 / 'synthesis_area_breakdown.json')
    ncells = jload(W3 / 'synthesis_cells.json')
    pw = jload(W3 / 'power_summary.json')
    readme = (W3 / 'README.md').read_text(encoding='utf-8')

    lf0, la0 = lookup_area(osb)
    lf1, la1 = lookup_area(nsb)
    N['LookupFFBefore'], N['LookupFFAfter'] = f'{lf0:,}', f'{lf1:,}'
    N['LookupAreaBefore'], N['LookupAreaAfter'] = f'{la0 / 1e3:.1f}', f'{la1 / 1e3:.1f}'
    N['LookupAreaPct'] = f'{pct(la1, la0):.2f}'
    std0 = osb['flop_area_um2'] + osb['comb_area_um2']
    std1 = nsb['flop_area_um2'] + nsb['comb_area_um2']
    N['LookupShareBefore'] = f'{100 * la0 / std0:.1f}'
    N['SynStdBefore'], N['SynStdAfter'] = f'{std0 / 1e6:.3f}', f'{std1 / 1e6:.3f}'
    N['SynStdPct'] = f'{pct(std1, std0):.2f}'
    sq0 = sum(v['count'] for v in osb['flops_by_register'].values())
    sq1 = sum(v['count'] for v in nsb['flops_by_register'].values())
    assert sq1 == ncells['sequential_count']
    N['SynSeqBefore'], N['SynSeqAfter'] = f'{sq0:,}', f'{sq1:,}'
    N['SynSeqPct'] = f'{pct(sq1, sq0):.2f}'
    act0, act1 = op['active_standard_area_um2'], nm['active_standard_area_um2']
    N['ActBefore'], N['ActAfter'] = f'{act0 / 1e6:.3f}', f'{act1 / 1e6:.3f}'
    N['ActPct'] = f'{pct(act1, act0):.2f}'
    N['ActInstAfter'] = f"{nm['active_standard_instances']:,}"
    wl0, wl1 = op['wire_length_um'], nm['wire_length_um']
    N['WireBefore'], N['WireAfter'] = f'{wl0 / 1e6:.3f}', f'{wl1 / 1e6:.3f}'
    N['WirePct'] = f'{pct(wl1, wl0):.2f}'
    vp0, vp1 = om['power']['total_mw'], pw['vectorless']['total_mw']
    N['VlessBefore'], N['VlessAfter'] = f'{vp0:.2f}', f'{vp1:.2f}'
    N['VlessPct'] = f'{pct(vp1, vp0):.2f}'
    N['DieBefore'], N['DieAfter'] = f"{op['die_area_mm2']:.3f}", f"{nm['die_area_mm2']:.3f}"
    N['CoreAfter'] = f"{nm['core_area_mm2']:.3f}"
    N['TTSetup'], N['TTHold'] = sgn(nm['setup_wns_ns']), sgn(nm['hold_wns_ns'])
    N['MacroCount'] = str(nm['macro_count'])
    N['MacroArea'] = f"{nm['macro_area_um2'] / 1e6:.3f}"
    N['CtsRepairArea'] = f"{nm['cts_and_repair_area_change_um2'] / 1e3:.1f}"
    N['SramSeqCount'] = f"{ncells['sequential_count']:,}"

    om_rows = {r['group']: r for r in rows(OLD / 'physical_area_modules.csv')}
    nm_rows = {r['group']: r for r in rows(W3 / 'physical_area_modules.csv')}
    lines = []
    for g in ['NPU', 'shared_store_logic', 'controller', 'host']:
        a0, a1 = float(om_rows[g]['area_um2']), float(nm_rows[g]['area_um2'])
        s0, s1 = int(om_rows[g]['sequential_count']), int(nm_rows[g]['sequential_count'])
        lines.append(
            f'{BS}texttt{{{esc(g)}}} & {a0 / 1e3:,.1f} & {a1 / 1e3:,.1f} & {pct(a1, a0):+.2f}{BS}% '
            f'& {s0:,} → {s1:,} {BS}{BS}\n'
        )
    (OUT / 'hier_rows.tex').write_text(''.join(lines), encoding='utf-8')
    assert abs(sum(float(r['area_um2']) for r in nm_rows.values()) - act1) < 1.0
    npu0, npu1 = float(om_rows['NPU']['area_um2']), float(nm_rows['NPU']['area_um2'])
    N['NpuAreaPct'] = f'{pct(npu1, npu0):.2f}'

    deltas = [
        ('查找表面积', pct(la1, la0)),
        ('触发器数', pct(sq1, sq0)),
        ('布线线长', pct(wl1, wl0)),
        ('综合面积', pct(std1, std0)),
        ('布线后面积', pct(act1, act0)),
        ('无向量功耗', pct(vp1, vp0)),
    ]
    # Highlight the dominant lookup-area bar in PKU crimson and secondary reductions in steel blue
    first = deltas[:1]
    rest = deltas[1:]
    plot_tex = (
        f'{BS}addplot[fill=peking, draw=none] coordinates {{'
        + ' '.join(f'({-v:.2f},{k})' for k, v in first)
        + '};\n'
        + f'{BS}addplot[fill=steel, draw=none] coordinates {{'
        + ' '.join(f'({-v:.2f},{k})' for k, v in rest)
        + '};\n'
    )
    (OUT / 'delta_plot.tex').write_text(plot_tex, encoding='utf-8')
    N['DeltaTicks'] = ','.join(k for k, _ in deltas)

    checks = jload(W3 / 'checks.json')
    geo_pass = sum(bool(v) for v in checks.values())
    N['GeoPass'], N['GeoTotal'] = str(geo_pass), str(len(checks))
    N['GeoStatus'] = '通过' if geo_pass == len(checks) else '存在失败'
    l5 = jload(W3 / 'lab5_summary.json')
    N['DieSide'] = f"{l5['raw_die_bbox_um'][2]:.2f}"
    N['DieLimit'] = grab(r'`<= (\d+) µm` Lab 4 die limit', readme, 'README (die limit)')[1]
    assert l5['raw_die_bbox_um'][2] <= float(N['DieLimit'])
    N['Util'] = grab(r'utilization `([\d.]+)`', readme, 'README (utilization)')[1]
    N['UtilPct'] = f"{100 * float(N['Util']):.0f}"
    N['HoldTarget'] = grab(r'hold target `([\d.]+) ns`', readme, 'README (hold target)')[1]
    m = grab(r'Core `(\d+) MHz` \(`([\d.]+) ns`\)', readme, 'README (core clock)')
    N['CoreMHz'], N['CorePeriod'] = m[1], m[2]
    N['Cols'] = str(jload(W3 / 'build_profile.json')['array_cols'])

    so = jload(W3 / 'signoff_summary.json')['results']
    N['StaSetup'], N['StaHold'] = sgn(so['sta']['setup_wns_ns']), sgn(so['sta']['hold_wns_ns'])
    N['LecVerified'] = 'verified' if so['lec']['verified'] else 'FAILED'
    sdf = jload(W3 / 'sdf_result.json')
    assert sdf['passed']
    N['SdfStatus'] = 'PASS' if sdf['passed'] else 'FAIL'
    d = sdf['diagnostics']
    N['SdfCmds'] = str(sdf['jtag_commands'])
    N['SdfUnexpected'] = str(len(d['fatal']) + len(d['unexpected_sdfnep']) + len(d['unexpected_sdfnet']))
    N['SdfPadConf'] = str(sdf['accepted_time0_input_pad_conflicts'])


# <<SECTION:physical_signoff>>
def sec_physical_signoff(N):
    l5 = jload(W3 / 'lab5_summary.json')
    ck = l5['checks']
    seal = l5['sealed_die_um']
    lim = l5['max_allowed_sealed_die_um']
    assert seal[0] == seal[1] and seal[0] <= lim
    N['DbuPerUm'] = str(l5['dbu_per_um'])
    N['SealSide'] = '%.1f' % seal[0]
    N['SealLimit'] = '%.0f' % lim
    N['FormalTapeout'] = 'true' if l5['formal_tapeout_signoff'] else 'false'
    dc = l5['dummy_config']
    N['DummyMsix'] = '%.1f' % dc['dmW_M6_um']
    assert dc['dmW_M6_um'] == dc['dmL_M6_um']

    drc = ck['drc']
    cats = drc['accepted_teaching_categories']
    assert drc['passed'] and not drc['blocking']
    assert sum(v['hier'] for v in cats.values()) == drc['raw_total_hier']
    assert sum(v['expanded'] for v in cats.values()) == drc['raw_total_expanded']
    N['DrcRules'] = str(drc['rules_executed'])
    N['DrcHier'] = '{:,}'.format(drc['raw_total_hier'])
    N['DrcExp'] = '{:,}'.format(drc['raw_total_expanded'])
    N['DrcBlocking'] = str(len(drc['blocking']))
    N['DummyMsixViol'] = str(cats.get('M6.A.1', {}).get('hier', 0))
    assert N['DummyMsixViol'] == '0' and 'M6.A.1' not in drc['blocking']
    N['CorFourAfter'] = str(cats.get('CO.R.4', {}).get('hier', 0))
    N['CorFourBefore'] = str(jload(W3 / 'signoff_summary.json')['results']['drc']['accepted']['CO.R.4']['count'])

    lv = ck['lvs']
    strict, relaxed = lv['pr_0pct'], lv['pr_3_48pct']
    assert not lv['sram_blackboxed']
    assert relaxed['layout_ports'] == relaxed['source_ports'] == len(lv['top_ports'])
    N['LvsPorts'] = str(relaxed['layout_ports'])
    N['LvsStrictStatus'], N['LvsStatus'] = strict['status'], relaxed['status']
    N['LvsErr'] = str(strict['property_error_count'])
    N['LvsMax'] = '%.2f' % strict['max_io_resistor_deviation_pct']
    N['LvsStrictTol'] = '%.0f' % strict['r_pr_tolerance_pct']
    N['LvsTol'] = '%.2f' % relaxed['r_pr_tolerance_pct']
    assert strict['max_io_resistor_deviation_pct'] < relaxed['r_pr_tolerance_pct']

    ant, wb = ck['antenna'], ck['wirebond']
    assert ant['passed'] and wb['passed']
    N['AntRules'], N['WbRules'] = str(ant['rules_executed']), str(wb['rules_executed'])
    N['AntHier'], N['AntExp'] = str(ant['total_hier']), str(ant['total_expanded'])
    N['WbHier'], N['WbExp'] = str(wb['total_hier']), str(wb['total_expanded'])
    N['DrcBadge'] = BS + ('ok' if (drc['passed'] and not drc['blocking']) else 'pending')
    N['LvsBadge'] = BS + ('ok' if relaxed['status'] == 'CORRECT' else 'pending')
    N['AntBadge'] = BS + ('ok' if ant['passed'] else 'pending')
    N['WbBadge'] = BS + ('ok' if wb['passed'] else 'pending')
    all_so = drc['passed'] and not drc['blocking'] and relaxed['status'] == 'CORRECT' and ant['passed'] and wb['passed']
    N['SignoffAllStatus'] = '全通过' if all_so else '存在未闭合项'

    erc = ck['erc']
    er = erc['rules']
    assert sum(v['hier_count'] for v in er.values()) == erc['total_hier']
    assert sum(v['expanded_count'] for v in er.values()) == erc['total_expanded']
    N['ErcRules'] = str(erc['rules_executed'])
    N['ErcHier'], N['ErcExp'] = str(erc['total_hier']), str(erc['total_expanded'])
    for key, short in [
        ('mnpg', 'Mnpg'),
        ('floating.nxwell_float', 'Nxwell'),
        ('floating.psub_float', 'Psub'),
        ('npvss49', 'Vss'),
    ]:
        N['Erc' + short + 'H'] = str(er[key]['hier_count'])
        N['Erc' + short + 'E'] = str(er[key]['expanded_count'])
    geo = jload(W3 / 'final_erc_geometry.json')
    assert (geo['total_hier'], geo['total_expanded']) == (erc['total_hier'], erc['total_expanded'])

    with open(W3 / 'wirebond_keepouts.tsv', newline='', encoding='utf-8') as f:
        wbrows = [r for r in csv.reader(f, delimiter='\t') if r]
    margins = set()
    die = l5['raw_die_bbox_um']
    for r in wbrows:
        pad = [float(v) for v in r[1].split()]
        keep = [float(v) for v in r[2].split()]
        for i, (a, b) in enumerate(zip(pad, keep)):
            d = round(abs(a - b), 3)
            if d == 0:
                assert abs(b - die[i]) < 1e-6, (r[0], i)
            else:
                margins.add(d)
    assert len(margins) == 1
    N['PadCount'] = str(len(wbrows))
    N['PadMargin'] = '%.0f' % next(iter(margins))

    dd = (W3 / 'erc_deep_dive.md').read_text(encoding='utf-8')
    m = grab(
        r'`(\d+) × (\d+)` \(`\d+`\) \+ `\((\d+) \+ (\d+)\) × (\d+)` \(`\d+`\) = \*\*`(\d+)`\*\*',
        dd,
        'erc_deep_dive.md (mnpg accounting)',
    )
    v1n, v1f, v2n, vpn, v2f = (int(m[i]) for i in range(1, 6))
    assert v1n * v1f + (v2n + vpn) * v2f == int(m[6]) == er['mnpg']['expanded_count']
    m2 = grab(
        r'`(\d+)` `PVSS1CDG` pads × `(\d+)` diode units = \*\*`(\d+)`\*\*',
        dd,
        'erc_deep_dive.md (PVSS1CDG accounting)',
    )
    vsn, vsd = int(m2[1]), int(m2[2])
    assert vsn * vsd == int(m2[3]) == er['npvss49']['expanded_count'] == er['floating.nxwell_float']['expanded_count']
    N['ErcVddOneN'], N['ErcVddOneF'] = str(v1n), str(v1f)
    N['ErcVddTwoN'], N['ErcVddPocN'], N['ErcVddTwoF'] = str(v2n), str(vpn), str(v2f)
    N['ErcVddOneE'], N['ErcVddTwoE'] = str(v1n * v1f), str((v2n + vpn) * v2f)
    N['ErcVssN'], N['ErcVssD'] = str(vsn), str(vsd)
    N['ErcDesignDefects'] = grab(
        r'\*\*Design-level defect count\*\*:\s*\*\*`(\d+)`\*\*',
        dd,
        'erc_deep_dive.md (design defects)',
    )[1]
    assert int(N['ErcDesignDefects']) == 0
    m3 = grab(
        r'\|\s*\*\*Total ERC\*\*\s*\|\s*\*\*`(\d+)\s*/\s*(\d+)`\*\*\s*\|\s*\*\*`(\d+)\s*/\s*(\d+)`\*\*\s*\|\s*\*\*`(\d+)`\*\*',
        dd,
        'erc_deep_dive.md (pre/post Seal-Ring ERC)',
    )
    assert int(m3[1]) == int(m3[3]) == erc['total_hier']
    assert int(m3[2]) == int(m3[4]) == erc['total_expanded']
    N['ErcPreH'], N['ErcPreE'], N['ErcPrePostDelta'] = m3[1], m3[2], m3[5]


# <<SECTION:mmmc>>
def sec_mmmc(N):
    mm = jload(W3 / 'mmmc_summary.json')
    readme = (W3 / 'README.md').read_text(encoding='utf-8')
    cor = {(c['corner'], c['mode']): c for c in rows(W3 / 'mmmc_corners.csv')}
    key = {'tt25': 'TT', 'ff0': 'FFz', 'ffm40': 'FFm', 'ss125': 'SS', 'SS125_DERATED': 'SSD'}
    for c, k in key.items():
        sw = float(cor[(c, 'setup')]['wns'])
        hw = float(cor[(c, 'hold')]['wns'])
        N[k + 'Setup'] = sgn(sw)
        N[k + 'HoldW'] = sgn(hw)
        N[k + 'Badge'] = BS + ('ok' if (sw >= 0 and hw >= 0) else 'pending')
        raw_tns = cor[(c, 'setup')]['tns_2000']
        N[k + 'Tns'] = '0' if raw_tns in ('0', '0.0', 'NA') else '{:,.1f}'.format(float(raw_tns))
    clean = ['tt25', 'ff0', 'ffm40']
    clean_ok = all(float(cor[(c, m)]['wns']) >= 0 for c in clean for m in ('setup', 'hold'))
    assert clean_ok
    N['CleanMmmcStatus'] = '均干净闭合' if clean_ok else '存在违例'
    hmin = min(clean, key=lambda c: float(cor[(c, 'hold')]['wns']))
    N['CleanHoldMin'] = sgn(float(cor[(hmin, 'hold')]['wns']))
    N['CleanHoldMinCorner'] = hmin
    ss = cor[('ss125', 'setup')]
    N['SSTns'] = '{:,.1f}'.format(float(ss['tns_2000']))
    N['SSViol'] = '{:,}'.format(int(ss['violating_endpoints_2000']))
    N['SSArr'], N['SSReq'] = ss['arrival'], ss['required']
    N['SSEndSram'] = 'SRAM' if 'i_sram' in ss['end'] else esc(ss['end'])
    m = grab(r'SS125_DERATED` \(SS, 125 .C\) \| `([\d.]+) ns` \(`([\d.]+) MHz`', readme, 'README (SS125_DERATED period)')
    N['SSDPeriod'], N['SSDFreq'] = m[1], m[2]
    ssd_ok = float(N['SSDSetup']) > 0 and float(N['SSDHoldW']) > 0
    assert ssd_ok
    N['SSDStatus'] = '干净闭合' if ssd_ok else '存在违例'
    N['RcNote'] = esc(mm['rc_note'])

    sram = rows(W3 / 'sram_interface_timing.csv')
    assert len(sram) == mm['sram_rows']
    N['SramRows'] = str(len(sram))
    sl = []
    for r_ in sram:
        vals = tuple(float(r_[k]) for k in ('setup_to', 'hold_to', 'setup_from', 'hold_from'))
        sl.append(
            BS + 'texttt{' + r_['corner'] + '} & ' + r_['role'] + ' & %+.3f & %+.3f & %+.3f & %+.3f ' % vals + BS + BS + '\n'
        )
    (OUT / 'sram_rows.tex').write_text(''.join(sl), encoding='utf-8')
    ssr = [r_ for r_ in sram if r_['corner'] == 'ss125']
    N['SramSSWorst'] = sgn(min(float(r_['setup_to']) for r_ in ssr))
    tt = [r_ for r_ in sram if r_['corner'] == 'tt25']
    N['SramTTWorst'] = sgn(min(float(r_['setup_to']) for r_ in tt))

    cdc = rows(W3 / 'cdc_4corners.tsv', '\t')
    assert len(cdc) == mm['cdc_rows']
    N['CdcRows'] = str(len(cdc))
    N['CdcPos'] = str(sum(float(r_['slack_ns']) > 0 for r_ in cdc))
    ncor = len({r_['corner'] for r_ in cdc})
    N['CdcCorners'] = str(ncor)
    N['CdcPaths'] = str(len(cdc) // ncor)
    by = {}
    for r_ in cdc:
        by[r_['corner']] = min(by.get(r_['corner'], 1e9), float(r_['slack_ns']))
    for c, k in key.items():
        if c in by:
            N['Cdc' + k + 'Min'] = sgn(by[c])
    cmin = min(by, key=by.get)
    N['CdcMin'], N['CdcMinCorner'] = sgn(by[cmin]), esc(cmin)
    N['MmmcCorners'] = str(len({c for c, _ in cor} - {'SS125_DERATED'}))


# <<SECTION:power>>
def sec_power(N):
    pw = jload(W3 / 'power_summary.json')
    wl = {w['name']: w for w in pw['workloads']}
    order = ['cnn_f1', 'cnn_f0', 'ecg', 'har', 'mnist_l']
    assert [w['name'] for w in pw['workloads']] == order
    assert all(w['activity_percent'] == 100.0 for w in pw['workloads'])
    N['PwAnnot'] = '%.0f' % min(w['activity_percent'] for w in pw['workloads'])
    N['PwN'] = str(len(order))
    pl = []
    for n in order:
        w = wl[n]
        pl.append(
            BS
            + 'texttt{'
            + esc(n)
            + '} & '
            + '{:,} & {:,.2f} & {:.2f} & {:.4f} '.format(w['npu_cycles'], w['dt_ns'] / 1e3, w['total_mw'], w['energy_uJ'])
            + BS
            + BS
            + '\n'
        )
    (OUT / 'power_rows.tex').write_text(''.join(pl), encoding='utf-8')
    vl = pw['vectorless']['total_mw']
    plot = (
        f'{BS}addplot[fill=peking, draw=none] coordinates {{(0,{wl["cnn_f1"]["total_mw"]:.2f})}};\n'
        + f'{BS}addplot[fill=steel, draw=none] coordinates {{'
        + ' '.join('(%d,%.2f)' % (i, wl[n]['total_mw']) for i, n in enumerate(order) if i > 0)
        + '};\n'
        + f'{BS}draw[ink, dashed, line width=0.8pt] (axis cs:-0.5,{vl:.2f}) -- (axis cs:{len(order) - 0.5:.1f},{vl:.2f});\n'
        + f'{BS}node[font={BS}tiny, text=ink, anchor=south east, inner sep=1.5pt] at (axis cs:{len(order) - 0.5:.1f},{vl:.2f}) '
        + f'{{无向量参考：{vl:.2f} mW}};\n'
    )
    (OUT / 'power_plot.tex').write_text(plot, encoding='utf-8')
    N['PowerTicks'] = ','.join(BS + 'texttt{' + esc(n) + '}' for n in order)
    N['PowerXmax'] = '%.1f' % (len(order) - 0.5)
    N['PowerLast'] = str(len(order) - 1)
    f1, f0 = wl['cnn_f1'], wl['cnn_f0']
    sav = 100 * (1 - f1['energy_uJ'] / f0['energy_uJ'])
    assert abs(sav - pw['cnn_energy_savings_percent']) < 1e-6
    N['FusionSave'] = '%.2f' % sav
    N['FusedE'], N['UnfusedE'] = '%.4f' % f1['energy_uJ'], '%.4f' % f0['energy_uJ']
    N['FusedCyc'], N['UnfusedCyc'] = '{:,}'.format(f1['npu_cycles']), '{:,}'.format(f0['npu_cycles'])
    N['FusionSpeed'] = '%.2f' % (f0['npu_cycles'] / f1['npu_cycles'])
    N['FusedMw'], N['UnfusedMw'] = '%.2f' % f1['total_mw'], '%.2f' % f0['total_mw']
    tot = [w['total_mw'] for w in pw['workloads']]
    dyn = [w['internal_mw'] + w['switching_mw'] for w in pw['workloads']]
    leak = [w['leakage_mw'] for w in pw['workloads']]
    N['VcdMin'], N['VcdMax'] = '%.2f' % min(tot), '%.2f' % max(tot)
    N['PwDynMin'], N['PwDynMax'] = '%.2f' % min(dyn), '%.2f' % max(dyn)
    N['PwLeakMin'], N['PwLeakMax'] = '%.4f' % min(leak), '%.4f' % max(leak)
    N['PwLeakDisp'] = N['PwLeakMin'] if N['PwLeakMin'] == N['PwLeakMax'] else f"{N['PwLeakMin']}–{N['PwLeakMax']}"
    N['PwLeakMaxPct'] = '%.2f' % max(100.0 * l_ / t_ for l_, t_ in zip(leak, tot))
    N['VlessRaw'] = '%.2f' % vl
    N['VlessOverMin'], N['VlessOverMax'] = '%.2f' % (vl / max(tot)), '%.2f' % (vl / min(tot))


# <<SECTION:rtl>>
def sec_rtl(N):
    prov = jload(RTL / 'provenance.json')
    N['RtlModels'] = str(len(prov['packaged_models']))
    N['RtlConfigs'] = str(len(prov['runs']))
    N['RtlNeg'] = str(len(prov['runs']['behavioral']['negative_checks_after']))
    mc = rows(RTL / 'model_cycles.csv')
    rtl_exact = all(
        r_['writes_before'] == r_['writes_after']
        and r_['checked_write_events_before'] == r_['checked_write_events_after']
        for r_ in mc
    )
    assert rtl_exact
    N['RtlExactShort'] = 'bit-exact' if rtl_exact else 'MISMATCH'
    N['RtlExactStatus'] = '全部 bit-exact 写回一致' if rtl_exact else '存在写回差异'
    neg_ok = all(
        run['negative_checks_before'] == run['negative_checks_after'] and len(run['negative_checks_after']) > 0
        for run in prov['runs'].values()
    )
    assert neg_ok
    N['RtlNegStatus'] = '全部通过' if neg_ok else '存在差异'
    N['RtlRuns'] = str(len(mc))
    d = [int(r_['cold_cycle_delta']) for r_ in mc]
    p = [float(r_['cold_cycle_pct']) for r_ in mc]
    N['CycDeltaMin'], N['CycDeltaMax'] = str(-max(d)), str(-min(d))
    N['CycPctMin'], N['CycPctMax'] = '%.2f' % -max(p), '%.2f' % -min(p)
    beh = {r_['model']: r_ for r_ in mc if r_['memory_config'] == 'behavioral'}
    fpg = {r_['model']: r_ for r_ in mc if r_['memory_config'] == 'fpga_mem'}
    cl = []
    for mdl in prov['packaged_models']:
        b, f = beh[mdl], fpg[mdl]
        cl.append(
            BS
            + 'texttt{'
            + esc(mdl)
            + '} & '
            + '{:,}'.format(int(b['cold_cycles_before']))
            + ' → '
            + '{:,}'.format(int(b['cold_cycles_after']))
            + ' & '
            + '{:+d}'.format(int(b['cold_cycle_delta']))
            + ' & '
            + '{:,}'.format(int(f['cold_cycles_before']))
            + ' → '
            + '{:,}'.format(int(f['cold_cycles_after']))
            + ' & '
            + '{:+.2f}'.format(float(f['cold_cycle_pct']))
            + BS
            + '% '
            + BS
            + BS
            + '\n'
        )
    (OUT / 'cycle_rows.tex').write_text(''.join(cl), encoding='utf-8')

    msg = git_msg(LOOKUP_COMMIT)
    m = grab(r'(\d+)x(\d+)-bit provenance table', msg, LOOKUP_COMMIT)
    N['Records'], N['RecordBitsOld'] = m[1], m[2]
    N['RecordWordBits'] = grab(r'one (\d+)-bit word', msg, LOOKUP_COMMIT)[1]
    for k, pat in [
        ('RegBeh', r'behavioral (\d+/\d+)'),
        ('RegFpga', r'FPGA_MEM (\d+/\d+)'),
        ('RegMacro', r'RA1SHD (\d+/\d+)'),
        ('FuzzPass', r'fuzz (\d+) pass'),
        ('CdcLimits', r'CDC limits (\d+/\d+)'),
        ('MutOld', r'strict mutation (\d+/\d+)'),
    ]:
        N[k] = grab(pat, msg, LOOKUP_COMMIT)[1]

    src = (ROOT / 'hardware/npu/scripts/mutation_test.py').read_text(encoding='utf-8')
    muts = re.findall(r"\('(l\d\d)', (CORE|CTRL),", src)
    en = {
        'l01': '张量覆写后旧记录未失效',
        'l02': '记录结束地址 off-by-one 边界错误',
        'l03': '失效写入被接受前即拉高 done',
        'l04': 'RAW 冒险：前一次写未完成即发起读',
        'l05': '写入未排空即宣布校验就绪',
        'l06': '忽略校验状态，允许未初始化源张量',
        'l07': 'VWAIT 期间未保持 Activation SRAM 端口所有权',
    }
    ids = sorted(i for i, _ in muts)
    assert ids == sorted(en), ids
    where = dict(muts)
    (OUT / 'mutants.tex').write_text(
        ''.join(
            BS + 'texttt{' + i + '} & ' + ('core' if where[i] == 'CORE' else 'ctrl') + ' & ' + en[i] + ' ' + BS + BS + '\n'
            for i in ids
        ),
        encoding='utf-8',
    )
    N['MutNew'] = str(len(ids))
    km = grab(r'All (\w+) are killed \(([^)]*)\)', git_msg(MUTANT_COMMIT), MUTANT_COMMIT)
    words = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
    killed = words.get(km[1]) or int(km[1])
    assert killed == len(ids)
    N['MutKilled'] = str(killed)
    N['MutKillPct'] = '%.0f' % (100 * killed / len(ids))


# <<SECTION:fpga>>
def sec_fpga(N):
    audit = jload(FPGA / 'audit.json')
    fpga_ok = bool(audit['fine']['passed'] and audit['coarse']['passed'])
    assert fpga_ok
    N['FpgaExactStatus'] = 'bit-exact' if fpga_ok else 'FAILED'
    N['FpgaCoarse'] = '{:,}'.format(audit['coarse']['inferences'])
    N['FpgaFine'] = '{:,}'.format(audit['fine']['inferences'])
    N['FpgaTotal'] = '{:,}'.format(audit['coarse']['inferences'] + audit['fine']['inferences'])

    def gm(v):
        return math.exp(sum(math.log(x) for x in v) / len(v))

    ana = jload(FPGA / 'fine/analysis.json')
    rows_1m = {r['model']: r for r in ana['rows'] if r['khz'] == 1000}
    order = ['ecg', 'kws_m', 'kws_l', 'mnist_l', 'fashion_cnn', 'har', 'gtsrb_l']
    assert set(rows_1m) == set(order)

    by = {}
    for r_ in rows(FPGA / 'fine/timing.csv'):
        by.setdefault(int(r_['khz']), []).append(r_)
    cold = {k: gm([float(r_['cold_ms']) for r_ in v]) for k, v in by.items()}
    res = {k: gm([float(r_['resident_ms']) for r_ in v]) for k, v in by.items()}
    assert min(cold, key=cold.get) == 1000
    N['FpgaBestMhz'] = '%g' % (min(cold, key=cold.get) / 1000)
    N['FpgaFreqs'] = str(len(by))
    N['FpgaModels'] = str(len(rows_1m))
    N['FpgaCold'], N['FpgaRes'] = '%.2f' % cold[1000], '%.2f' % res[1000]
    alt = min(res, key=res.get)
    N['FpgaAltPct'] = '%.2f' % (100 * (res[1000] - res[alt]) / res[1000])

    # Mutually exclusive breakdown of measured EGo1 resident_ms at 1 MHz TCK (note: completion_poll wraps the <=1.6 ms NPU compute)
    ctrl_coords, xfer_coords, wrap_coords = [], [], []
    comp_vals, ctrl_vals, xfer_vals, wrap_vals, upload_vals, cold_vals, res_vals = [], [], [], [], [], [], []
    for mdl in order:
        r_ = rows_1m[mdl]
        p = r_['phases_ms']
        comp_ms = r_['cycles'][0] / 50e3
        ctrl_ms = (
            p['input_metadata']
            + p['input_commit']
            + p['start']
            + p['completion_poll']
            + p['output_metadata']
            + p['ack']
        )
        xfer_ms = p['input_upload'] + p['output_readout']
        wrap_ms = r_['resident_ms'] - (ctrl_ms + xfer_ms)
        assert wrap_ms >= 0.0 and abs((ctrl_ms + xfer_ms + wrap_ms) - r_['resident_ms']) < 1e-6, (mdl, wrap_ms)
        comp_vals.append(comp_ms)
        ctrl_vals.append(ctrl_ms)
        xfer_vals.append(xfer_ms)
        wrap_vals.append(wrap_ms)
        upload_vals.append(r_['upload_ms'])
        cold_vals.append(r_['cold_ms'])
        res_vals.append(r_['resident_ms'])
        tick = esc(mdl)
        ctrl_coords.append(f'({ctrl_ms:.4f},{tick})')
        xfer_coords.append(f'({xfer_ms:.4f},{tick})')
        wrap_coords.append(f'({wrap_ms:.4f},{tick})')

    plot_tex = (
        f'{BS}addplot[fill=peking, draw=none] coordinates {{' + ' '.join(ctrl_coords) + '};\n'
        + f'{BS}addplot[fill=steel, draw=none] coordinates {{' + ' '.join(xfer_coords) + '};\n'
        + f'{BS}addplot[fill=muted!65!white, draw=none] coordinates {{' + ' '.join(wrap_coords) + '};\n'
    )
    (OUT / 'fpga_breakdown_plot.tex').write_text(plot_tex, encoding='utf-8')
    N['FpgaTicks'] = ','.join(f'{esc(m)}' for m in order)
    N['FpgaComputeMin'] = '%.2f' % min(comp_vals)
    N['FpgaComputeMax'] = '%.2f' % max(comp_vals)
    N['FpgaComputeMaxPct'] = '%.1f' % max(100.0 * c / r for c, r in zip(comp_vals, res_vals))
    N['FpgaCtrlMin'] = '%.1f' % min(ctrl_vals)
    N['FpgaCtrlMax'] = '%.1f' % max(ctrl_vals)
    N['FpgaCtrlGeomean'] = '%.2f' % gm(ctrl_vals)
    N['FpgaXferMin'] = '%.1f' % min(xfer_vals)
    N['FpgaXferMax'] = '%.1f' % max(xfer_vals)
    N['FpgaXferGeomean'] = '%.1f' % gm(xfer_vals)
    N['FpgaWrapMin'] = '%.2f' % min(wrap_vals)
    N['FpgaWrapMax'] = '%.2f' % max(wrap_vals)
    N['FpgaUploadMin'] = '%.0f' % min(upload_vals)
    N['FpgaUploadMax'] = '{:,.0f}'.format(max(upload_vals))
    N['FpgaUploadGeomean'] = '%.1f' % gm(upload_vals)
    N['FpgaUploadShare'] = '%.0f' % (100.0 * gm(upload_vals) / cold[1000])
    N['FpgaColdMin'] = '%.0f' % min(cold_vals)
    N['FpgaColdMax'] = '{:,.0f}'.format(max(cold_vals))
    N['FpgaResMin'] = '%.1f' % min(res_vals)
    N['FpgaResMax'] = '%.1f' % max(res_vals)

    # Root-cause attribution and 1 -> 5 MHz TCK evidence, all derived from analysis.json / tcl_tcp_sim.json
    tclm = {m_['model']: m_ for m_ in jload(JT / 'tcl_tcp_sim.json')['models']}
    rows_5m = {r_['model']: r_ for r_ in ana['rows'] if r_['khz'] == 5000}
    assert set(rows_5m) == set(order) == set(tclm)
    N['FpgaEcgOneMs'], N['FpgaEcgFiveMs'] = '%.2f' % rows_1m['ecg']['resident_ms'], '%.2f' % rows_5m['ecg']['resident_ms']
    N['FpgaGtsrbInOneMs'], N['FpgaGtsrbInFiveMs'] = '%.2f' % rows_1m['gtsrb_l']['phases_ms']['input_upload'], '%.2f' % rows_5m['gtsrb_l']['phases_ms']['input_upload']
    N['FpgaGtsrbUpOneMs'], N['FpgaGtsrbUpFiveMs'] = '%.2f' % rows_1m['gtsrb_l']['upload_ms'], '%.2f' % rows_5m['gtsrb_l']['upload_ms']
    N['FpgaTckRatio'] = '%g' % (5000 / 1000)
    N['FpgaResFive'] = '%.2f' % res[5000]
    N['FpgaTckOutcome'] = '无净提速收益' if (res[5000] >= res[1000] and cold[5000] >= cold[1000]) else '存在净提速'
    assert all(tclm[m_]['model_bytes'] % 4 == 0 for m_ in order)
    mpw = [rows_1m[m_]['upload_ms'] / (tclm[m_]['model_bytes'] // 4) for m_ in order]
    N['FpgaUpMpwMin'], N['FpgaUpMpwMax'] = '%.2f' % min(mpw), '%.2f' % max(mpw)
    inw = lambda m_: -(-tclm[m_]['input_bytes'] // 4)
    N['FpgaEcgInWords'], N['FpgaEcgInRpcMs'] = str(inw('ecg')), '%.2f' % rows_1m['ecg']['phases_ms']['input_upload']
    N['FpgaKwsLInWords'], N['FpgaKwsLInRpcMs'] = str(inw('kws_l')), '%.2f' % rows_1m['kws_l']['phases_ms']['input_upload']
    N['FpgaGtsrbInWords'] = str(inw('gtsrb_l'))
    N['FpgaGtsrbInMpw'] = '%.2f' % (rows_1m['gtsrb_l']['phases_ms']['input_upload'] / inw('gtsrb_l'))
    drs = [tclm[m_]['pipeline']['total_drscans'] for m_ in order]
    assert all(tclm[m_]['batched']['total_drscans'] == tclm[m_]['pipeline']['total_drscans'] for m_ in order)
    N['RpcDrscanMin'], N['RpcDrscanMax'] = '{:,}'.format(min(drs)), '{:,}'.format(max(drs))

    summ = (FPGA / 'implementation/summary.md').read_text(encoding='utf-8')
    m = grab(r'WNS: ([\d.]+) ns; TNS: ([\d.]+) ns; WHS: ([\d.]+) ns', summ, 'implementation/summary.md timing')
    N['VivWns'], N['VivWhs'] = '+' + m[1], '+' + m[3]
    assert float(m[2]) == 0.0
    m = grab(r'Slice LUTs \| (\d+) \| (\d+) \| ([\d.]+)', summ, 'summary LUT')
    N['VivLut'], N['VivLutPct'] = '{:,}'.format(int(m[1])), m[3]
    m = grab(r'Slice Registers \| (\d+) \| (\d+) \| ([\d.]+)', summ, 'summary FF')
    N['VivFf'], N['VivFfPct'] = '{:,}'.format(int(m[1])), m[3]
    N['VivBram'] = grab(r'Block RAM Tile \| (\d+)', summ, 'summary BRAM')[1]
    N['VivDsp'] = grab(r'DSPs \| (\d+)', summ, 'summary DSP')[1]
    N['VivPart'] = esc(grab(r'Part: (\S+?);', summ, 'summary part')[1])
    N['VivClk'] = grab(r'(\d+) MHz output', summ, 'summary clock')[1]


# <<SECTION:rpc>>
def sec_rpc(N):
    rtl = jload(JT / 'rtl_1mhz_7models.json')
    tcl = jload(JT / 'tcl_tcp_sim.json')
    assert rtl['all_bit_exact'] and tcl['all_bit_exact']
    rtl_rows = {r['model']: r for r in rtl['models']}
    tcl_rows = {r['model']: r for r in tcl['models']}
    order = [r['model'] for r in tcl['models']]
    assert set(rtl_rows) == set(order)

    lines = []
    burst_ms, up_pct, tck_ratios = [], [], []
    batched_rpcs, pipe_rpcs, rpc_cuts, b_ms, p_ms, l_sp = [], [], [], [], [], []
    for mdl in order:
        rw = rtl_rows[mdl]
        tc = tcl_rows[mdl]
        b, p = tc['batched'], tc['pipeline']
        assert b['total_drscans'] == p['total_drscans']
        burst_ms.append(rw['burst_ms'])
        up_pct.append(rw['burst_model_upload_pct'])
        tck_ratios.append(rw['tck_ratio_16ns'])
        batched_rpcs.append(b['total_rpcs'])
        pipe_rpcs.append(p['total_rpcs'])
        rpc_cuts.append(tc['rpc_reduction_x'])
        b_ms.append(b['median_total_ms'])
        p_ms.append(p['median_total_ms'])
        l_sp.append(tc['loopback_speedup_x'])
        lines.append(
            f"{BS}texttt{{{esc(mdl)}}} & {rw['burst_ms']:.1f} & {rw['burst_model_upload_pct']:.1f}{BS}% "
            f"& {b['total_rpcs']} → {p['total_rpcs']} & {tc['rpc_reduction_x']:.1f}× "
            f"& {b['median_total_ms']:.1f} → {p['median_total_ms']:.1f} & {tc['loopback_speedup_x']:.1f}× {BS}{BS}\n"
        )
    (OUT / 'jtag_sim_rows.tex').write_text(''.join(lines), encoding='utf-8')

    anchors = rtl['wordwise_1mhz_anchors']
    assert len(anchors) >= 2
    N['RpcModels'] = str(len(order))
    exact_cnt = sum(bool(tc['batched']['exact'] and tc['pipeline']['exact']) for tc in tcl_rows.values())
    N['RpcExactCount'] = str(exact_cnt)
    N['RpcExactStatus'] = '传输一致' if (exact_cnt == len(order) and tcl['all_bit_exact']) else '存在差异'
    N['RtlTckMhz'] = '%g' % rtl['tck_mhz']
    N['RtlBurstMsMin'], N['RtlBurstMsMax'] = '%.1f' % min(burst_ms), '%.1f' % max(burst_ms)
    N['RtlUpPctMin'], N['RtlUpPctMax'] = '%.1f' % min(up_pct), '%.1f' % max(up_pct)
    N['RtlTckCutMin'], N['RtlTckCutMax'] = '%.2f' % min(tck_ratios), '%.2f' % max(tck_ratios)
    N['RtlAnchorCount'] = str(len(anchors))
    N['RtlAnchorWordMin'] = '%.1f' % min(a['wordwise_ms'] for a in anchors)
    N['RtlAnchorWordMax'] = '%.1f' % max(a['wordwise_ms'] for a in anchors)
    N['RtlAnchorBurstMin'] = '%.1f' % min(a['burst_ms'] for a in anchors)
    N['RtlAnchorBurstMax'] = '%.1f' % max(a['burst_ms'] for a in anchors)
    N['RtlAnchorSpMin'] = '%.1f' % min(a['wire_speedup_x'] for a in anchors)
    N['RtlAnchorSpMax'] = '%.1f' % max(a['wire_speedup_x'] for a in anchors)

    N['RpcBatchedMin'], N['RpcBatchedMax'] = str(min(batched_rpcs)), str(max(batched_rpcs))
    assert len(set(pipe_rpcs)) == 1
    N['RpcPipe'] = str(pipe_rpcs[0])
    N['RpcPipeLoad'] = str(tcl_rows[order[0]]['pipeline']['load_rpcs'])
    N['RpcPipeInfer'] = str(tcl_rows[order[0]]['pipeline']['infer_rpcs'])
    N['RpcSpMin'], N['RpcSpMax'] = '%.1f' % min(rpc_cuts), '%.1f' % max(rpc_cuts)
    N['RpcTcpBatchedMin'], N['RpcTcpBatchedMax'] = '%.1f' % min(b_ms), '%.1f' % max(b_ms)
    N['RpcTcpPipeMin'], N['RpcTcpPipeMax'] = '%.1f' % min(p_ms), '%.1f' % max(p_ms)
    N['RpcTcpSpMin'], N['RpcTcpSpMax'] = '%.1f' % min(l_sp), '%.1f' % max(l_sp)

    # Also summarize cached-manifest batched range from RTL e2e_timing.json (no estimates)
    e2e = [r_ for r_ in jload(JT / 'e2e_timing.json') if r_['protocol'] == 'burst']
    cached_batched = [r_['rpc_counts']['batched']['load_rpcs'] + r_['rpc_counts']['batched']['infer_rpcs'] for r_ in e2e]
    N['RpcCachedMin'], N['RpcCachedMax'] = str(min(cached_batched)), str(max(cached_batched))


# <<SECTION:demo>>
def sec_demo(N):
    s = jload(DEMO / 'summary.json')
    demo_ok = all(c['passed'] for c in s['checks'].values())
    assert demo_ok
    N['DemoUnitStatus'] = '全部通过' if demo_ok else '存在失败'
    N['DemoExactStatus'] = 'bit-exact' if (s['golden_complete_outputs_equal'] == s['golden_frames'] and demo_ok) else 'MISMATCH'
    N['DemoUnit'], N['DemoJtag'] = str(s['demo_unit_tests']), str(s['jtag_unit_tests'])
    N['DemoGolden'], N['DemoGoldenTotal'] = str(s['golden_complete_outputs_equal']), str(s['golden_frames'])
    assert s['golden_complete_outputs_equal'] == s['golden_frames']
    N['DemoZoo'] = str(s['existing_zoo_frames'])
    N['DemoFpga'] = 'true' if s['fpga_validated'] else 'false'
    emo = jload(MODELS / 'emotion/training_result.json')['evaluation']
    face = jload(MODELS / 'face/training_result.json')['evaluation']
    N['EmoAcc'], N['EmoSamples'] = '%.2f' % (100 * emo['accuracy']), '{:,}'.format(emo['samples'])
    N['FaceP'], N['FaceR'], N['FaceF'] = ('%.1f' % (100 * face[k]) for k in ('precision', 'recall', 'f1'))
    demo_py = (ROOT / 'software/host/demo.py').read_text(encoding='utf-8')
    N['NumScenes'] = str(len(re.findall(r'^\s+"\w+": \(', demo_py, re.M)))
    tests = (ROOT / 'software/tests/test_demos.py').read_text(encoding='utf-8')
    fixes = [
        'test_browser_restart_waits_for_the_previous_upload_queue',
        'test_phone_follow_resumes_after_gap_and_ignores_partial_tail',
        'test_fpga_validation_requires_every_vector_from_every_demo',
    ]
    assert all('def ' + t + '(' in tests for t in fixes)
    N['DemoFixes'] = str(len(fixes))
    gap = grab(r'dt > ([\d.]+)', (ROOT / 'software/host/demos/signals.py').read_text(encoding='utf-8'), 'signals.py')
    N['HarGapMs'] = '%.0f' % (float(gap[1]) * 1e3)


def main():
    OUT.mkdir(exist_ok=True)
    N = {}
    for sec in (sec_asic, sec_physical_signoff, sec_mmmc, sec_power, sec_rtl, sec_fpga, sec_rpc, sec_demo):
        sec(N)
    bad = [k for k in N if not re.fullmatch(r'[A-Za-z]+', k)]
    assert not bad, bad
    (OUT / 'numbers.tex').write_text(
        '% generated by make_data.py -- do not edit\n'
        + ''.join(f'\\newcommand{{\\N{k}}}{{{v}}}\n' for k, v in N.items()),
        encoding='utf-8',
    )
    for k, v in N.items():
        print(f'{k:22s} {v}')


if __name__ == '__main__':
    main()
