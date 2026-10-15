#!/usr/bin/env python3
"""Scan fully routed EGo1 clocks and report power at 50 MHz and the best clock.

Example (run from repository root):
  python hardware/fpga/scripts/run_ego1_ppa.py --vivado-root D:/vivado24/Vivado/2024.2 \
    --output build/ego1_ppa --max-mhz 100 --saif build/kws_saif/workload.saif \
    --workload-log build/kws_saif/simulation.log
Remote capture (run by the operator with server access):
  powershell -NoProfile -ExecutionPolicy Bypass -File \
    hardware/soc/sim/scripts/run_kws_saif_remote.ps1 -HostName <server> \
    -WorkloadDir build/kws_workload
The returned build/kws_saif_<timestamp> contains workload.saif, workload.json,
simulation.log and firmware.sha256. Use that SAIF in --resume after capture.
No server access is performed by this entry point.
Run without --saif for vectorless only. After remote capture, --resume reuses the
same scan and routed checkpoints and adds activity-based power. See ppa.md.
Run --selftest to check checkpoint recovery without Vivado.
"""
import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.dont_write_bytecode = True
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'software/tools'))
from kws_vcd_to_saif import build_path, window
from summarize_ego1_ppa import summarize_point, write_summary as write_ppa_summary
from ego1_saif_mapping import map_activity, diagnose


def source_digest():
    source_hash = hashlib.sha256()
    sources = sorted(path for path in (ROOT/'hardware').rglob('*')
                     if path.is_file() and path.suffix in ('.sv','.v','.svh','.xdc','.f'))
    for path in sources:
        source_hash.update(path.relative_to(ROOT).as_posix().encode())
        source_hash.update(path.read_bytes())
    return source_hash.hexdigest()


def launch(command, directory, name, env=None):
    """Spawn in the background; periodically print progress and retain full logs."""
    startupinfo = None
    if os.name == 'nt':
        # CREATE_NO_WINDOW breaks Vivado's realtime synthesis subprocess:
        # couldn't read file ".../realtime\fpga_top.tcl": no such file or directory.
        # Hide the window without stripping the console needed by synthesis.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    with (directory/(name+'.stdout.log')).open('w') as stdout, (directory/(name+'.stderr.log')).open('w') as stderr:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, env=env,
            startupinfo=startupinfo)
        print(f'{name}: pid={proc.pid}; logs={directory}', flush=True)
        while proc.poll() is None:
            time.sleep(10)
            print(f'{name}: running; log={directory/(name+".stdout.log")}', flush=True)
        return proc.returncode


def write_summary(output, points, resolution, converged, reason, provenance=None):
    """Keep recovery provenance visible without changing the acceptance gates."""
    result = write_ppa_summary(output, points, resolution, converged, reason, provenance)
    records = {str(Path(p['directory']).resolve()): p for p in (provenance or {}).get('points', [])}
    lines = ['', '## Checkpoint recovery', '',
             '| Requested MHz | Directory | Original exit code | Recovery attempts (checkpoint, exit code) |',
             '|---:|---|---:|---|']
    for point in result['points']:
        record = records.get(str(Path(point['directory']).resolve()), {})
        attempts = record.get('recoveries', [])
        point['recoveries'] = attempts
        point['native_exit_code'] = record.get('native_exit_code', point['exit_code'])
        detail = '; '.join(f'{Path(a["checkpoint"]).name}, {a["exit_code"]}' for a in attempts) or 'None'
        lines.append(f'| {point["requested_mhz"]:g} | {point["directory"]} | {point["native_exit_code"]} | {detail} |')
    (output/'ppa.json').write_text(json.dumps(result, indent=2)+'\n')
    with (output/'ppa.md').open('a') as report:
        report.write('\n'.join(lines)+'\n')
    return result


def recover_point(args, record, save):
    """Retry a failed tool process from its furthest checkpoint, at most twice."""
    directory = Path(record['directory'])
    attempts = record.setdefault('recoveries', [])
    record.setdefault('native_exit_code', record['exit_code'])
    while record['exit_code'] != 0 and len(attempts) < 2:
        point = summarize_point(directory, record['requested_mhz'], record['exit_code'],
                                core_profile=args.core_profile)
        if point['status'] != 'tool_fail':
            break
        checkpoint = next((directory/(stage+'.dcp') for stage in ('route', 'place', 'opt', 'synth')
                           if (directory/(stage+'.dcp')).is_file()), None)
        if checkpoint is None:
            break
        recovery = directory/f'recovery_{len(attempts)+1}'
        recovery.mkdir(exist_ok=True)
        # Preserve failure evidence and prevent stale acceptance artifacts being reused.
        for name in ('summary.md', 'route_metrics.tsv', 'fpga_top.bit'):
            path = directory/name
            if path.exists():
                path.replace(recovery/name)
        attempt = dict(checkpoint=str(checkpoint), exit_code=None, directory=str(recovery), jobs=1)
        attempts.append(attempt)
        save()
        env = os.environ.copy()
        (directory/'tmp').mkdir(exist_ok=True)
        env['TEMP'] = env['TMP'] = str(directory/'tmp')
        env['XILINX_LOCAL_USER_DATA'] = 'no'
        command = [str(args.vivado_root/'bin/vivado.bat'), '-mode', 'batch', '-source',
                   str(ROOT/'hardware/fpga/scripts/recover_ego1_ppa.tcl'),
                   '-log', str(recovery/'vivado.log'), '-journal', str(recovery/'vivado.jou'),
                   '-tclargs', str(ROOT), str(directory), str(checkpoint), str(record['requested_mhz']),
                   str(recovery/'vivado.log')]
        print(f'Recovering {record["requested_mhz"]:g} MHz from {checkpoint}; attempt {len(attempts)}/2', flush=True)
        record['exit_code'] = attempt['exit_code'] = launch(command, recovery, 'recovery', env)
        save()
    return summarize_point(directory, record['requested_mhz'], record['exit_code'],
                           core_profile=args.core_profile)


def rescale_saif(source, output, period_ns, metadata):
    # Same RTL cycle trace at a different implemented clock. Scale dwell/window,
    # preserve toggle counts, including aliases and X time. Do not scale TC/IG.
    text = source.read_text()
    if '(TIMESCALE 1 ps)' not in text:
        raise ValueError('SAIF must use the converter 1 ps timescale')
    ratio = Decimal(str(period_ns))/Decimal(str(metadata['period_ns']))
    duration = round(Decimal(metadata['duration_ps'])*ratio)
    def dwell(match):
        t0, t1, tx = map(int, match.groups())
        if t0+t1+tx != metadata['duration_ps']:
            raise ValueError('SAIF dwell does not match activity metadata')
        a = round(Decimal(t0)*ratio)
        b = round(Decimal(t0+t1)*ratio)
        return f'(T0 {a}) (T1 {b-a}) (TX {duration-b})'
    text = re.sub(r'\(T0 (\d+)\) \(T1 (\d+)\) \(TX (\d+)\)', dwell, text)
    text = re.sub(r'\(DURATION\s+\d+\)', f'(DURATION {duration})', text)
    output.write_text(text)


def scan(args, output, manifest):
    def run_point(freq):
        previous = [record for record in manifest['points'] if record['requested_mhz'] == freq]
        if previous:
            record = previous[-1]
            cached = summarize_point(record['directory'], freq, record['exit_code'],core_profile=args.core_profile)
            if cached['status'] in ('pass','timing_fail'):
                print(f'Reusing routed point {freq:g} MHz: {cached["status"]}', flush=True)
                return cached
            cached = recover_point(args, record, save)
            if cached['status'] in ('pass', 'timing_fail'):
                save()
                return cached
        label = f'clock_{freq:g}'.replace('.', '_')
        if previous:
            label += f'_try{len(previous)+1}'
        directory = output/label
        exit_code = launch(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(ROOT/'hardware/fpga/scripts/build_ego1.ps1'), '-VivadoRoot', str(args.vivado_root),
            '-Jobs', str(args.jobs), '-FrequencyMHz', str(freq), '-OutputDir', str(directory)], output, label)
        record = dict(directory=str(directory), requested_mhz=freq, exit_code=exit_code, jobs=args.jobs)
        manifest['points'].append(record)
        point = recover_point(args, record, save)
        manifest['reason'] = f'Latest point {freq:g} MHz: {point["status"]}'
        save()
        return point
    def save():
        (output/'scan.json').write_text(json.dumps(manifest, indent=2)+'\n')
        points=[summarize_point(p['directory'],p['requested_mhz'],p['exit_code'],core_profile=args.core_profile) for p in manifest['points']]
        write_summary(output, points, args.resolution_mhz, manifest['converged'], manifest['reason'], manifest)
    first=run_point(50.0)
    if first['status'] != 'pass':
        manifest['reason'] = '50 MHz baseline failed; no Fmax inferred'
        save()
        return
    observed = [summarize_point(record['directory'], record['requested_mhz'], record['exit_code'],
                                core_profile=args.core_profile) for record in manifest['points']]
    accepted = [point for point in observed if point['status']=='pass']
    low = max(point['requested_mhz'] for point in accepted)
    failed = [point['requested_mhz'] for point in observed
              if point['status']=='timing_fail' and point['requested_mhz'] > low]
    high = min(failed) if failed else None
    freq = low
    while high is None and freq < args.max_mhz:
        freq=min(freq+args.step_mhz, args.max_mhz)
        point=run_point(freq)
        if point['status'] == 'tool_fail':
            manifest['reason']='Tool/implementation failure; cannot establish a timing boundary'
            save()
            return
        if point['status'] == 'timing_fail':
            high=freq
            break
        if point['actual_mhz'] <= first['actual_mhz'] and freq > 50:
            manifest['reason']='Clock wizard did not increase the actual frequency'
            save()
            return
        low=freq
    if high is None:
        manifest['reason']='All points passed at scan ceiling; increase --max-mhz to bracket Fmax'
        save()
        return
    # Refine requested-frequency bracket on the requested resolution grid.
    while high-low > args.resolution_mhz + 1e-8:
        mid=low + max(1, int((high-low)/(2*args.resolution_mhz)))*args.resolution_mhz
        mid=round(mid, 6)
        point=run_point(mid)
        if point['status']=='tool_fail':
            manifest['reason']='Refinement failed in tools; Fmax bracket is incomplete'
            save()
            return
        if point['status']=='pass':
            low=mid
        else:
            high=mid
    passing=[summarize_point(p['directory'],p['requested_mhz'],p['exit_code'],core_profile=args.core_profile) for p in manifest['points']]
    passing=[p for p in passing if p['status']=='pass']
    actual_high=next(summarize_point(p['directory'],p['requested_mhz'],p['exit_code'],core_profile=args.core_profile)['actual_mhz'] for p in reversed(manifest['points']) if p['requested_mhz']==high and summarize_point(p['directory'],p['requested_mhz'],p['exit_code'])['status']=='timing_fail')
    if actual_high <= max(p['actual_mhz'] for p in passing):
        manifest['reason']='Passing/failing points do not form an actual-frequency bracket (MMCM rounding or non-monotonic implementation)'
    else:
        manifest['converged']=True
        manifest['reason']=f'Passing/failing request bracket [{low:g}, {high:g}] MHz; resolution {args.resolution_mhz:g} MHz'
    save()


def recovery_selftest():
    """Exercise recovery with real summary parsing and controlled tool exits."""
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import patch

    (ROOT/'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ppa_recovery_selftest_', dir=ROOT/'build') as tmp:
        output = Path(tmp)
        args = SimpleNamespace(vivado_root=output, core_profile=None, jobs=4,
                               resolution_mhz=1, step_mhz=10, max_mhz=80)
        def fixture(label, checkpoint=None):
            directory = output/label
            directory.mkdir()
            if checkpoint:
                (directory/(checkpoint+'.dcp')).touch()
            return dict(directory=str(directory), requested_mhz=50, exit_code=-1073740940, jobs=4)
        def accepted(directory, timing_fail=False):
            (directory/'route.dcp').touch()
            (directory/'route_metrics.tsv').write_text(
                f'requested_mhz\t50\nperiod_ns\t20\nwns_ns\t{-1 if timing_fail else 1}\nwhs_ns\t0.1\n')
            (directory/'summary.md').write_text(
                'Result: FAILED\nFailure: Timing failed: WNS=-1 ns\n' if timing_fail else 'Result: PASS\n')

        for stage in ('synth', 'opt', 'place', 'route'):
            record = fixture(stage, stage)
            directory = Path(record['directory'])
            def recovered(command, logs, name, env):
                assert Path(command[command.index('-tclargs')+3]).name == stage+'.dcp'
                assert env['TEMP'] == str(directory/'tmp')
                assert not (directory/'summary.md').exists()
                accepted(directory)
                return 0
            with patch(__name__+'.launch', side_effect=recovered) as runner:
                assert recover_point(args, record, lambda: None)['status'] == 'pass'
                assert runner.call_count == 1 and len(record['recoveries']) == 1
            assert record['native_exit_code'] == -1073740940 and record['exit_code'] == 0

        missing = fixture('missing')
        with patch(__name__+'.launch') as runner:
            assert recover_point(args, missing, lambda: None)['status'] == 'tool_fail'
            runner.assert_not_called()
        capped = fixture('capped', 'place')
        with patch(__name__+'.launch', return_value=-1073740940) as runner:
            assert recover_point(args, capped, lambda: None)['status'] == 'tool_fail'
            assert runner.call_count == 2
            recover_point(args, capped, lambda: None)
            assert runner.call_count == 2  # The cap persists across resume.
        timed = fixture('timing', 'route')
        accepted(Path(timed['directory']), timing_fail=True)
        timed['exit_code'] = 1
        with patch(__name__+'.launch') as runner:
            assert recover_point(args, timed, lambda: None)['status'] == 'timing_fail'
            runner.assert_not_called()
        retry = fixture('retry', 'place')
        def second_attempt(command, logs, name, env):
            if len(retry['recoveries']) == 1:
                return -1073740940
            accepted(Path(retry['directory']), timing_fail=True)
            return 1
        with patch(__name__+'.launch', side_effect=second_attempt):
            assert recover_point(args, retry, lambda: None)['status'] == 'timing_fail'
            assert len(retry['recoveries']) == 2

        # Exercise scan's fresh-point and resume wiring, including persisted provenance.
        scan_out = output/'scan'
        scan_out.mkdir()
        def crashed_build(command, logs, name, env=None):
            directory = Path(command[-1])
            directory.mkdir()
            (directory/'place.dcp').touch()
            return -1073740940
        manifest = dict(points=[], converged=False, reason='')
        with patch(__name__+'.launch', side_effect=crashed_build), patch(__name__+'.recover_point', return_value={'status':'tool_fail'}) as recover:
            scan(args, scan_out, manifest)
            assert recover.call_count == 1
        def resume_build(command, logs, name, env=None):
            if '-tclargs' in command:
                directory = Path(command[command.index('-tclargs')+2])
                freq = float(command[command.index('-tclargs')+4])
            else:
                directory = Path(command[-1])
                directory.mkdir()
                freq = float(command[command.index('-FrequencyMHz')+1])
                (directory/'place.dcp').touch()
                # Every fresh point crashes once, then recovers in a new process.
                return -1073740940
            accepted(directory, timing_fail=freq > 73)
            text = (directory/'route_metrics.tsv').read_text()
            (directory/'route_metrics.tsv').write_text(text.replace('period_ns\t20', f'period_ns\t{1000/freq}'))
            return 1 if freq > 73 else 0
        with patch(__name__+'.launch', side_effect=resume_build):
            scan(args, scan_out, manifest)
        assert manifest['converged'] and '[73, 74]' in manifest['reason']
        result = json.loads((scan_out/'ppa.json').read_text())
        assert all(p['recoveries'] for p in result['points'])
        assert result['points'][0]['recoveries'][0]['exit_code'] == 0
        assert 'place.dcp, 0' in (scan_out/'ppa.md').read_text()
        assert not result['complete']  # Recovery does not waive power requirements.
        persisted = json.loads((scan_out/'scan.json').read_text())
        assert persisted['points'][0]['native_exit_code'] == -1073740940
        assert persisted['points'][0]['exit_code'] == 0
    print('PASS checkpoint recovery: synth/opt/place/route, no checkpoint, retry cap, timing failure, scan wiring and provenance')


def main():
    if sys.argv[1:] == ['--selftest']:
        recovery_selftest()
        return 0
    p=argparse.ArgumentParser(__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--vivado-root', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT/'build'/('ego1_ppa_'+datetime.now().strftime('%Y%m%d_%H%M%S')))
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--max-mhz', type=float, default=100)
    p.add_argument('--step-mhz', type=float, default=10)
    p.add_argument('--resolution-mhz', type=float, default=1)
    p.add_argument('--saif', type=Path)
    p.add_argument('--workload-log', type=Path)
    p.add_argument('--firmware-version', help='Human-readable firmware identity for the captured workload')
    p.add_argument('--core-profile', type=Path, help='Measured run_tinycnn8_fused_test.py --summary JSON (four classes)')
    p.add_argument('--resume', action='store_true', help='Reuse existing scan, regenerate selected power reports')
    p.add_argument('--continue-scan', action='store_true', help='With --resume, retry failed points and continue the search')
    args=p.parse_args()
    if args.continue_scan and not args.resume:
        p.error('--continue-scan requires --resume')
    if args.jobs < 1 or not 50 < args.max_mhz <= 500 or args.step_mhz <= 0 or not 0 < args.resolution_mhz <= args.step_mhz:
        p.error('Require jobs>0, 50<max<=500, step>=resolution>0')
    output=build_path(args.output/'scan.json').parent
    if os.name != 'nt':
        p.error('This entry point uses the Windows build_ego1.ps1 launcher')
    if not (args.vivado_root/'bin/vivado.bat').is_file():
        p.error('Vivado installation not found')
    activity=None
    if args.saif:
        args.saif=args.saif.resolve()
        activity=json.loads(args.saif.with_suffix('.json').read_text())
        with args.saif.open('rb') as f:
            if hashlib.file_digest(f, 'sha256').hexdigest() != activity.get('saif_sha256'):
                p.error('SAIF hash does not match converter metadata')
        if activity.get('implementation_hierarchy') != 'fpga_top/u_soc' or activity.get('cycles', 0) <= 0:
            p.error('SAIF requires matching converter metadata')
        if not args.workload_log:
            args.workload_log=args.saif.parent/'simulation.log'
        if not args.workload_log.is_file():
            p.error('SAIF workload log missing')
        begin,end,cycles=window(args.workload_log.read_text(encoding='utf-8-sig'))
        if (begin,end,cycles)!=(activity.get('begin_ps'),activity.get('end_ps'),activity['cycles']):
            p.error('SAIF capture window does not match the workload log')
    if args.resume:
        manifest=json.loads((output/'scan.json').read_text())
        if manifest.get('hardware_source_sha256') and manifest['hardware_source_sha256'] != source_digest():
            p.error('Hardware sources changed since scan; use a new output directory')
        if args.core_profile:
            manifest['core_profile']=str(args.core_profile.resolve())
        elif manifest.get('core_profile'):
            args.core_profile=Path(manifest['core_profile'])
        args.resolution_mhz=manifest['resolution_mhz']
        if args.continue_scan:
            manifest['converged']=False
            manifest['max_mhz']=args.max_mhz
            manifest['step_mhz']=args.step_mhz
            scan(args, output, manifest)
    else:
        if (output/'scan.json').exists() or any(output.iterdir()):
            p.error('Output must be new/empty, or use --resume')
        manifest=dict(schema=1, resolution_mhz=args.resolution_mhz, max_mhz=args.max_mhz,
                      step_mhz=args.step_mhz, jobs=args.jobs, vivado_root=str(args.vivado_root),
                      hardware_source_sha256=source_digest(), core_profile=str(args.core_profile.resolve()) if args.core_profile else None,
                      converged=False, reason='Not run', points=[])
        scan(args, output, manifest)
    manifest['activity_metadata']=str(args.saif.with_suffix('.json')) if args.saif else None
    manifest['workload_log']=str(args.workload_log.resolve()) if args.workload_log else None
    manifest['saif']=str(args.saif) if args.saif else None
    if args.saif:
        identity=args.saif.parent/'firmware.sha256'
        manifest['firmware']=dict(version=args.firmware_version or manifest.get('firmware',{}).get('version','unspecified'),
            image_sha256=identity.read_text().split()[0] if identity.exists() else None,
            captured_window_cycles=activity['cycles'],capture_period_ns=activity['period_ns'],
            capture_metadata=activity,
            sample_count=int(re.search(r'^KWS_PASS count=(\d+)$',args.workload_log.read_text(encoding='utf-8-sig'),re.M)[1]))
        with args.saif.open('rb') as f:
            manifest['saif_sha256']=hashlib.file_digest(f, 'sha256').hexdigest()
    points=[summarize_point(x['directory'],x['requested_mhz'],x['exit_code'],activity,args.workload_log,args.core_profile) for x in manifest['points']]
    routed=[x for x in points if x['timing'] and (Path(x['directory'])/'route.dcp').is_file()]
    passing=[x for x in points if x['status']=='pass']
    best=max(passing, key=lambda x:x['actual_mhz']) if passing else None
    targets=[x for x in routed if x['requested_mhz']==50 or (best and x['directory']==best['directory'])]
    env=os.environ.copy()
    env['XILINX_LOCAL_USER_DATA']='no'
    for point in targets:
        directory=Path(point['directory'])
        power=directory/'power'
        power.mkdir(exist_ok=True)
        for filename in ['vectorless_power.rpt','vectorless_power.xml','saif_power.rpt','saif_power.xml',
                         'saif_read.log','saif_unmatched.rpt','saif_switching.rpt']:
            (power/filename).unlink(missing_ok=True)
        scaled=''
        if args.saif:
            inventory=power/'design_nets.tsv'
            if not inventory.exists() or len(inventory.read_text().splitlines())<100:
                export_command=[str(args.vivado_root/'bin/vivado.bat'),'-mode','batch','-source',
                    str(ROOT/'hardware/fpga/scripts/export_ego1_activity_nets.tcl'),'-log',str(power/'inventory.log'),
                    '-journal',str(power/'inventory.jou'),'-tclargs',str(directory/'route.dcp'),str(inventory),'export_only']
                env['TEMP']=env['TMP']=str(directory/'tmp')
                if launch(export_command,power,'inventory',env):
                    raise RuntimeError('Net inventory export failed: '+str(power))
            mapped=power/'workload_mapped.saif'
            map_activity(args.saif,inventory,mapped,ROOT)
            scaled=power/'workload_scaled.saif'
            rescale_saif(mapped,scaled,point['timing']['period_ns'],activity)
        # cmd.exe forwards quoted paths to the vendor .bat launcher.
        command=[str(args.vivado_root/'bin/vivado.bat'), '-mode','batch','-source',
            str(ROOT/'hardware/fpga/scripts/report_ego1_power.tcl'), '-log',str(power/'vivado.log'),
            '-journal',str(power/'vivado.jou'), '-tclargs',str(directory/'route.dcp'),str(power),str(scaled)]
        env['TEMP']=env['TMP']=str(directory/'tmp')
        point['power_exit_code']=launch(command,power,'power',env)
        if args.saif and point['power_exit_code']==0:
            diagnose(power)
        next(x for x in manifest['points'] if x['directory']==point['directory'])['power_exit_code']=point['power_exit_code']
    (output/'scan.json').write_text(json.dumps(manifest,indent=2)+'\n')
    points=[summarize_point(x['directory'],x['requested_mhz'],x['exit_code'],activity,args.workload_log,args.core_profile) for x in manifest['points']]
    result=write_summary(output,points,args.resolution_mhz,manifest['converged'],manifest['reason'],manifest)
    power_ok = bool(targets) and all(x.get('power_exit_code') == 0 for x in manifest['points'] if x['directory'] in {t['directory'] for t in targets})
    complete=power_ok and manifest['converged'] and len(targets)>0 and all(
        all(mode in x['power'] and x['power'][mode]['energy_uj'] is not None and
            x['power'][mode]['confidence'] != 'unreported' and
            all(v is not None for v in x['power'][mode]['components_dynamic_w'].values()) and
            (mode!='saif' or (x['power'][mode]['matching']['matched_ratio'] or 0) > 0)
            for mode in ['vectorless','saif']) for x in points if x['directory'] in {t['directory'] for t in targets})
    print(f'PPA complete={complete}; summary={output/"ppa.md"}',flush=True)
    return 0 if complete else 1


if __name__=='__main__':
    sys.exit(main())
