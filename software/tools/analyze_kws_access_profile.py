"""Summarize passive KWS bus probes; all generated artifacts stay in build/.

REQ/GNT/RESP are CPU OBI observations. AXI V/H mean first VALID/handshake.
Function windows use accepted ID PCs from the *same image's* symbols.txt.
They include called helpers, but exclude caller timing/argument/return overhead.
Fetch counts are 32-bit OBI requests, not retired instructions. Wait unions are
bus occupancy, not a proof of CPU stalls or achievable speedup.
"""
import argparse
from collections import defaultdict, deque
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[2]
HEX = {'addr', 'data', 'pc', 'insn'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse(text):
    require(not re.search(r'FAIL:|TIMEOUT|\*(?:E|F|SE),|^\s*(?:Fatal:|Error:)', text, re.M),
            'simulation reports a failure')
    metadata = re.findall(r'^ACCESS_META schema=([12]) period_ns=20 sample=posedge_pre_nba$', text, re.M)
    require(len(metadata) == 1,
            'missing/duplicate or unsupported ACCESS_META')
    passed = re.findall(r'^KWS_PASS count=(\d+)\s*$', text, re.M)
    require(len(passed) == 1, 'missing/duplicate KWS_PASS')
    samples = re.findall(r'^KWS_SAMPLE (.+)$', text, re.M)
    require(len(samples) == int(passed[0]), 'incomplete firmware results')
    events = []
    for line in text.splitlines():
        if not line.startswith(('ACCESS_OBI ', 'ACCESS_AXI ', 'ACCESS_MEM ',
                                'ACCESS_WRAPPER ', 'ACCESS_ID ')):
            continue
        kind, fields = line.split(' ', 1)
        event = {'kind': kind}
        if metadata[0] == '2':
            event['unified_cpu'] = True
        for field in fields.split():
            key, value = field.split('=', 1)
            event[key] = (value if key == 'data' and re.search('[xzXZ]', value)
                          else int(value, 16)) if key in HEX else (
                value if key in {'port', 'event', 'channel'} else int(value))
        events.append(event)
    require(events, 'no probe events')
    return sorted(events, key=lambda e: e['cycle']), samples


def grouped(events, kind, keys):
    result = defaultdict(list)
    for e in events:
        if e['kind'] == kind:
            result[tuple(e[k] for k in keys)].append(e)
    return result


def axi_channels(events):
    groups = grouped(events, 'ACCESS_AXI', ('port', 'channel', 'seq'))
    channels = defaultdict(list)
    for (port, ch, seq), group in groups.items():
        parts = {e['event']: e for e in group}
        require(len(parts) == len(group) and 'V' in parts, 'duplicate/missing AXI V')
        if 'H' not in parts:  # Simulation may end with one fetch in flight.
            continue
        v, h = parts['V'], parts['H']
        require(v['cycle'] <= h['cycle'], 'AXI handshake precedes valid')
        require(all(v[k] == h[k] for k in ('addr', 'data', 'id', 'resp', 'len', 'last')),
                'AXI payload changed during backpressure')
        require(h['len'] == 0 and h['last'] == 1, 'profiler expects single-beat AXI')
        require(h['resp'] == 0, 'non-OKAY AXI response')
        channels[port, ch].append(dict(h, v=v['cycle'], h=h['cycle']))
    for group in channels.values():
        group.sort(key=lambda e: e['seq'])
    return channels


def transactions(events):
    channels = axi_channels(events)
    unified = any(e.get('unified_cpu') for e in events)
    auxiliary = ('S1', 'S2') if unified else ('S2', 'S3')
    require(not any(channels[port, ch] for port in auxiliary for ch in ('AW','AR')),
            'profiler expects baseline CPU-only traffic; DMA/debug traffic is unsupported')
    # W has no ID: pair by AW order at each port. Responses pair by AXI ID FIFO.
    bus = defaultdict(deque)
    for port in ['S0', 'S1', 'S2', 'S3', 'M0', 'M1', 'M2', 'M3']:
        for write in (0, 1):
            requests = channels[port, 'AW' if write else 'AR']
            responses = defaultdict(deque)
            for e in channels[port, 'B' if write else 'R']:
                responses[e['id']].append(e)
            for i, req in enumerate(requests):
                record = {'a': req}
                if write:
                    if i >= len(channels[port, 'W']):
                        continue
                    record['w'] = channels[port, 'W'][i]
                if not responses[req['id']]:
                    continue
                record['r'] = responses[req['id']].popleft()
                bus[port, write, req['id'], req['addr']].append(record)
    obi = grouped(events, 'ACCESS_OBI', ('port', 'seq'))
    memory = grouped(events, 'ACCESS_MEM', ('port', 'we', 'addr'))
    wrapper = grouped(events, 'ACCESS_WRAPPER', ('we', 'addr'))
    rows, incomplete = [], []
    for (port, seq), group in sorted(obi.items(), key=lambda item: item[1][0]['cycle']):
        parts = {e['event']: e for e in group}
        require(len(parts) == len(group) and 'REQ' in parts, 'duplicate/missing OBI REQ')
        if set(parts) != {'REQ', 'GNT', 'RESP'}:
            incomplete.append((port, seq))
            continue
        req, grant, resp = (parts[k] for k in ('REQ', 'GNT', 'RESP'))
        addr, write = req['addr'], req['we']
        require(req['cycle'] <= grant['cycle'] <= resp['cycle'], 'OBI event ordering')
        require((addr, write) == (grant['addr'], grant['we']), 'OBI request changed')
        source = 0 if unified or port == 'I' else 1
        sid = 0 if unified else (1 if port == 'I' else 2)
        if 0x80000000 <= addr < 0x80002000:
            target = 1
        elif 0x70000000 <= addr < 0x70004000:
            target = 3
        elif 0x10000 <= addr < 0x20000:
            target = 0
        else:
            raise ValueError(f'unsupported CPU target {addr:08x}')
        mid = sid | (source << 2)
        skey, mkey = (f'S{source}', write, sid, addr), (f'M{target}', write, mid, addr)
        require(bus[skey] and bus[mkey], f'missing AXI path for {port}/{seq}')
        s, m = bus[skey].popleft(), bus[mkey].popleft()
        row = dict(port=port, seq=seq, addr=f'{addr:08x}', we=write, target=target,
                   req=req['cycle'], gnt=grant['cycle'], resp=resp['cycle'])
        for prefix, record in [('s', s), ('m', m)]:
            for ch, e in record.items():
                row[prefix+ch+'v'], row[prefix+ch+'h'] = e['v'], e['h']
        candidates = memory[f'M{target}', write, addr]
        # Legacy READ_LATENCY=1 may repeat read req under backpressure.
        hits = [e for e in candidates if m['a']['h'] <= e['cycle'] <= m['r']['v']]
        require(hits, f'missing memory request for {port}/{seq}')
        row['mem'] = hits[0]['cycle']
        if target == 3:
            hits = [e for e in wrapper[write, addr] if e['cycle'] == row['mem']]
            require(len(hits) == 1, 'NPU subsystem/wrapper request mismatch')
            row['wrapper'] = hits[0]['cycle']
        row.update(req_to_gnt=row['gnt']-row['req'], req_to_resp=row['resp']-row['req'],
                   req_to_axi=row['sav']-row['req'],
                   axi_accept=row['sah']-row['sav'],
                   xbar_address=row['mav']-row['sah'],
                   target_accept=row['mah']-row['mav'],
                   axi2mem_issue=row['mem']-row['mah'],
                   target_service=row['mrv']-row['mem'],
                   target_response_wait=row['mrh']-row['mrv'],
                   xbar_response=row['srv']-row['mrh'],
                   adapter_response_wait=row['srh']-row['srv'],
                   obi_return=row['resp']-row['srh'])
        if write:
            row.update(xbar_w=row['mwv']-row['swh'],
                       target_w_wait=row['mwh']-row['mwv'],
                       aw_w_join=row['mem']-max(row['mah'], row['mwh']))
        # Address and W timelines are parallel. Never sum their branches.
        require(all(row[k] >= 0 for k in ('req_to_axi', 'axi_accept', 'xbar_address',
                'target_accept', 'axi2mem_issue', 'target_service', 'target_response_wait',
                'xbar_response', 'adapter_response_wait', 'obi_return')),
                'negative path segment (unsupported reordering or bad probe)')
        rows.append(row)
    require(rows, 'no complete transactions')
    return rows, incomplete


def function_windows(events, symbol_text):
    symbols = []
    for line in symbol_text.splitlines():
        parts = line.split()
        if len(parts) == 3:
            symbols.append((int(parts[0], 16), parts[2]))
    symbols.sort()
    lookup = {name: address for address, name in symbols}
    require(all(n in lookup for n in ('main', 'kws_load_input', 'kws_read_result')),
            'symbols must come from the profiled workload image')
    main_lo = lookup['main']
    main_hi = min(a for a, _ in symbols if a > main_lo)
    entries = {lookup['kws_load_input']: 'input', lookup['kws_read_result']: 'read'}
    active, result = None, []
    for e in events:
        if e['kind'] != 'ACCESS_ID':
            continue
        if e['pc'] in entries:
            require(active is None, 'nested/duplicate phase entry')
            active = dict(phase=entries[e['pc']], start=e['cycle'])
        elif active and main_lo <= e['pc'] < main_hi:
            result.append(dict(active, end=e['cycle']))
            active = None
    require(active is None, 'incomplete function window')
    counters = defaultdict(int)
    for w in result:
        w['sample'] = counters[w['phase']]
        counters[w['phase']] += 1
    return result


def union(intervals):
    total, end = 0, -1
    for lo, hi in sorted(intervals):
        total += max(0, hi-max(lo, end))
        end = max(end, hi)
    return total


def window_stats(rows, events, start, end):
    intervals = {}
    for port in ('I', 'D'):
        intervals[port] = [(max(start, r['req']), min(end, r['resp'])) for r in rows
                          if r['port'] == port and r['req'] < end and r['resp'] > start]
    iw, dw = (union(intervals[p]) for p in ('I', 'D'))
    either = union(intervals['I'] + intervals['D'])
    return dict(cycles=end-start, fetches=sum(r['port'] == 'I' and start <= r['req'] < end for r in rows),
                id_accepts=sum(e['kind'] == 'ACCESS_ID' and start <= e['cycle'] < end for e in events),
                fetch_pending=iw, data_pending=dw, overlap=iw+dw-either,
                fetch_only=either-dw, data_only=either-iw, neither=end-start-either,
                fetch_pending_pct=round(100*iw/(end-start), 3))


def analyze(text, symbol_text):
    events, samples = parse(text)
    rows, incomplete = transactions(events)
    windows = function_windows(events, symbol_text)
    require(len(windows) == 2*len(samples), 'function window/sample count mismatch')
    phases, iterations = [], []
    for w in windows:
        selected = [r for r in rows if w['start'] <= r['req'] < w['end']]
        data = [r for r in selected if r['port'] == 'D']
        mmio = [r for r in data if r['target'] == 3]
        writes = [r for r in mmio if r['we'] and 0x70001000 <= int(r['addr'],16) < 0x70001500]
        logits = [r for r in mmio if not r['we'] and 0x70000010 <= int(r['addr'],16) < 0x70000020]
        if w['phase'] == 'input':
            require([r['addr'] for r in writes] == [f'{0x70001000+4*i:08x}' for i in range(320)],
                    'input phase must write all 320 inputs exactly once in order')
            spans = writes
        else:
            require([r['addr'] for r in logits] == [f'{0x70000010+4*i:08x}' for i in range(4)],
                    'read phase must read all four logits exactly once in order')
            spans = logits
        require(all(r['resp'] <= w['end'] for r in data), 'phase data response crosses return')
        require(not any(r['kind'] == 'ACCESS_OBI' and r['port'] in ('I','D') and
                (r['port'], r['seq']) in incomplete and r['cycle'] < w['end'] for r in events),
                'incomplete request before end of measured phase')
        stats = window_stats(rows, events, w['start'], w['end'])
        phases.append(dict(w, **stats, mmio_transactions=len(mmio),
                           firmware_cycles=int(dict(f.split('=',1) for f in samples[w['sample']].split())[w['phase']]),
                           first_mmio=spans[0]['req'], last_mmio_resp=spans[-1]['resp']))
        for i, r in enumerate(spans):
            end = spans[i+1]['req'] if i+1 < len(spans) else w['end']
            iterations.append(dict(sample=w['sample'], phase=w['phase'], index=i,
                start=r['req'], end=end, **window_stats(rows, events, r['req'], end),
                mmio_req_to_resp=r['req_to_resp']))
        for r in selected:
            r['sample'], r['phase'] = w['sample'], w['phase']
    return rows, phases, iterations, incomplete


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def table(rows, fields):
    return '\n'.join(['| '+' | '.join(fields)+' |', '| '+' | '.join(['---']*len(fields))+' |',
                      *['| '+' | '.join(str(r.get(k,'')) for k in fields)+' |' for r in rows]])


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--log', required=True)
    p.add_argument('--workload', required=True, help='original image directory containing symbols.txt/workload.hex')
    p.add_argument('--output', required=True)
    args = p.parse_args()
    out = Path(args.output).resolve()
    require(out.is_relative_to(ROOT/'build') and out != ROOT/'build', 'output must be under worktree build/')
    log, workload = Path(args.log), Path(args.workload)
    rows, phases, iterations, incomplete = analyze(log.read_text(encoding='utf-8-sig'),
                                                  (workload/'symbols.txt').read_text())
    image_hash = hashlib.sha256((workload/'workload.hex').read_bytes()).hexdigest()
    logged_hash = re.findall(r'^ACCESS_IMAGE sha256=([0-9a-f]{64})$',
                            log.read_text(encoding='utf-8-sig'), re.M)
    require(logged_hash == [image_hash], 'profile log/image SHA256 mismatch or missing ACCESS_IMAGE')
    out.mkdir(parents=True, exist_ok=True)
    for name, values in [('transactions', rows), ('phases', phases), ('iterations', iterations)]:
        write_csv(out/f'{name}.csv', values)
    segments = ['req_to_gnt','req_to_resp','req_to_axi','axi_accept','xbar_address',
                'target_accept','axi2mem_issue','target_service','target_response_wait',
                'xbar_response','adapter_response_wait','obi_return','xbar_w','target_w_wait','aw_w_join']
    stats = []
    for phase in ('input','read'):
        for kind in ('fetch','input_write','logit_read','other_data'):
            chosen = [r for r in rows if r.get('phase') == phase and (
                'fetch' if r['port']=='I' else 'input_write' if r['we'] and r['target']==3 and
                0x70001000<=int(r['addr'],16)<0x70001500 else 'logit_read' if not r['we'] and
                r['target']==3 and 0x70000010<=int(r['addr'],16)<0x70000020 else 'other_data') == kind]
            for segment in segments:
                values = [r[segment] for r in chosen if segment in r]
                if values:
                    stats.append(dict(phase=phase, kind=kind, segment=segment, count=len(values),
                        min=min(values), mean=round(statistics.mean(values),3), max=max(values)))
    write_csv(out/'latencies.csv', stats)
    summary = dict(schema=1, log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
                   image_sha256=hashlib.sha256((workload/'workload.hex').read_bytes()).hexdigest(),
                   incomplete_tail=incomplete, phases=phases, latencies=stats)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    report = table(phases,['sample','phase','cycles','firmware_cycles','fetches','id_accepts',
                          'fetch_pending','fetch_pending_pct','data_pending','overlap'])
    report += '\n\n'+table(stats,['phase','kind','segment','count','min','mean','max'])
    report += ('\n\nFunction windows: accepted entry ID PC to first caller main ID PC; '
               'firmware_cycles include caller overhead. Pending spans are [REQ, RESP); '
               'fetch_pending_pct is occupancy, not CPU stall attribution. '
               'Input iteration spans start at each MMIO REQ and end at the next REQ; '
               'last iteration ends at caller return. Logit spans include scale/argmax work. '
               'AXI address and W paths run in parallel; do not add both. '
               'The image hash identifies the supplied image; use the matching remote archive.\n')
    (out/'summary.md').write_text(report,encoding='utf-8')
    print(report)
    print(f'PASS access profile summary: {out}; incomplete tail={incomplete}')


if __name__ == '__main__':
    main()
