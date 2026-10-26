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
            'DRC RESULTS DATABASE': 'DRC RESULTS DATABASE "drc.db"', 'DRC SUMMARY REPORT': 'DRC SUMMARY REPORT "drc.rep"'})
        script = f'calibre -drc -hier -turbo 8 rules.drc\ntest -s drc.rep && echo {marker}\n'
    elif check == 'lvs':
        rewrite_deck(v['lvs_deck'], out/'rules.lvs', {
            'LAYOUT PRIMARY': f'LAYOUT PRIMARY "{TOP}"', 'LAYOUT PATH': 'LAYOUT PATH "chip.gds"',
            'SOURCE PRIMARY': f'SOURCE PRIMARY "{TOP}"', 'SOURCE PATH': 'SOURCE PATH "source.cdl"',
            'LVS REPORT': 'LVS REPORT "lvs.rep"', 'LVS POWER NAME': 'LVS POWER NAME "VDD" "VDDPST"',
            'LVS GROUND NAME': 'LVS GROUND NAME "VSS" "VSSPST"'})
        script = (f'v2lvs -v {outputs}/physical.v -o netlist.cdl -s0 VSS -s1 VDD\n'
                  f'printf \'.INCLUDE "{v["std_spi"]}"\\n.INCLUDE "{v["io_spi"]}"\\n.INCLUDE "{v["sram_cdl"]}"\\n.INCLUDE "netlist.cdl"\\n\' > source.cdl\n'
                  f'calibre -lvs -hier -turbo 8 rules.lvs\ntest -s lvs.rep && echo {marker}\n')
    elif check == 'sta':
        (out/'sta.tcl').write_text(f'''set search_path "."
set link_path [list * {v['std_db']} {v['io_db']} {libs['macro_lib']}]
read_verilog {outputs}/simulation.v
current_design {TOP}
link_design {TOP}
read_sdc {outputs}/mapped.sdc
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


def evaluate(check, out, state):
    """Pass only on positive evidence from the tool's own report."""
    def read(name):
        p = out/name
        return p.read_text(errors='replace') if p.is_file() else ''
    result = dict(check=check, tool_status=state.get('status'))
    if check == 'drc':
        rep = read('drc.rep')
        total = re.search(r'TOTAL DRC Results Generated:\s+(\d+)', rep)
        rules = {m[0]: int(m[1]) for m in re.findall(r'RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count\s+=\s+(\d+)', rep)}
        result.update(total=int(total[1]) if total else None, violated_rules={k: n for k, n in rules.items() if n})
        result['passed'] = bool(total) and int(total[1]) == 0
    elif check == 'lvs':
        rep = read('lvs.rep')
        correct = bool(re.search(r'(?m)^\s*#\s+CORRECT\s+#', rep))
        incorrect = bool(re.search(r'(?m)INCORRECT', rep))
        result.update(correct=correct, incorrect=incorrect)
        result['passed'] = correct and not incorrect
    elif check == 'sta':
        log = read('console.log')
        wns = {k: float(v) for k, v in re.findall(r'PT_(SETUP|HOLD)_WNS\s+(-?[\d.]+)', log)}
        result.update(setup_wns_ns=wns.get('SETUP'), hold_wns_ns=wns.get('HOLD'),
                      unannotated='not annotated' in read('parasitics.rpt').lower())
        result['passed'] = len(wns) == 2 and wns['SETUP'] >= 0 and wns['HOLD'] >= 0
    else:
        log = read('console.log')
        ok = re.search(r'LEC_RESULT\s+(\d)', log)
        result.update(verified=bool(ok and ok[1] == '1'), succeeded='Verification SUCCEEDED' in log)
        result['passed'] = bool(ok and ok[1] == '1') and 'Verification SUCCEEDED' in log
    result['passed'] = result['passed'] and state.get('status') == 'complete'
    return result


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
        state = launch(command, out, marker, timeout=6*3600)
    result = evaluate(check, out, state)
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
                   artifacts_sha256=artifacts, note='TT corner only; no IR/EM/ESD, fill or seal ring')
    save(cand/'signoff/summary.json', summary)
    print(json.dumps({c: r['passed'] for c, r in results.items()}))


if __name__ == '__main__':
    main()
