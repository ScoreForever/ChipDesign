"""Fail-closed reader for the Cadence per-bit mailbox audit (1 ns units)."""
import csv
import math
from pathlib import Path


def expected_paths():
    result = set()
    def pin(stem, bit, terminal):
        return f'i_host_{stem}_reg[{bit}]/{terminal}'
    for target, offset, width in [('cmd_data_o', 0, 32), ('cmd_addr_o', 32, 32), ('cmd_op_o', 64, 8)]:
        for bit in range(width):
            result.add(('request', pin('req_payload', offset+bit, 'Q'), pin(target, bit, 'D'), 'late'))
    for field, width in [('data', 32), ('error', 5)]:
        for bit in range(width):
            result.add(('response', pin('rsp_mailbox_'+field, bit, 'Q'), pin('reply_'+field, bit, 'D'), 'late'))
    for bank in ['req_sync', 'ack_sync']:
        for mode in ['late', 'early']:
            result.add((bank, pin(bank, 0, 'Q'), pin(bank, 1, 'D'), mode))
    return result


def validate(path):
    try:
        with Path(path).open() as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        found = set()
        for row in rows:
            normalize = lambda name: name.replace('i_host/', 'i_host_', 1)
            key = (row['kind'], normalize(row['from']), normalize(row['to']), row['mode'])
            if key in found:
                return False
            found.add(key)
            slack = float(row['slack_ns'])
            if not math.isfinite(slack) or slack < 0:
                return False
            if row['kind'] in ('request', 'response'):
                arrival, required = float(row['arrival_ns']), float(row['required_ns'])
                if not (math.isfinite(arrival) and math.isfinite(required) and
                        arrival >= 0 and 0 < required <= 20.001 and
                        abs(required-arrival-slack) <= .01):
                    return False
        return found == expected_paths() and len(rows) == 113
    except (OSError, ValueError, KeyError, TypeError):
        return False


def validate_synth(path):
    """Mapped-netlist audit: every payload and both first-stage synchronizer
    setup paths present once, with finite nonnegative slack (ns)."""
    try:
        with Path(path).open() as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        normalize = lambda name: name.replace('i_host/', 'i_host_', 1)
        found = {(r['kind'], normalize(r['from']), normalize(r['to']), r['mode']) for r in rows}
        expected = {p for p in expected_paths() if p[3] == 'late'}
        slacks = [float(r['slack_ns']) for r in rows]
        return found == expected and len(rows) == len(expected) and all(math.isfinite(v) and v >= 0 for v in slacks)
    except (OSError, ValueError, KeyError, TypeError):
        return False
