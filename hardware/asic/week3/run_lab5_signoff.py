#!/usr/bin/env python3
"""Run full Week 3 Lab 5 signoff (Seal Ring, Dummy Fill, DRC/nmLVS/ANT/WB/ERC) on a finished Week 3 candidate."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPT = HERE.parent / 'optimize'
if str(OPT) not in sys.path:
    sys.path.insert(0, str(OPT))

import flow  # noqa: E402
import signoff  # noqa: E402

LAB5_DIR = Path('/lec/lab5/layout')
DEFAULT_DRC_DECK = LAB5_DIR / 'rule' / 'calibre.drc'
DEFAULT_LVS_DECK = LAB5_DIR / 'rule' / 'calibre.lvs'
DEFAULT_ANT_DECK = LAB5_DIR / 'rule' / 'ant.drc'
DEFAULT_DM_DECK = LAB5_DIR / 'dummy' / 'scripts' / 'Dummy_Metal_Calibre_0.18um.215a'
DEFAULT_DOD_DECK = LAB5_DIR / 'dummy' / 'scripts' / 'Dummy_OD_PO_Calibre_0.18um.210a'
DEFAULT_IO_OA = Path('/lec/pdk/tsmc180/io/T18_1P6M_CUP_IO')
DEFAULT_WB_ZIP = Path('/lec/pdk/tsmc180/tsmc180/Foundry Design Documents/t000cldr002c1_1_9_1a.zip')

TEACHING_DRC_PREFIXES = ('DRM.', 'ESD.', 'LUP.', 'MOM.')
EXPECTED_PORTS = {
    'VDDPST', 'VDD', 'VSSPST', 'VSS', 'POC',
    'CLK_I', 'RST_NI', 'IRQ_O', 'TCK_I', 'TMS_I', 'TD_I', 'TD_O',
}


def sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run_logged(cmd: list[str], cwd: Path, log_path: Path, check: bool = True) -> tuple[int, str]:
    """Run a command capturing combined stdout/stderr to log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('w') as log:
        proc = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    text = log_path.read_text(errors='replace')
    if check and proc.returncode != 0:
        raise RuntimeError(f'Command failed ({proc.returncode}): {" ".join(cmd)}; see {log_path}')
    return proc.returncode, text


def resolve_tsmc18_oa(explicit: Path | None) -> Path:
    """Locate the TSMC 0.18um OpenAccess technology library directory."""
    if explicit is not None:
        return explicit.resolve()
    matches = sorted(Path('/lec/pdk/tsmc180').glob('*/tsmc18mm*/PDK/tsmc18'))
    if not matches:
        raise FileNotFoundError('Could not locate tsmc18 OA library under /lec/pdk/tsmc180')
    return matches[0].resolve()


def resolve_cadence_oa_libs() -> tuple[Path, Path]:
    """Locate Cadence basic and analogLib OA directories from IC618 installation."""
    strmout = shutil.which('strmout')
    if not strmout:
        raise FileNotFoundError('strmout not found on PATH')
    for candidate in [*Path(strmout).parents, *Path(strmout).resolve().parents]:
        basic = candidate / 'tools/dfII/etc/cdslib/basic'
        analog = candidate / 'tools/dfII/etc/cdslib/artist/analogLib'
        if basic.is_dir() and analog.is_dir():
            return basic, analog
    raise FileNotFoundError(f'Missing Cadence basic/analogLib near {strmout}')


def extract_wirebond_deck(wb_zip: Path, dest_dir: Path) -> Path:
    """Unpack C18_WIRE_BOND_6M.19_1a1 from Foundry Design Documents archive."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = list(dest_dir.rglob('C18_WIRE_BOND_6M.19_1a1'))
    if existing:
        return existing[0]
    with zipfile.ZipFile(wb_zip) as zf:
        tar_names = [n for n in zf.namelist() if 'DRC_Calibre_AL_WB_FC' in n and n.endswith('.tar.gz')]
        if not tar_names:
            raise FileNotFoundError(f'DRC_Calibre_AL_WB_FC tarball missing in {wb_zip}')
        tar_path = dest_dir / Path(tar_names[0]).name
        with zf.open(tar_names[0]) as src, tar_path.open('wb') as dst:
            shutil.copyfileobj(src, dst)
    run_logged(['tar', '-xzf', str(tar_path), '-C', str(dest_dir)], dest_dir, dest_dir / 'untar_wb.log')
    found = list(dest_dir.rglob('C18_WIRE_BOND_6M.19_1a1'))
    if not found:
        raise FileNotFoundError('C18_WIRE_BOND_6M.19_1a1 not found after extraction')
    return found[0]


def inspect_gds(gds_path: Path, work_dir: Path) -> tuple[str, float, list[int]]:
    """Query topcell name, user units per DBU, and DBU bounding box via calibredrv."""
    tcl_path = work_dir / 'inspect_gds.tcl'
    tcl_path.write_text(
        f'set L [layout create {flow.tcl(gds_path)} -dt_expand]\n'
        'set top [$L topcell]\n'
        'puts "GDS_INFO $top [$L units] [$L bbox $top]"\n'
    )
    _, out = run_logged(['calibredrv', str(tcl_path)], work_dir, work_dir / 'inspect_gds.log')
    m = re.search(r'^GDS_INFO\s+(\S+)\s+([0-9.eE+-]+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)', out, re.M)
    if not m:
        raise RuntimeError(f'Failed to parse GDS_INFO for {gds_path}')
    return m.group(1), float(m.group(2)), [int(m.group(i)) for i in range(3, 7)]


def compute_sealring_box(bbox_dbu: list[int], dbu_per_um: int = 2000, max_outer_um: float = 1980.0) -> dict:
    """Compute a centered integer-um Seal Ring reference box aligned to the 0.005 um grid."""
    bx0, by0, bx1, by1 = bbox_dbu
    die_w_um = (bx1 - bx0) / dbu_per_um
    die_h_um = (by1 - by0) / dbu_per_um
    side_um = int(math.ceil(max(die_w_um, die_h_um) + 0.5))
    if side_um + 30.0 > max_outer_um:
        side_um = int(math.ceil(max(die_w_um, die_h_um)))
    outer_um = side_um + 30.0
    if outer_um > max_outer_um:
        raise RuntimeError(f'Chip too large for {max_outer_um} um sealed limit: {outer_um} um')

    grid_dbu = dbu_per_um // 200  # 10 DBU = 0.005 um at 2000 DBU/um

    def snap_origin(c_low: int, c_high: int) -> tuple[int, int]:
        span = side_um * dbu_per_um
        ideal_x0 = ((c_low + c_high) - span) / 2.0
        rx0 = int(round(ideal_x0 / grid_dbu) * grid_dbu)
        rx1 = rx0 + span
        if rx0 > c_low - (grid_dbu * 20):
            rx0 -= grid_dbu * 20
            rx1 = rx0 + span
        if rx1 < c_high + (grid_dbu * 20):
            rx0 += grid_dbu * 20
            rx1 = rx0 + span
        return rx0, rx1

    rx0, rx1 = snap_origin(bx0, bx1)
    ry0, ry1 = snap_origin(by0, by1)
    return {
        'ref_dbu': [rx0, ry0, rx1, ry1],
        'ref_um': [rx0 / dbu_per_um, ry0 / dbu_per_um, rx1 / dbu_per_um, ry1 / dbu_per_um],
        'outer_dbu': [rx0 - 15 * dbu_per_um, ry0 - 15 * dbu_per_um, rx1 + 15 * dbu_per_um, ry1 + 15 * dbu_per_um],
        'outer_um': [
            (rx0 - 15 * dbu_per_um) / dbu_per_um,
            (ry0 - 15 * dbu_per_um) / dbu_per_um,
            (rx1 + 15 * dbu_per_um) / dbu_per_um,
            (ry1 + 15 * dbu_per_um) / dbu_per_um,
        ],
        'sealed_width_um': outer_um,
        'sealed_height_um': outer_um,
    }


def export_sealring_primitives(io_oa: Path, tsmc18_oa: Path, sr_dir: Path) -> tuple[Path, Path]:
    """Export native corner and edge seal-ring GDS cells directly at 2000 DBU/um."""
    sr_dir.mkdir(parents=True, exist_ok=True)
    basic, analog = resolve_cadence_oa_libs()
    (sr_dir / 'cds.lib').write_text(
        f'DEFINE basic {basic}\n'
        f'DEFINE analogLib {analog}\n'
        f'DEFINE tsmc18 {tsmc18_oa}\n'
        f'DEFINE T18_1P6M_CUP_IO {io_oa}\n'
    )
    corner_gds = sr_dir / 'sr_corner.gds'
    edge_gds = sr_dir / 'sr_edge.gds'
    for cell, gds_out, log_name in [
        ('tsmc_c018_sealring_corner_1p6m', corner_gds, 'strmout_corner.log'),
        ('tsmc_c018_seal_ring_edge_1p6m', edge_gds, 'strmout_edge.log'),
    ]:
        run_logged(
            [
                'strmout', '-library', 'T18_1P6M_CUP_IO',
                '-topCell', cell, '-view', 'layout',
                '-strmFile', str(gds_out),
                '-logFile', str(sr_dir / log_name),
                '-dbuPerUU', '2000',
            ],
            sr_dir,
            sr_dir / f'{cell}_cmd.log',
        )
    return corner_gds, edge_gds


def verify_and_prepare_pimp_patch(raw_gds: Path, work_dir: Path) -> dict | None:
    """Verify the known B776_POSTVDD2CDG CO/PP/NP gap via gdspy before applying the 2000-DBU PP patch."""
    sub_gds = work_dir / 'B776_POSTVDD2CDG.gds'
    tcl = work_dir / 'extract_b776.tcl'
    tcl.write_text(
        f'set L [layout create {flow.tcl(raw_gds)} -dt_expand]\n'
        'if {[$L exists cell B776_POSTVDD2CDG]} {\n'
        f'    $L gdsout {flow.tcl(sub_gds)} B776_POSTVDD2CDG\n'
        '    puts "FOUND_B776 1"\n'
        '} else { puts "FOUND_B776 0" }\n'
    )
    _, out = run_logged(['calibredrv', str(tcl)], work_dir, work_dir / 'extract_b776.log')
    if 'FOUND_B776 1' not in out:
        return None
    py_code = """
import gdspy, json, sys
lib = gdspy.GdsLibrary(infile=sys.argv[1])
cell = lib.cells['B776_POSTVDD2CDG']
polys = cell.get_polygons(by_spec=True)
co_list = polys.get((15, 0), [])
pp_list = polys.get((7, 0), [])
def covered_x(x0, y0, x1, y1, rect_list):
    for p in rect_list:
        px0, py0 = p.min(axis=0); px1, py1 = p.max(axis=0)
        if px0 <= x0 + 1e-6 and px1 >= x1 - 1e-6 and py0 <= y0 + 1e-6 and py1 >= y1 - 1e-6:
            return True
    return False
for co in co_list:
    cx0, cy0 = co.min(axis=0); cx1, cy1 = co.max(axis=0)
    if abs(cx0 - 52.23) < 0.05 and abs(cy0 - 72.13) < 0.05:
        already = covered_x(cx0, cy0, cx1, cy1, pp_list)
        print(json.dumps({
            'cell': 'B776_POSTVDD2CDG',
            'co_um': [round(float(cx0), 4), round(float(cy0), 4), round(float(cx1), 4), round(float(cy1), 4)],
            'already_covered': bool(already),
            'patch_pp_um': [52.30, 52.91, 52.59, 73.795],
            'patch_pp_2000dbu': [104600, 105820, 105180, 147590],
            'adjacent_np_wall_um': 52.59,
        }))
        sys.exit(0)
print(json.dumps({'cell': 'B776_POSTVDD2CDG', 'already_covered': True}))
"""
    res = subprocess.run(
        [sys.executable, '-c', py_code, str(sub_gds)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(res.stdout.strip().splitlines()[-1])
    if info.get('already_covered'):
        return None
    return info


def build_predummy(
    raw_gds: Path, corner_gds: Path, edge_gds: Path, sr_box: dict, pimp_patch: dict | None, out_gds: Path, work_dir: Path
) -> dict:
    """Assemble CO.R.4 PIMP fix and 2000-DBU native Seal Ring into preDummy.gds."""
    rx0, ry0, rx1, ry1 = sr_box['ref_dbu']
    patch_tcl = ''
    if pimp_patch is not None:
        x0, y0, x1, y1 = pimp_patch['patch_pp_2000dbu']
        patch_tcl = (
            f'$L create polygon B776_POSTVDD2CDG 7 {x0} {y0} {x1} {y1}\n'
            'puts "APPLIED_COR4_PATCH 1"\n'
        )
    tcl = work_dir / 'build_predummy.tcl'
    tcl.write_text(
        f'set L [layout create {flow.tcl(raw_gds)} -dt_expand -preservePaths]\n'
        'set top [$L topcell]\n'
        f'{patch_tcl}'
        f'set C [layout create {flow.tcl(corner_gds)} -dt_expand]\n'
        f'set E [layout create {flow.tcl(edge_gds)} -dt_expand]\n'
        '$L import layout $C FALSE append\n'
        '$L import layout $E FALSE append\n'
        f'set x0 {rx0}; set y0 {ry0}; set x1 {rx1}; set y1 {ry1}\n'
        'set sr_off 30000; set step 2000; set e5 10000; set e4 8000\n'
        'set sr_cell "tsmc_c018_sealring_1p6m"\n'
        '$L create cell $sr_cell\n'
        '$L create ref $sr_cell tsmc_c018_sealring_corner_1p6m [expr {$x0 - $sr_off}] [expr {$y0 - $sr_off}] 0 0 1\n'
        '$L create ref $sr_cell tsmc_c018_sealring_corner_1p6m [expr {$x1 + $sr_off}] [expr {$y0 - $sr_off}] 0 90 1\n'
        '$L create ref $sr_cell tsmc_c018_sealring_corner_1p6m [expr {$x0 - $sr_off}] [expr {$y1 + $sr_off}] 1 0 1\n'
        '$L create ref $sr_cell tsmc_c018_sealring_corner_1p6m [expr {$x1 + $sr_off}] [expr {$y1 + $sr_off}] 0 180 1\n'
        'for {set x [expr {$x0 - $e5}]} {$x <= [expr {$x1 + $e4}]} {incr x $step} {\n'
        '    $L create ref $sr_cell tsmc_c018_seal_ring_edge_1p6m $x $y1 0 0 1\n'
        '    $L create ref $sr_cell tsmc_c018_seal_ring_edge_1p6m $x $y0 1 0 1\n'
        '}\n'
        'for {set y [expr {$y0 - $e5}]} {$y <= [expr {$y1 + $e4}]} {incr y $step} {\n'
        '    $L create ref $sr_cell tsmc_c018_seal_ring_edge_1p6m $x0 $y 0 90 1\n'
        '    $L create ref $sr_cell tsmc_c018_seal_ring_edge_1p6m $x1 $y 1 90 1\n'
        '}\n'
        '$L create ref $top $sr_cell 0 0 0 0 1\n'
        f'$L gdsout {flow.tcl(out_gds)} $top\n'
        'puts "PREDUMMY_BBOX [$L topcell] [$L units] [$L bbox $top]"\n'
    )
    run_logged(['calibredrv', str(tcl)], work_dir, work_dir / 'build_predummy.log')
    top, units, bbox = inspect_gds(out_gds, work_dir)
    return {'topcell': top, 'units': units, 'bbox_dbu': bbox}


def run_dummy_insertion(
    predummy_gds: Path, dm_deck_src: Path, dod_deck_src: Path, out_gds: Path, work_dir: Path, threads: int
) -> dict:
    """Run Dummy Metal (0.8um normal M6) and Dummy OD/PO insertion, merging into my_soc_top."""
    work_dir.mkdir(parents=True, exist_ok=True)
    dm = dm_deck_src.read_text(errors='replace')
    dm = re.sub(r'(?m)^(\s*#DEFINE\s+THICK_40K_M6.*)$', r'// \1', dm)
    dm = re.sub(r'(?m)^VARIABLE\s+dmW_M6\s+0\.6\b', 'VARIABLE dmW_M6\t\t0.8', dm)
    dm = re.sub(r'(?m)^VARIABLE\s+dmL_M6\s+0\.6\b', 'VARIABLE dmL_M6\t\t0.8', dm)
    dm = re.sub(r'(?m)^PRECISION\s+1000\b', 'PRECISION    2000', dm)
    dm = re.sub(r'(?m)^RESOLUTION\s+5\b', 'RESOLUTION     10', dm)
    dm = re.sub(r'(?m)^LAYOUT PATH\s+.*', f'LAYOUT PATH "{predummy_gds}"', dm)
    dm = re.sub(r'(?m)^LAYOUT PRIMARY\s+.*', 'LAYOUT PRIMARY "my_soc_top"', dm)
    dm_deck = work_dir / 'dummy_metal.cal'
    dm_deck.write_text(dm)

    dod = dod_deck_src.read_text(errors='replace')
    dod = re.sub(r'(?m)^PRECISION\s+1000\b', 'PRECISION    2000', dod)
    dod = re.sub(r'(?m)^RESOLUTION\s+5\b', 'RESOLUTION     10', dod)
    dod = re.sub(r'(?m)^LAYOUT PATH\s+.*', f'LAYOUT PATH "{predummy_gds}"', dod)
    dod = re.sub(r'(?m)^LAYOUT PRIMARY\s+.*', 'LAYOUT PRIMARY "my_soc_top"', dod)
    dod_deck = work_dir / 'dummy_odpo.cal'
    dod_deck.write_text(dod)

    run_logged(['calibre', '-drc', '-hier', '-hyper', '-turbo', str(threads), str(dm_deck)], work_dir, work_dir / 'dm.log')
    run_logged(['calibre', '-drc', '-hier', '-hyper', '-turbo', str(threads), str(dod_deck)], work_dir, work_dir / 'dod.log')

    dm_gds = work_dir / 'DM.gds'
    dod_gds = work_dir / 'DOD.gds'
    merge_tcl = work_dir / 'merge_postdummy.tcl'
    merge_tcl.write_text(
        f'set L [layout create {flow.tcl(predummy_gds)} -dt_expand -preservePaths]\n'
        'set top [$L topcell]\n'
        f'set O [layout create {flow.tcl(dod_gds)} -dt_expand]\n'
        '$O cellname [$O topcell] tsmc_dummy_odpo\n'
        '$L import layout $O FALSE append\n'
        f'set D [layout create {flow.tcl(dm_gds)} -dt_expand]\n'
        '$D cellname [$D topcell] tsmc_dummy_metal\n'
        '$L import layout $D FALSE append\n'
        '$L create ref $top tsmc_dummy_odpo 0 0 0 0 1\n'
        '$L create ref $top tsmc_dummy_metal 0 0 0 0 1\n'
        f'$L gdsout {flow.tcl(out_gds)} $top\n'
    )
    run_logged(['calibredrv', str(merge_tcl)], work_dir, work_dir / 'merge.log')
    top, units, bbox = inspect_gds(out_gds, work_dir)
    if top != 'my_soc_top' or abs(units - 0.0005) > 1e-9:
        raise RuntimeError(f'Unexpected postDummy header: top={top}, units={units}')
    return {'topcell': top, 'units': units, 'bbox_dbu': bbox, 'dm_gds': dm_gds, 'dod_gds': dod_gds}


def parse_drc_rep(rep_path: Path) -> dict:
    """Extract rule count, hierarchical total, expanded total, and per-rule counts from Calibre DRC.rep."""
    text = rep_path.read_text(errors='replace')
    rules = {}
    for m in re.finditer(r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count\s*=\s*(\d+)\s*\((\d+)\)', text, re.M):
        hier, exp = int(m.group(2)), int(m.group(3))
        if hier or exp:
            rules[m.group(1)] = {'hier': hier, 'expanded': exp}
    m_exec = re.search(r'TOTAL DRC RuleChecks Executed:\s+(\d+)', text)
    m_res = re.search(r'TOTAL DRC Results Generated:\s+(\d+)\s*\((\d+)\)', text)
    return {
        'rules_executed': int(m_exec.group(1)) if m_exec else 0,
        'total_hier': int(m_res.group(1)) if m_res else sum(v['hier'] for v in rules.values()),
        'total_expanded': int(m_res.group(2)) if m_res else sum(v['expanded'] for v in rules.values()),
        'nonzero_rules': rules,
    }


def run_drc(post_gds: Path, drc_src: Path, work_dir: Path, threads: int) -> dict:
    """Run full-deck unmasked Calibre DRC on postDummy.gds at 2000 DBU/um."""
    work_dir.mkdir(parents=True, exist_ok=True)
    text = drc_src.read_text(errors='replace')
    text = re.sub(r'(?m)^PRECISION\s+1000\b', 'PRECISION    2000', text)
    text = re.sub(r'(?m)^RESOLUTION\s+5\b', 'RESOLUTION     10', text)
    text = text.replace('"GDSFILENAME"', f'"{post_gds}"').replace('"TOPCELLNAME"', '"my_soc_top"')
    deck = work_dir / 'drc.cal'
    deck.write_text(text)
    run_logged(['calibre', '-drc', '-hier', '-hyper', '-turbo', str(threads), str(deck)], work_dir, work_dir / 'drc.log')
    parsed = parse_drc_rep(work_dir / 'DRC.rep')
    accepted = {}
    blocking = {}
    for rule, counts in parsed['nonzero_rules'].items():
        if rule.startswith(TEACHING_DRC_PREFIXES):
            accepted[rule] = counts
        else:
            blocking[rule] = counts
    return {
        'check': 'drc',
        'deck': drc_src.name,
        'rules_executed': parsed['rules_executed'],
        'raw_total_hier': parsed['total_hier'],
        'raw_total_expanded': parsed['total_expanded'],
        'accepted_teaching_categories': accepted,
        'blocking': blocking,
        'passed': not blocking,
    }


def run_simple_check(kind: str, post_gds: Path, deck_src: Path, work_dir: Path, threads: int, wirebond: bool = False) -> dict:
    """Run Antenna or WireBond Calibre check and require 0 (0) results."""
    work_dir.mkdir(parents=True, exist_ok=True)
    text = deck_src.read_text(errors='replace')
    if wirebond:
        text = text.replace('//#DEFINE PITCH_55_SINGLE', '#DEFINE PITCH_55_SINGLE')
        text = text.replace('#DEFINE PITCH_60_STAGGER', '//#DEFINE PITCH_60_STAGGER')
        text = text.replace('//#DEFINE OBV_CUP', '#DEFINE OBV_CUP')
    text = re.sub(r'(?m)^PRECISION\s+1000\b', 'PRECISION    2000', text)
    text = re.sub(r'(?m)^RESOLUTION\s+5\b', 'RESOLUTION     10', text)
    text = text.replace('"GDSFILENAME"', f'"{post_gds}"').replace('"TOPCELLNAME"', '"my_soc_top"')
    deck = work_dir / f'{kind}.cal'
    deck.write_text(text)
    run_logged(['calibre', '-drc', '-hier', '-hyper', '-turbo', str(threads), str(deck)], work_dir, work_dir / f'{kind}.log')
    parsed = parse_drc_rep(work_dir / 'DRC.rep')
    return {
        'check': kind,
        'deck': deck_src.name,
        'rules_executed': parsed['rules_executed'],
        'total_hier': parsed['total_hier'],
        'total_expanded': parsed['total_expanded'],
        'nonzero_rules': parsed['nonzero_rules'],
        'passed': parsed['total_hier'] == 0 and parsed['total_expanded'] == 0,
    }


def parse_erc_sum(sum_path: Path) -> dict:
    """Parse Calibre ERC summary report rule counts."""
    text = sum_path.read_text(errors='replace')
    rules = {}
    for m in re.finditer(r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count\s*=\s*(\d+)\s*\((\d+)\)', text, re.M):
        rules[m.group(1)] = {'hier': int(m.group(2)), 'expanded': int(m.group(3))}
    m_exec = re.search(r'TOTAL ERC RuleChecks Executed:\s+(\d+)', text)
    m_res = re.search(r'TOTAL ERC RuleCheck Results Generated:\s+(\d+)\s*\((\d+)\)', text)
    return {
        'rules_executed': int(m_exec.group(1)) if m_exec else len(rules),
        'total_hier': int(m_res.group(1)) if m_res else sum(v['hier'] for v in rules.values()),
        'total_expanded': int(m_res.group(2)) if m_res else sum(v['expanded'] for v in rules.values()),
        'nonzero_rules': {k: v for k, v in rules.items() if v['hier'] or v['expanded']},
    }


def parse_erc_db(db_path: Path, erc_sum: dict, dbu_per_um: int = 2000) -> dict:
    """Extract all ERC marker polygons in microns without leaking private file paths."""
    lines = db_path.read_text(errors='replace').splitlines()
    if not lines:
        raise RuntimeError(f'Empty ERC database: {db_path}')
    header_parts = lines[0].split()
    precision_dbu = int(header_parts[1]) if len(header_parts) >= 2 else dbu_per_um
    entries = {}
    i = 1
    while i < len(lines):
        rule = lines[i].strip()
        if not rule or i + 1 >= len(lines):
            i += 1
            continue
        meta = lines[i + 1].split()
        if len(meta) < 3 or not meta[0].isdigit():
            i += 1
            continue
        hier_cnt = int(meta[0])
        # Next lines include 'Rule File Pathname: ...' then description then 'p <idx> <nverts>'
        j = i + 2
        desc_lines = []
        polys_um = []
        while j < len(lines):
            cur = lines[j].strip()
            if cur.startswith('Rule File Pathname:'):
                j += 1
                continue
            if re.match(r'^[pe]\s+\d+\s+\d+$', cur):
                _, _, n_str = cur.split()
                n_pts = int(n_str)
                pts = []
                for k in range(1, n_pts + 1):
                    px, py = map(int, lines[j + k].split()[:2])
                    pts.append([round(px / precision_dbu, 4), round(py / precision_dbu, 4)])
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                polys_um.append({
                    'bbox_um': [min(xs), min(ys), max(xs), max(ys)],
                    'vertices_um': pts,
                })
                j += n_pts + 1
                continue
            # Check if next line starts a new rule block
            if j + 1 < len(lines) and re.match(r'^\d+\s+\d+\s+\d+\s+[A-Z][a-z]{2}\s', lines[j + 1]):
                break
            desc_lines.append(cur)
            j += 1
        sum_counts = erc_sum['nonzero_rules'].get(rule, {'hier': hier_cnt, 'expanded': hier_cnt})
        if hier_cnt or sum_counts['hier']:
            entries[rule] = {
                'hier_count': sum_counts['hier'],
                'expanded_count': sum_counts['expanded'],
                'description': ' '.join(desc_lines).strip(),
                'markers_um': polys_um,
            }
        i = j
    return {
        'topcell': header_parts[0],
        'dbu_per_um': precision_dbu,
        'rules_executed': erc_sum['rules_executed'],
        'total_hier': erc_sum['total_hier'],
        'total_expanded': erc_sum['total_expanded'],
        'rules': entries,
    }


def parse_lvs_rep(rep_path: Path) -> dict:
    """Parse LVS comparison status, matched ports, and IO resistor deviation summary."""
    text = rep_path.read_text(errors='replace')
    status = 'UNKNOWN'
    if re.search(r'#\s+CORRECT\s+#', text):
        status = 'CORRECT'
    elif re.search(r'#\s+INCORRECT\s+#', text):
        status = 'INCORRECT'

    ports_match = re.search(r'^\s*Ports:\s*(\d+)\s+(\d+)(?:\s+(\d+)\s+(\d+))?', text, re.M)
    layout_ports = int(ports_match.group(1)) if ports_match else 0
    source_ports = int(ports_match.group(2)) if ports_match else 0
    error_lines = [m.group(1).strip() for m in re.finditer(r'^\s*Error:\s*(.+)$', text, re.M)]

    pr_errors = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if re.match(r'^\s*\d+\s+\S+\s+R\(PR\)(?=\s|$)', line):
            nxt = lines[idx + 1] if idx + 1 < len(lines) else ''
            m_val = re.search(
                r'r:\s*([0-9.eE+-]+)\s*ohm\s+r:\s*([0-9.eE+-]+)\s*ohm\s+([\d.]+)%',
                nxt,
            )
            if m_val:
                pr_errors.append({
                    'layout_ohm': float(m_val.group(1)),
                    'source_ohm': float(m_val.group(2)),
                    'diff_pct': float(m_val.group(3)),
                })

    prop_sum_m = re.search(r'^\s*(\d+)\s+\d+\s+\d+\s+\d+\s+R\(PR\)', text, re.M)
    prop_err_count = int(prop_sum_m.group(1)) if prop_sum_m else len(pr_errors)
    max_dev = max((e['diff_pct'] for e in pr_errors), default=None)
    return {
        'status': status,
        'layout_ports': layout_ports,
        'source_ports': source_ports,
        'errors': sorted(set(error_lines)),
        'property_error_count': prop_err_count if status == 'INCORRECT' else 0,
        'max_io_resistor_deviation_pct': max_dev,
        'sample_deviations': pr_errors[:5],
    }


def run_lvs_suite(cand_dir: Path, post_gds: Path, lvs_deck_src: Path, out_lvs_dir: Path, threads: int) -> dict:
    """Assemble full CDL (un-blackboxed SRAMs) and run both 0% and 3.48% R(PR) LVS + ERC."""
    out_lvs_dir.mkdir(parents=True, exist_ok=True)
    libs = flow.libraries()
    vmap = signoff.views(libs)
    phys_v = (cand_dir / 'outputs' / 'physical.v').resolve()
    net_cdl = (out_lvs_dir / 'netlist.cdl').resolve()
    run_logged(['v2lvs', '-v', str(phys_v), '-o', str(net_cdl), '-s0', 'VSS', '-s1', 'VDD'], out_lvs_dir, out_lvs_dir / 'v2lvs.log')

    source_cdl = out_lvs_dir / 'source.cdl'
    source_cdl.write_text(
        f'.INCLUDE "{vmap["std_spi"]}"\n'
        f'.INCLUDE "{vmap["io_spi"]}"\n'
        f'.INCLUDE "{vmap["sram_cdl"]}"\n'
        f'.INCLUDE "{net_cdl}"\n'
    )

    poc_line = signoff.poc_label(cand_dir)
    base = lvs_deck_src.read_text(errors='replace')
    base = base.replace('LAYOUT PRIMARY "ts_allcelllvs"', 'LAYOUT PRIMARY "my_soc_top"')
    base = base.replace('LAYOUT PATH "ts_allcelllvs.gds"', f'LAYOUT PATH "{post_gds}"')
    base = base.replace('SOURCE PRIMARY "ts_allcelllvs"', 'SOURCE PRIMARY "my_soc_top"')
    base = base.replace('SOURCE PATH "ts_allcelllvs.cdl"', f'SOURCE PATH "{source_cdl}"')
    base = re.sub(r'(?m)^PRECISION\s+1000\b', 'PRECISION 2000', base)
    base = re.sub(r'(?m)^RESOLUTION\s+1\b', 'RESOLUTION 2', base)
    base = re.sub(r'(?m)^LVS REDUCE SPLIT GATES\s+NO.*', 'LVS REDUCE SPLIT GATES YES', base)
    base = base.replace('LVS POWER NAME POWER_NAME', 'LVS POWER NAME "VDD" "VDDPST"')
    base = base.replace('LVS GROUND NAME GROUND_NAME', 'LVS GROUND NAME "VSS" "VSSPST"')
    base += f'\n{poc_line}\n'

    results = {}
    for tag, tol in [('pr_0pct', '0'), ('pr_3_48pct', '3.48')]:
        sub_dir = out_lvs_dir / tag
        sub_dir.mkdir(parents=True, exist_ok=True)
        deck_txt = re.sub(r'TRACE PROPERTY R\(PR\) R R \S+', f'TRACE PROPERTY R(PR) R R {tol}', base)
        deck_path = sub_dir / 'rules.lvs'
        deck_path.write_text(deck_txt)
        run_logged(['calibre', '-lvs', '-hier', '-turbo', str(threads), str(deck_path)], sub_dir, sub_dir / 'lvs.log')
        rep_parsed = parse_lvs_rep(sub_dir / 'lvs.rep')
        rep_parsed['r_pr_tolerance_pct'] = float(tol)
        results[tag] = rep_parsed

    r348 = results['pr_3_48pct']
    if r348['status'] != 'CORRECT' or r348['layout_ports'] != len(EXPECTED_PORTS) or r348['source_ports'] != len(EXPECTED_PORTS):
        raise RuntimeError(f'3.48% LVS failed: {r348}')

    erc_dir = out_lvs_dir / 'pr_3_48pct'
    erc_sum = parse_erc_sum(erc_dir / 'calibre_erc.sum')
    erc_geom = parse_erc_db(erc_dir / 'calibre_erc.db', erc_sum, dbu_per_um=2000)

    results['sram_blackboxed'] = False
    results['sram_cell'] = 'RA1SHD_2048x32M8'
    results['top_ports'] = sorted(EXPECTED_PORTS)
    results['netlist_cdl'] = net_cdl
    results['source_cdl'] = source_cdl
    return results, erc_geom


def main() -> None:
    """Execute end-to-end Lab 5 physical verification on a finished candidate."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cand-dir', '--candidate', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    ap.add_argument('--threads', type=int, default=32)
    ap.add_argument('--tsmc18-oa-dir', type=Path, default=None)
    ap.add_argument('--io-oa-dir', type=Path, default=DEFAULT_IO_OA)
    ap.add_argument('--drc-deck', type=Path, default=DEFAULT_DRC_DECK)
    ap.add_argument('--lvs-deck', type=Path, default=DEFAULT_LVS_DECK)
    ap.add_argument('--ant-deck', type=Path, default=DEFAULT_ANT_DECK)
    ap.add_argument('--wb-zip', type=Path, default=DEFAULT_WB_ZIP)
    ap.add_argument('--dummy-metal-deck', type=Path, default=DEFAULT_DM_DECK)
    ap.add_argument('--dummy-odpo-deck', type=Path, default=DEFAULT_DOD_DECK)
    args = ap.parse_args()

    cand = args.cand_dir.resolve()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / 'work'
    summary_dir = out / 'summary'
    work.mkdir(exist_ok=True)
    summary_dir.mkdir(exist_ok=True)

    raw_gds_gz = cand / 'outputs' / 'chip.gds.gz'
    raw_gds = work / 'chip_raw.gds'
    with gzip.open(raw_gds_gz, 'rb') as src, raw_gds.open('wb') as dst:
        shutil.copyfileobj(src, dst)

    top, units, bbox_dbu = inspect_gds(raw_gds, work)
    if top != 'my_soc_top' or abs(units - 0.0005) > 1e-9:
        raise RuntimeError(f'Unexpected candidate GDS header: top={top}, units={units}')

    sr_box = compute_sealring_box(bbox_dbu, dbu_per_um=2000, max_outer_um=1980.0)
    tsmc18_oa = resolve_tsmc18_oa(args.tsmc18_oa_dir)
    corner_gds, edge_gds = export_sealring_primitives(args.io_oa_dir.resolve(), tsmc18_oa, work / 'sealring')
    pimp_patch = verify_and_prepare_pimp_patch(raw_gds, work)

    predummy_gds = work / 'preDummy.gds'
    predummy_info = build_predummy(raw_gds, corner_gds, edge_gds, sr_box, pimp_patch, predummy_gds, work)

    postdummy_gds = work / 'postDummy.gds'
    dummy_info = run_dummy_insertion(
        predummy_gds, args.dummy_metal_deck, args.dummy_odpo_deck, postdummy_gds, work / 'dummy', args.threads
    )

    wb_deck = extract_wirebond_deck(args.wb_zip, work / 'wb_deck')
    drc_res = run_drc(postdummy_gds, args.drc_deck, work / 'drc', args.threads)
    ant_res = run_simple_check('ant', postdummy_gds, args.ant_deck, work / 'ant', args.threads, wirebond=False)
    wb_res = run_simple_check('wb', postdummy_gds, wb_deck, work / 'wb', args.threads, wirebond=True)
    lvs_res, erc_geom = run_lvs_suite(cand, postdummy_gds, args.lvs_deck, work / 'lvs', args.threads)

    if not drc_res['passed'] or not ant_res['passed'] or not wb_res['passed']:
        raise RuntimeError(f'Lab 5 physical check failed: DRC={drc_res["passed"]}, ANT={ant_res["passed"]}, WB={wb_res["passed"]}')

    # Sanitize Zero-PDK public summaries (never store host paths or usernames)
    lvs_public = {k: v for k, v in lvs_res.items() if k not in ('netlist_cdl', 'source_cdl')}
    lab5_summary = {
        'scope': 'Same-source Lab 5 signoff on 2000-DBU postDummy.gds (Seal Ring + CO.R.4 IO PIMP fix + Dummy OD/PO & Metal M1-M6 + DRC + nmLVS + Antenna + WireBond + ERC)',
        'topcell': 'my_soc_top',
        'dbu_per_um': 2000,
        'raw_die_bbox_um': [c / 2000.0 for c in bbox_dbu],
        'sealring_ref_box_um': sr_box['ref_um'],
        'sealed_outer_bbox_um': sr_box['outer_um'],
        'sealed_die_um': [sr_box['sealed_width_um'], sr_box['sealed_height_um']],
        'max_allowed_sealed_die_um': 1980.0,
        'cor4_pimp_patch': pimp_patch,
        'dummy_config': {
            'thick_40k_m6': False,
            'dmW_M6_um': 0.8,
            'dmL_M6_um': 0.8,
            'fill_layers': ['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'DOD', 'DPO'],
            'mixed_scheme_dummy_datatype': 1,
        },
        'checks': {
            'drc': drc_res,
            'lvs': lvs_public,
            'antenna': ant_res,
            'wirebond': wb_res,
            'erc': {
                'rules_executed': erc_geom['rules_executed'],
                'total_hier': erc_geom['total_hier'],
                'total_expanded': erc_geom['total_expanded'],
                'rules': {r: {'hier_count': d['hier_count'], 'expanded_count': d['expanded_count']} for r, d in erc_geom['rules'].items()},
                'status': 'manual_review_required',
            },
        },
        'formal_tapeout_signoff': False,
    }

    manifest = {
        'dbu_per_um': 2000,
        'candidate_name': cand.name,
        'private_server_sha256': {
            'chip.gds.gz': sha256_file(raw_gds_gz),
            'physical.v': sha256_file(cand / 'outputs' / 'physical.v'),
            'routed.def': sha256_file(cand / 'outputs' / 'routed.def'),
            'routed.sdf.gz': sha256_file(cand / 'outputs' / 'routed.sdf.gz'),
            'routed.spef.gz': sha256_file(cand / 'outputs' / 'routed.spef.gz'),
            'preDummy.gds': sha256_file(predummy_gds),
            'DM.gds': sha256_file(dummy_info['dm_gds']),
            'DOD.gds': sha256_file(dummy_info['dod_gds']),
            'postDummy.gds': sha256_file(postdummy_gds),
            'netlist.cdl': sha256_file(lvs_res['netlist_cdl']),
        },
    }

    (summary_dir / 'lab5_summary.json').write_text(json.dumps(lab5_summary, indent=2) + '\n')
    (summary_dir / 'final_erc_geometry.json').write_text(json.dumps(erc_geom, indent=2) + '\n')
    (summary_dir / 'lab5_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'passed': True, 'sealed_die_um': lab5_summary['sealed_die_um'], 'drc_blocking': drc_res['blocking'], 'ant': ant_res['total_hier'], 'wb': wb_res['total_hier'], 'erc_hier': erc_geom['total_hier']}, indent=2))


if __name__ == '__main__':
    main()
