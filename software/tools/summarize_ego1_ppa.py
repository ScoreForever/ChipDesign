#!/usr/bin/env python3
"""Summarize routed EGo1 timing, resource and power reports without guessed values."""
import argparse
import sys
sys.dont_write_bytecode = True
import json
import re
from pathlib import Path


def metrics(directory):
    path = Path(directory) / 'route_metrics.tsv'
    if not path.exists():
        return None
    data = dict(line.split('\t', 1) for line in path.read_text().splitlines())
    return {key: float(value) for key, value in data.items()}


def power_report(path):
    text = path.read_text()
    unit = re.search(r'Total On-Chip Power \((W|mW|uW|nW)\)', text)
    unit = unit[1] if unit else 'W'
    factor = dict(W=1, mW=1e-3, uW=1e-6, nW=1e-9)[unit]
    def number(label):
        match = re.search(r'\|\s*'+label+r'\s*\|\s*([\d.]+)\s*\|', text, re.I)
        return float(match[1])*factor if match else None
    result = dict(total_w=number(r'Total On-Chip Power \('+unit+r'\)'),
        dynamic_w=number(r'Dynamic \('+unit+r'\)'), static_w=number(r'Device Static \('+unit+r'\)'))
    confidence = re.search(r'\|\s*Confidence Level\s*\|\s*([^|]+)\|', text)
    result['confidence'] = confidence[1].strip() if confidence else 'unreported'
    # Vivado's "By Hierarchy" table is indented, not full hierarchical names.
    hierarchy = {}
    stack = []
    section = False
    report_lines = text.splitlines()
    for index, line in enumerate(report_lines):
        if re.search(r'By Hierarchy', line, re.I) and index+1 < len(report_lines) and report_lines[index+1].startswith('---'):
            section = True
            stack = []
            continue
        if section and re.match(r'^\d+(?:\.\d+)*\.?\s+[A-Za-z]', line) and 'By Hierarchy' not in line:
            section = False
        if not section or '|' not in line:
            continue
        cols = line.split('|')[1:-1]
        if len(cols) < 2 or not re.fullmatch(r'\s*\d+(?:\.\d+)?\s*', cols[1]):
            continue
        name = cols[0].strip()
        if not re.fullmatch(r'[\w./\[\]\\-]+', name):
            continue
        indent = len(cols[0])-len(cols[0].lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = '/'.join([x[1] for x in stack]+[name])
        hierarchy[full] = float(cols[1])*factor
        stack.append((indent, name))
    result['hierarchy_dynamic_w'] = hierarchy
    categories = dict(cpu=['i_cpu'], npu=['i_npu_subsystem'], crossbar=['i_axi_xbar'],
                      storage=['i_mainmem', 'i_bootram'])
    result['components_dynamic_w'] = {}
    for category, instances in categories.items():
        rows = {key: val for key, val in hierarchy.items() if key in {'fpga_top/u_soc/'+instance for instance in instances}}
        result['components_dynamic_w'][category] = sum(rows.values()) if len(rows) == len(instances) else None
    result['component_scope'] = 'Inclusive subsystem dynamic power; NPU local memories counted in NPU. Device static is unallocated.'
    result['report_power_unit'] = unit
    result['report'] = str(path.resolve())
    return result


def saif_matching(directory):
    paths = [directory/'vivado.log', directory/'saif_read.log', directory/'saif_unmatched.rpt']
    text = '\n'.join(p.read_text(errors='replace') for p in paths if p.exists())
    # Keep native diagnostics as evidence. Unsupported tool wording remains unknown.
    native_counts = re.findall(r'Design nets matched\s*=\s*(\d+)\s+of\s+(\d+)', text, re.I)
    native = (None, *native_counts[-1]) if native_counts else None
    if native and int(native[2]) > 0 and int(native[1]) <= int(native[2]):
        matched, total = map(int, native[1:])
        return dict(matched_nets=matched, design_nets=total, matched_ratio=matched/total,
                    denominator='implemented design nets (native read_saif coverage)', evidence=str(paths[0].resolve()))
    patterns = [r'(?:Successfully\s+)?matched\s+(\d+)\s+(?:out\s+of|of)\s+(\d+)\s+nets',
                r'(\d+)\s+(?:out\s+of|of)\s+(\d+)\s+nets\s+(?:were\s+)?matched']
    for pattern in patterns:
        found = re.search(pattern, text, re.I)
        if found:
            matched, total = map(int, found.groups())
            if total > 0 and 0 <= matched <= total:
                return dict(matched_nets=matched, saif_nets=total, matched_ratio=matched/total,
                            denominator='SAIF nets reported by read_saif', evidence=str(paths[0].resolve()))
    matched = re.search(r'(?<!un)\bmatched\s+(\d+)\s+nets', text, re.I)
    total = re.search(r'(?:total(?:\s+of)?|read)\s+(\d+)\s+nets', text, re.I)
    if matched and total and int(total[1]) > 0 and int(matched[1]) <= int(total[1]):
        return dict(matched_nets=int(matched[1]), saif_nets=int(total[1]),
                    matched_ratio=int(matched[1])/int(total[1]), evidence=str(paths[0].resolve()))
    return dict(matched_nets=None, saif_nets=None, matched_ratio=None,
                reason='Native read_saif matching counts unavailable; inspect saif_read.log and saif_unmatched.rpt')


def summarize_point(directory, requested, exit_code, activity=None, workload_log=None, core_profile=None):
    directory = Path(directory)
    timing = metrics(directory)
    summary_path = directory/'summary.md'
    summary = summary_path.read_text(encoding='utf-8-sig') if summary_path.exists() else ''
    point = dict(requested_mhz=requested, directory=str(directory.resolve()), exit_code=exit_code,
                 timing=timing, power={})
    if timing:
        point['actual_mhz'] = 1000/timing['period_ns']
        timing_pass = timing['wns_ns'] >= 0 and timing['whs_ns'] >= 0
        point['status'] = 'pass' if exit_code == 0 and 'Result: PASS' in summary and timing_pass else (
            'timing_fail' if not timing_pass and 'Timing failed:' in summary else 'tool_fail')
    else:
        point['status'] = 'tool_fail'
    utilization = directory/'route_utilization.rpt'
    point['resources'] = {}
    if utilization.exists():
        for line in utilization.read_text().splitlines():
            cols = [c.strip() for c in line.split('|')]
            if len(cols) > 3 and re.match(r'^(Slice LUTs|CLB LUTs|Slice Registers|CLB Registers|Block RAM Tile|DSPs)(\*|$)', cols[1]):
                try:
                    point['resources'][cols[1]] = float(cols[2])
                except ValueError:
                    pass
    if activity and timing:
        point['activity_window_cycles'] = activity['cycles']
        point['activity_window_us'] = activity['cycles'] * timing['period_ns']/1000
    if core_profile and timing and point['status']=='pass':
        profiles = json.loads(Path(core_profile).read_text())['cases']
        totals = {case['profile']['total'] for case in profiles if case['profile']['classes'] == 4}
        if len(totals) != 1:
            raise ValueError('Core profile must contain a stable measured four-class cycle count')
        point['core_cycles'] = totals.pop()
        point['core_latency_us'] = point['core_cycles']*timing['period_ns']/1000
        point['core_profile_source'] = str(Path(core_profile).resolve())
    if workload_log and timing and point['status']=='pass':
        text = Path(workload_log).read_text(encoding='utf-8-sig')
        sample = re.search(r'^KWS_SAMPLE index=0 .*?perf=(\d+) total=(\d+)$', text, re.M)
        if sample:
            point['core_cycles'], point['firmware_total_cycles'] = map(int, sample.groups())
            point['core_latency_us'] = point['core_cycles']*timing['period_ns']/1000
            point['firmware_latency_us'] = point['firmware_total_cycles']*timing['period_ns']/1000
    for mode in ('vectorless', 'saif'):
        path = directory/'power'/(mode+'_power.rpt')
        if not path.exists():
            continue
        data = power_report(path)
        # W * us = uJ; energy uses exactly the interval whose activity was averaged.
        duration = point.get('activity_window_us')
        data['energy_uj'] = data['total_w']*duration if duration is not None and data['total_w'] is not None else None
        data['dynamic_energy_uj'] = data['dynamic_w']*duration if duration is not None and data['dynamic_w'] is not None else None
        if mode == 'saif':
            data['matching'] = saif_matching(directory/'power')
            diagnostics=directory/'power/saif_diagnostics.json'
            if diagnostics.exists():
                data['diagnostics']=json.loads(diagnostics.read_text())
            mapping=directory/'power/workload_mapped.mapping.json'
            if mapping.exists():
                data['mapping']={key:value for key,value in json.loads(mapping.read_text()).items() if key!='aliases'}
                data['mapping']['evidence_file']=str(mapping.resolve())
            data['activity_quality']='partial RTL annotation; unmatched activity uses Vivado estimation'
            data['direct_coverage_below_80_percent']=(data['matching'].get('matched_ratio') or 0)<.8
        point['power'][mode] = data
    return point


def write_summary(output, points, resolution, converged, reason, provenance=None):
    if provenance:
        records = {record['directory']:record for record in provenance.get('points', [])}
        for point in points:
            record = records.get(point['directory'], {})
            for key in ['jobs','native_exit_code']:
                if key in record:
                    point[key] = record[key]
            if 'power_exit_code' in record:
                point['power_exit_code'] = record['power_exit_code']
    passing = [p for p in points if p['status'] == 'pass']
    best = max(passing, key=lambda p:p['actual_mhz']) if passing else None
    result = dict(schema=1, search_resolution_mhz=resolution, converged=converged, reason=reason,
                  fmax_mhz=best['actual_mhz'] if best and converged else None,
                  highest_passing_tested_mhz=best['actual_mhz'] if best else None, points=points,
                  assumptions=['Fmax is highest passing tested clock within the requested grid/bracket, not a guarantee of monotonic P&R.',
                  'SAIF rates at Fmax are rescaled by implemented clock period; cycle-level workload is assumed unchanged.',
                  'Activity is RTL simulation mapped to routed nets; unmatched logic and routing glitches use Vivado estimation.',
                  'Energy uses the captured bus window; firmware total and NPU core latencies are reported separately.'])
    firmware=(provenance or {}).get('firmware')
    if firmware:
        result['firmware']=firmware
    missing = []
    if not converged:
        missing.append('Fmax search has not converged')
    selected = [p for p in points if p['requested_mhz']==50 and p['status']=='pass']
    if best and best not in selected:
        selected.append(best)
    if not selected:
        missing.append('No accepted routed implementation')
    for point in selected:
        if point.get('power_exit_code', 0) != 0:
            missing.append(f'{point["requested_mhz"]:g} MHz power tool execution failed')
        for mode in ['vectorless','saif']:
            power = point['power'].get(mode)
            label = f'{point["requested_mhz"]:g} MHz {mode}'
            if not power:
                missing.append(label+' power report')
                continue
            if any(power[key] is None for key in ['total_w','dynamic_w','static_w']):
                missing.append(label+' power totals')
            if any(value is None for value in power['components_dynamic_w'].values()):
                missing.append(label+' component breakdown')
            if power['confidence']=='unreported':
                missing.append(label+' confidence level')
            if power['energy_uj'] is None:
                missing.append(label+' inference energy (requires real activity window)')
            if mode=='saif' and (power['matching'].get('matched_ratio') or 0)<=0:
                missing.append(label+' nonzero native net matching coverage')
    result['missing_measurements'] = missing
    result['complete'] = not missing
    if provenance:
        result['provenance'] = {key: value for key,value in provenance.items() if key != 'points'}
    output.mkdir(parents=True, exist_ok=True)
    (output/'ppa.json').write_text(json.dumps(result, indent=2)+'\n')
    def show(value):
        return 'unavailable' if value is None else f'{value:.6g}'
    lines = ['# EGo1 workload PPA', '', f'PPA complete: {result["complete"]}', f'Converged: {converged}; {reason}',
             f'Fmax (MHz): {show(result["fmax_mhz"])}',
             f'Highest passing tested (MHz): {show(result["highest_passing_tested_mhz"])}', '',
             '| Requested MHz | Actual MHz | WNS ns | WHS ns | Status | Core latency us | Firmware latency us |',
             '|---:|---:|---:|---:|---|---:|---:|']
    if firmware:
        capture=firmware.get('capture_metadata',{})
        lines[2:2]=[f'Firmware: {firmware["version"]} (software optimization state is part of the workload identity)',
            f'Firmware image SHA-256: {firmware.get("image_sha256", "unavailable")}',
            f'Capture: first inference (sample 0 of {firmware.get("sample_count", "unavailable")} successful workload samples); {capture.get("cycles")} cycles at {capture.get("period_ns")} ns; {capture.get("duration_ps",0)/1e6:g} us.',
            'Energy uses the captured first-input-write through post-clear idle-response window, including CPU scaling. Model setup is excluded.',
            'Firmware total timing includes profiling/call overhead outside that bus window and is reported separately.', '']
    for p in points:
        t=p['timing'] or {}
        lines.append(f'| {p["requested_mhz"]:g} | {show(p.get("actual_mhz"))} | {show(t.get("wns_ns"))} | {show(t.get("whs_ns"))} | {p["status"]} | {show(p.get("core_latency_us"))} | {show(p.get("firmware_latency_us"))} |')
    lines += ['', '| MHz | Mode | Static W | Dynamic W | Total W | Confidence | Matched nets (native denominator) | Window us | Energy uJ |',
              '|---:|---|---:|---:|---:|---|---|---:|---:|']
    for p in points:
        for mode, power in p['power'].items():
            m=power.get('matching', {})
            match='unavailable' if m.get('matched_ratio') is None else f'{m["matched_nets"]}/{m.get("design_nets",m.get("saif_nets"))} ({m["matched_ratio"]:.2%})'
            lines.append(f'| {show(p.get("actual_mhz"))} | {mode} | {show(power["static_w"])} | {show(power["dynamic_w"])} | {show(power["total_w"])} | {power["confidence"]} | {match} | {show(p.get("activity_window_us"))} | {show(power["energy_uj"])} |')
    lines += ['', '| MHz | Mode | CPU W | NPU W | Crossbar W | Storage W |', '|---:|---|---:|---:|---:|---:|']
    for p in points:
        for mode, power in p['power'].items():
            c=power['components_dynamic_w']
            lines.append(f'| {show(p.get("actual_mhz"))} | {mode} | '+ ' | '.join(show(c[x]) for x in ['cpu','npu','crossbar','storage'])+' |')
    lines += ['', 'Hierarchy values are inclusive dynamic power. NPU memories are included in NPU; storage covers BootRAM and main SRAM. Device static power is not apportioned.', '', '## Missing measurements', '']
    lines += ['- '+x for x in missing] if missing else ['None.']
    lines += ['', '## Activity matching and limitations', '']
    for point in selected:
        power=point['power'].get('saif')
        if not power: continue
        match=power['matching']
        lines += [f'### {point["requested_mhz"]:g} MHz', '',
            f'Native direct design-net coverage: {show(match.get("matched_ratio"))}; denominator: {match.get("denominator", "unavailable")}.',
            'Coverage below 80% is a material limitation: SAIF-guided power remains a mixed activity/estimation result. No default toggle rates are assigned by these scripts.', '']
        mapping=power.get('mapping',{})
        lines += [f'Observed aliases added: {mapping.get("added_aliases", "unavailable")}; conflicting groups not extended: {mapping.get("conflicting_alias_groups_not_extended", "unavailable")}.', '']
        diagnostics=power.get('diagnostics',{})
        lines += ['| Unmatched design driver class | Nets |','|---|---:|']
        for key,value in diagnostics.get('design_categories',{}).items():
            lines.append(f'| {key} | {value["count"]} |')
        lines += ['',f'Unmatched simulation nets: {diagnostics.get("unmatched_simulation_nets", "unavailable")}.',
            f'Unmatched design names also present in SAIF: {diagnostics.get("unmatched_design_names_present_in_saif", "unavailable")}; with majority X/Z dwell: {diagnostics.get("same_name_with_majority_unknown_dwell", "unavailable")}.', '']
        kinds=diagnostics.get('source_vcd_declaration_bits_by_kind',{})
        lines += [f'Source VCD declaration bits by kind: {kinds}. Parameters are simulation constants, not routed nets; they are retained in the source SAIF rather than removed to inflate coverage.',
                  f'Clock-constraint/activity mismatch warning: {diagnostics.get("clock_constraint_mismatch_warning", "unavailable")}. The workload holds JTAG TCK static while the checkpoint constrains it to 1 MHz; timing constraints are retained and peripheral clock power remains an estimation limitation.', '']
        history=diagnostics.get('native_matching_history',[])
        lines += ['Recorded native matching counts across runs: '+', '.join(f'{a}/{b}' for a,b in sorted({(h['matched_nets'],h['design_nets']) for h in history}))+'.', '']
        lines += ['- '+item for item in diagnostics.get('interpretation',[])]
        lines += ['']
    lines += ['', '## Assumptions', '']
    lines += ['- '+x for x in result['assumptions']]
    (output/'ppa.md').write_text('\n'.join(lines)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--scan', type=Path, required=True, help='Scan manifest JSON')
    args=parser.parse_args()
    manifest=json.loads(args.scan.read_text())
    activity=json.loads(Path(manifest['activity_metadata']).read_text()) if manifest.get('activity_metadata') else None
    points=[summarize_point(p['directory'],p['requested_mhz'],p['exit_code'],activity,manifest.get('workload_log'),manifest.get('core_profile')) for p in manifest['points']]
    from kws_vcd_to_saif import build_path
    result=write_summary(build_path(args.scan.parent/'ppa.json').parent, points,manifest['resolution_mhz'],manifest['converged'],manifest['reason'],manifest)
    print(json.dumps(dict(converged=result['converged'], fmax_mhz=result['fmax_mhz'], points=len(points))))


if __name__ == '__main__':
    main()
