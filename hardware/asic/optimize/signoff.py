#!/usr/bin/env python3
"""Independent signoff of a finished PnR candidate; run on the course Linux node.

Innovus checks its own database. These checks use other engines on the
exported files:
  drc  Calibre DRC on the merged GDS with the PDK rule deck
  lvs  Calibre LVS: merged GDS against the physical netlist (via v2lvs)
  sta  PrimeTime setup/hold with the routed netlist and Innovus SPEF
  lec  Formality equivalence of the synthesized and routed netlists
Each check runs in <candidate>/signoff/<check>/ through flow.launch (exit code,
completion marker, timeout). The PDK decks are copied there with only the
input/output statements replaced; nothing private enters the repository.
Unrecognized or missing reports fail closed.
"""
from __future__ import annotations
import argparse
import gzip
import json
from pathlib import Path
import re
import shutil
from flow import launch, recover_tool, save, digest, libraries

PDK = Path('/lec/pdk/tsmc180')
CALIBRE = PDK/'t018cmsp007k3_1_3a/tsmc18mm_1.8v_3.3v_T-018-CM-SP-007-K3_IC61_bsim4.5_pdk_v1.3a_20170119/PDK/Calibre'
TOP = 'my_soc_top'
CHECKS = ('drc', 'lvs', 'sta', 'lec')


def views(libs):
    """Signoff-only views next to the PnR libraries (same TT corner)."""
    std_db = Path(str(libs['std_lib']).replace('.lib', '.db'))
    io_db = Path(str(libs['io_lib']).replace('.lib', '.db'))
    std_spi = PDK/'tcb018gbwp7t_290a/TSMCHOME/digital/Back_End/spice/tcb018gbwp7t_270a/tcb018gbwp7t_270a.spi'
    io_spi = PDK/'tpd018nv_280a/TSMCHOME/digital/Back_End/spice/tpd018nv_270a/tpd018nv_1_2.spi'
    sram_cdl = Path(str(libs['macro_lib']).rsplit('/', 1)[0])/'RA1SHD_2048x32M8.cdl'
    return dict(std_db=std_db, io_db=io_db, std_spi=std_spi, io_spi=io_spi, sram_cdl=sram_cdl,
                drc_deck=CALIBRE/'drc/calibre.drc', lvs_deck=CALIBRE/'lvs/calibre.lvs')


def rewrite_deck(source, target, replacements):
    """Copy a Calibre deck, replacing whole statements by their leading keywords."""
    seen = set(); out = []
    for line in Path(source).read_text(errors='replace').splitlines():
        key = next((k for k in replacements if re.match(r'\s*'+re.escape(k)+r'\b', line)), None)
        if key is None:
            out.append(line)
        elif key not in seen:
            out.append(replacements[key]); seen.add(key)
    missing = set(replacements) - seen
    if missing:
        raise RuntimeError(f'deck {source} lacks statements {sorted(missing)}')
    Path(target).write_text('\n'.join(out)+'\n')


GDS_UNITS = 2000  # route.tcl: streamOut -units 2000 (database units per micron)


def precision(deck):
    """Statements that make the deck match the GDS database precision.

    The PDK decks use PRECISION 1000; RESOLUTION is in database units, so it
    scales with it to keep the same grid in microns."""
    text = Path(deck).read_text(errors='replace')
    prec = int(re.search(r'(?m)^\s*PRECISION\s+(\d+)', text)[1])
    res = int(re.search(r'(?m)^\s*RESOLUTION\s+(\d+)', text)[1])
    if GDS_UNITS % prec:
        raise RuntimeError(f'cannot rescale deck precision {prec} to {GDS_UNITS}')
    return {'PRECISION': f'PRECISION {GDS_UNITS}', 'RESOLUTION': f'RESOLUTION {res*GDS_UNITS//prec}'}


# The IO ring's POC rail is a .GLOBAL in the IO SPICE (so a source port) but has
# no top-level label in the layout. tpd018nv.gds labels it on metal3 (text
# layer 42) at y=113.215 um inside every pad cell; label it once at the top.
POC_RAIL_Y_UM = 113.215
POC_TEXT_LAYER = 42


def poc_label(cand):
    """LAYOUT TEXT statement on the POC rail of the south PVDD2POC pad (R0)."""
    config = (cand/'config.tcl').read_text()
    pad = re.search(r'\{\S+ PVDD2POC - supply S ([\d.]+) ([\d.]+) R0\}', config)
    if not pad:
        raise RuntimeError('no south R0 PVDD2POC pad to label POC')
    x, y = float(pad[1]) + 30, float(pad[2]) + POC_RAIL_Y_UM
    return f'LAYOUT TEXT "POC" {x:.3f} {y:.3f} {POC_TEXT_LAYER} "{TOP}"'


# The IO SPICE (tpd018nv_270a) and the IO GDS (280a) differ by a uniform ~3.5%
# on the poly resistors inside the supply pads; values within this bound there
# are recorded, not treated as connectivity errors.
LVS_IO_RESISTOR_TOLERANCE = 5.0


def property_errors(rep):
    """Parse the PROPERTY ERRORS table of a Calibre LVS report."""
    section = rep.split('PROPERTY ERRORS', 1)
    if len(section) < 2:
        return []
    # Table body: after the header rule line, up to the next banner rule.
    body = section[1].split('\n*', 2)
    body = body[1].split('\n*', 1)[0] if len(body) > 1 else ''
    out = []
    for m in re.finditer(r'(?m)^\s*(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*\n.*?([\d.]+)%\s*$', body):
        out.append(dict(layout=m[2], layout_type=m[3], source=m[4], source_type=m[5], percent=float(m[6])))
    numbered = re.findall(r'(?m)^\s*\d+\s+\S', body)
    if len(numbered) != len(out):  # an entry in another format: never accept silently
        out.append(dict(layout='unparsed', layout_type='?', source='?', source_type='?', percent=float('inf')))
    return out


# Rules whose geometry only library cells draw (our PnR adds metal and vias).
FRONT_END = ('CO.', 'PO.', 'OD.', 'NP.', 'PP.', 'RPO.', 'NW.', 'PW.', 'DNW.', 'ESD.', 'LUP.')


def drc_waiver(rule, where):
    """Reason a DRC result is not a design error, or None (blocking).

    - DRM.R.1 is an unconditional reminder the deck always emits.
    - Guideline (g) and front-end rules are accepted only when every result is
      inside the IO ring, i.e. inside the vendor IO cells."""
    if rule == 'DRM.R.1':
        return 'deck reminder to consult the DRM; emitted unconditionally'
    in_ring = bool(where) and set(where) == {'io_ring'}
    guideline = bool(re.search(r'\.\d+g(?:_|$)', rule))
    if in_ring and (guideline or rule.startswith(FRONT_END)):
        return ('ESD/latch-up guideline inside vendor IO cells' if guideline
                else 'front-end layer rule inside vendor IO cells')
    return None


def drc_locations(db, ring):
    """Count each rule's results inside the IO ring band versus elsewhere.

    ring = (die_um, depth_um); a result is in the ring when its centroid is
    within depth_um of a die edge. Parses Calibre ASCII results databases."""
    from collections import Counter, defaultdict
    die, depth = ring
    lines = Path(db).read_text(errors='replace').splitlines()
    unit = int(lines[0].split()[1])
    out = defaultdict(Counter); i = 1
    while i < len(lines):
        name = lines[i]; cur, _, ntext = map(int, lines[i+1].split()[:3]); i += 2+ntext
        for _ in range(cur):
            head = lines[i].split(); kind, n = head[0], int(head[2]); i += 1
            pts = []
            while len(pts) < (n if kind == 'p' else 1) and i < len(lines):
                f = lines[i].split(); i += 1
                if len(f) >= 2 and re.fullmatch(r'-?\d+', f[0]) and re.fullmatch(r'-?\d+', f[1]):
                    pts.append((int(f[0])/unit, int(f[1])/unit))
            x = sum(q[0] for q in pts)/len(pts); y = sum(q[1] for q in pts)/len(pts)
            out[name]['io_ring' if min(x, y, die-x, die-y) < depth else 'core'] += 1
    return out


def unzip(src, dst):
    with gzip.open(src, 'rb') as a, open(dst, 'wb') as b:
        shutil.copyfileobj(a, b)


def prepare(check, cand, out, libs, v):
    """Write the run script for one check; return (command, marker)."""
    outputs = cand/'outputs'
    marker = f'SIGNOFF_{check.upper()}_DONE'
    if check in ('drc', 'lvs'):
        unzip(outputs/'chip.gds.gz', out/'chip.gds')
    if check == 'drc':
        rewrite_deck(v['drc_deck'], out/'rules.drc', {
            'LAYOUT PATH': 'LAYOUT PATH "chip.gds"', 'LAYOUT PRIMARY': f'LAYOUT PRIMARY "{TOP}"',
            'DRC RESULTS DATABASE': 'DRC RESULTS DATABASE "drc.db"', 'DRC SUMMARY REPORT': 'DRC SUMMARY REPORT "drc.rep"',
            **precision(v['drc_deck'])})
        script = f'calibre -drc -hier -turbo 8 rules.drc\ntest -s drc.rep && echo {marker}\n'
    elif check == 'lvs':
        rewrite_deck(v['lvs_deck'], out/'rules.lvs', {
            'LAYOUT PRIMARY': f'LAYOUT PRIMARY "{TOP}"', 'LAYOUT PATH': 'LAYOUT PATH "chip.gds"',
            'SOURCE PRIMARY': f'SOURCE PRIMARY "{TOP}"', 'SOURCE PATH': 'SOURCE PATH "source.cdl"',
            'LVS REPORT': 'LVS REPORT "lvs.rep"', 'LVS POWER NAME': 'LVS POWER NAME "VDD" "VDDPST"',
            'LVS GROUND NAME': 'LVS GROUND NAME "VSS" "VSSPST"',
            # The IO pads' output NOR2 is drawn as fingered series stacks but is a
            # single gate in the IO SPICE; the deck documents this switch for that.
            'LVS REDUCE SPLIT GATES': 'LVS REDUCE SPLIT GATES YES', **precision(v['lvs_deck'])})
        with open(out/'rules.lvs', 'a') as deck:
            deck.write(poc_label(cand)+'\n')
        script = (f'v2lvs -v {outputs}/physical.v -o netlist.cdl -s0 VSS -s1 VDD\n'
                  f'printf \'.INCLUDE "{v["std_spi"]}"\\n.INCLUDE "{v["io_spi"]}"\\n.INCLUDE "{v["sram_cdl"]}"\\n.INCLUDE "netlist.cdl"\\n\' > source.cdl\n'
                  f'calibre -lvs -hier -turbo 8 rules.lvs\ntest -s lvs.rep && echo {marker}\n')
    elif check == 'sta':
        # Innovus writes "current_design <top>", which read_sdc in PrimeTime rejects
        # (and then drops the whole file); the design is already current.
        sdc = (outputs/'mapped.sdc').read_text()
        (out/'signoff.sdc').write_text(re.sub(r'(?m)^\s*current_design\b.*$', '', sdc))
        (out/'sta.tcl').write_text(f'''set search_path "."
# No Library Compiler on the node, so the SRAM .lib cannot become a .db:
# both SRAMs link as black boxes and their interface paths stay with Innovus.
set link_path [list * {v['std_db']} {v['io_db']}]
set link_create_black_boxes true
read_verilog {outputs}/simulation.v
current_design {TOP}
link_design
read_sdc -echo signoff.sdc
set_propagated_clock [all_clocks]
read_parasitics -format spef {outputs}/routed.spef.gz
update_timing -full
check_timing -verbose > check_timing.rpt
report_annotated_parasitics -check > parasitics.rpt
report_timing -delay_type max -max_paths 20 > setup.rpt
report_timing -delay_type min -max_paths 20 > hold.rpt
report_constraint -all_violators > violators.rpt
foreach {{kind type}} {{SETUP max HOLD min}} {{
    set path [get_timing_paths -delay_type $type -max_paths 1]
    puts "PT_${{kind}}_WNS [get_attribute $path slack]"
}}
puts "{marker}"
exit
''')
        script = 'pt_shell -file sta.tcl\n'
    else:
        (out/'lec.tcl').write_text(f'''set hdlin_unresolved_modules black_box
set verification_set_undriven_signals 0:X
read_db [list {v['std_db']} {v['io_db']}]
read_verilog -r [list {cand}/../../synthesis/mapped.v]
set_top r:/WORK/{TOP}
read_verilog -i [list {outputs}/simulation.v]
set_top i:/WORK/{TOP}
match
report_unmatched_points > unmatched.rpt
set ok [verify]
report_failing_points > failing.rpt
report_status > status.rpt
puts "LEC_RESULT $ok"
puts "{marker}"
exit
''')
        script = 'fm_shell -file lec.tcl\n'
    (out/'run.sh').write_text('#!/bin/bash\nset -u\n'+script)
    return ['bash', str(out/'run.sh')], marker


def evaluate(check, out, state, ring=None):
    """Pass only on positive evidence from the tool's own report."""
    def read(name):
        p = out/name
        return p.read_text(errors='replace') if p.is_file() else ''
    result = dict(check=check, tool_status=state.get('status'))
    if check == 'drc':
        rep = read('drc.rep')
        total = re.search(r'TOTAL DRC Results Generated:\s+(\d+)', rep)
        rules = {m[0]: int(m[1]) for m in re.findall(r'RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count\s+=\s+(\d+)', rep)}
        violated = {k: n for k, n in rules.items() if n}
        where = drc_locations(out/'drc.db', ring) if violated and ring else {}
        accepted = {k: dict(count=n, reason=drc_waiver(k, where.get(k, {}))) for k, n in violated.items()}
        accepted = {k: v for k, v in accepted.items() if v['reason']}
        blocking = {k: n for k, n in violated.items() if k not in accepted}
        result.update(total=int(total[1]) if total else None, violated_rules=violated,
                      locations={k: dict(v) for k, v in where.items()}, accepted=accepted, blocking=blocking)
        result['passed'] = bool(total) and not blocking
    elif check == 'lvs':
        rep = read('lvs.rep')
        correct = bool(re.search(r'(?m)^\s*#\s+CORRECT\s+#', rep))
        incorrect = bool(re.search(r'(?m)INCORRECT', rep))
        top = rep.split('CELL COMPARISON RESULTS ( TOP LEVEL )', 1)[-1].split('LAYOUT CELL NAME', 1)[0]
        errors = re.findall(r'(?m)^\s*Error:\s+(.+?)\s*$', top)
        props = property_errors(rep)
        accepted = [e for e in props if e['source'].startswith('Xi_pad_') and e['layout_type'] == 'R(PR)'
                    and e['source_type'] == 'R' and e['percent'] <= LVS_IO_RESISTOR_TOLERANCE]
        # Only IO-pad resistor values may differ; any other discrepancy fails.
        only_io_resistors = (bool(props) and len(accepted) == len(props)
                             and [e.rstrip('.') for e in errors] == ['Property errors'])
        result.update(correct=correct, incorrect=incorrect, top_errors=errors, property_errors=len(props),
                      accepted_io_resistor_errors=len(accepted),
                      max_io_resistor_deviation_percent=max((e['percent'] for e in accepted), default=None))
        result['passed'] = (correct and not incorrect) or (bool(top) and only_io_resistors)
    elif check == 'sta':
        log = read('console.log')
        wns = {k: float(v) for k, v in re.findall(r'PT_(SETUP|HOLD)_WNS\s+(-?[\d.]+)', log)}
        result.update(setup_wns_ns=wns.get('SETUP'), hold_wns_ns=wns.get('HOLD'),
                      unannotated='not annotated' in read('parasitics.rpt').lower(),
                      black_boxes=sorted(set(re.findall(r'Creating black box for \S+/(\S+)', log))),
                      scope='standard cells and IO; SRAM interface timing is checked by Innovus only')
        result['passed'] = len(wns) == 2 and wns['SETUP'] >= 0 and wns['HOLD'] >= 0
    else:
        log = read('console.log')
        ok = re.search(r'LEC_RESULT\s+(\d)', log)
        result.update(verified=bool(ok and ok[1] == '1'), succeeded='Verification SUCCEEDED' in log)
        result['passed'] = bool(ok and ok[1] == '1') and 'Verification SUCCEEDED' in log
    result['passed'] = result['passed'] and state.get('status') == 'complete'
    return result


def io_ring(cand):
    """(die side, IO ring depth) in um from the candidate's generated config."""
    config = (cand/'config.tcl').read_text()
    die = float(re.search(r'(?m)^set die_side_um ([\d.]+)', config)[1])
    corner = float(re.search(r'(?m)^set corner_um ([\d.]+)', config)[1])
    return die, corner  # PCORNER side equals the IO cell height


def run_check(check, cand, libs, v):
    out = cand/'signoff'/check
    marker = f'SIGNOFF_{check.upper()}_DONE'
    if (out/'status.json').exists():
        state = json.loads((out/'status.json').read_text())
        if state.get('status') == 'running':
            state = recover_tool(out, marker)
    else:
        out.mkdir(parents=True, exist_ok=True)
        command, marker = prepare(check, cand, out, libs, v)
        # pt_shell prints PT-063 at startup when Library Compiler is absent; the
        # SRAMs are black boxes here, so no .lib is read and it has no effect.
        state = launch(command, out, marker, timeout=6*3600, accepted_codes=('PT-063',) if check == 'sta' else ())
    result = evaluate(check, out, state, io_ring(cand))
    save(out/'result.json', result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidate', type=Path, required=True)
    ap.add_argument('--checks', nargs='+', choices=CHECKS, default=list(CHECKS))
    args = ap.parse_args(); cand = args.candidate.resolve()
    artifacts = json.loads((cand/'artifact_sha256.json').read_text())
    if any(digest(cand/p) != sha for p, sha in artifacts.items()):
        raise RuntimeError('physical artifacts changed since PnR acceptance')
    libs = libraries(); v = views(libs)
    missing = [str(p) for p in v.values() if not Path(p).is_file()]
    if missing:
        raise RuntimeError('missing signoff views: '+', '.join(missing))
    results = {c: run_check(c, cand, libs, v) for c in args.checks}
    summary = dict(results=results, passed=all(r['passed'] for r in results.values()),
                   artifacts_sha256=artifacts, note='TT corner only; M5/M6 fill only; no IR/EM, seal ring or chip-level ESD review')
    save(cand/'signoff/summary.json', summary)
    print(json.dumps({c: r['passed'] for c, r in results.items()}))


if __name__ == '__main__':
    main()
