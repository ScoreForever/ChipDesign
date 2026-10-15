"""Fault-injection checks: a passing trace must reject independently corrupted events."""
from __future__ import annotations

import csv
from pathlib import Path
import tempfile

from check_fused_events import check_trace


def negative_checks(path, inputs, weights, bias, multiplier, shift, classes=4):
    rows = []
    with Path(path).open() as stream:
        for row in csv.DictReader(stream):
            rows.append(row)
            if row['done'] == '1':
                break
    faults = [('retire', 'retire', '0', 'array retirement latency'),
              ('retire_tag', 'retire', None, 'retirement bank/tag'),
              ('array_value', 'retire', None, 'array dot product'),
              ('activation_address', 'issue', None, 'activation address'),
              ('pool_value', 'rq', None, 'pool/quantizer input'),
              ('write_value', 'qout', None, 'requant/write data'),
              ('profiler', 'busy', None, 'profiler total'),
              ('requant_latency', 'qout', None, 'requant retirement latency'),
              ('clock_gap', 'busy', None, 'missing/duplicate clock'),
              ('bank_hazard', 'issue', None, 'weight bank overwritten')]
    passed = []
    with tempfile.TemporaryDirectory(prefix='fused-event-faults-') as directory:
        for name, selector, value, expected in faults:
            copy = [dict(row) for row in rows]
            row = next(row for row in copy if row[selector] == '1')
            if name == 'retire': row['retire'] = value
            elif name == 'retire_tag': row['rtag'] = f"{int(row['rtag'], 16)^1:x}"
            elif name == 'array_value': row['psum'] = f"{int(row['psum'], 16)^1:x}"
            elif name == 'activation_address': row['ibase'] = str(int(row['ibase'])^1)
            elif name == 'pool_value': row['qv'] = f"{int(row['qv'], 16)^1:x}"
            elif name == 'write_value': row['wv'] = f"{int(row['wv'], 16)^1:x}"
            elif name == 'profiler': row['total'] = str(int(row['total'])+1)
            elif name == 'requant_latency':
                copy[copy.index(row)-1]['qout'] = '1'
            elif name == 'clock_gap':
                copy.remove(row)
            else:
                row = next(row for row in copy if row['phase'] == '0' and row['weights'] == '8'
                           and row['load'] == '0' and row['issue'] == '1')
                row.update(load='1', lb='0', lr='0')
            mutated = Path(directory)/(name+'.csv')
            with mutated.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(copy[0]))
                writer.writeheader(); writer.writerows(copy)
            try:
                check_trace(mutated, inputs[:1], weights, bias, multiplier, shift, classes)
            except AssertionError as error:
                if expected not in str(error):
                    raise AssertionError(f'{name}: unexpected rejection: {error}') from error
            else:
                raise AssertionError(f'{name}: corrupted trace was accepted')
            passed.append(name)
    return passed
