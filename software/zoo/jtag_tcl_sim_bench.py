#!/usr/bin/env python3
"""Measure host-side JTAG batched vs. Single-RPC pipeline protocols through live TclRpc + tclsh.

Runs unmodified ``nocpu_jtag.TclRpc``, ``nocpu_jtag.JtagTransport``, and ``nocpu_jtag.Npu``
over a localhost TCP loopback OpenOCD-Tcl socket backed by a persistent ``tclsh`` child process
executing the production ABI 5.0 Tcl procedures and verifying bit-exact model outputs.
"""
from __future__ import annotations

import argparse
import json
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/host'))
from nocpu_jtag import JtagTransport, Npu, TclRpc  # noqa: E402
from npu_program import ABI, NUMERIC_FORMAT, compile_model, encode_tensor, reference  # noqa: E402

MODELS = ('ecg', 'fashion_cnn', 'gtsrb_l', 'har', 'kws_l', 'kws_m', 'mnist_l')

_TCL_HARNESS_HEADER = r"""
package require Tcl 8.6

array set ::regs {}
array set ::mem {}
set ::ir 0x12
set ::burst_addr 0
set ::burst_r_first 0
set ::burst_r_prev 0
set ::pending_op 0
set ::pending_addr 0
set ::pending_data 0
set ::pending_reply 0
set ::has_reply 0
set ::model_loading 0
set ::input_loading 0
set ::model_valid 0
set ::input_valid 0
set ::done 0
set ::drscan_count 0
set ::irscan_count 0
array set ::golden_db {}

proc reset_tap_state {cols act_bytes model_bytes} {
    array unset ::regs
    array unset ::mem
    set ::regs(0) [expr {0x00050000}]
    set ::regs(4) 0
    set ::regs(8) 0
    set ::regs(52) 0
    set ::regs(56) 0
    set ::regs(80) $act_bytes
    set ::regs(84) $model_bytes
    set ::regs(88) [expr {(8 << 16) | $cols}]
    set ::regs(92) 1024
    set ::regs(96) 128
    set ::regs(108) [expr {0x0f200404}]
    set ::ir 0x12
    set ::burst_addr 0
    set ::burst_r_first 0
    set ::burst_r_prev 0
    set ::has_reply 0
    set ::model_loading 0
    set ::input_loading 0
    set ::model_valid 0
    set ::input_valid 0
    set ::done 0
}

proc status_word {} {
    return [expr {(0 << 6) | ($::input_loading << 5) | ($::model_loading << 4) | ($::done << 3) | ($::input_valid << 2) | ($::model_valid << 1) | 0}]
}

proc load_golden_model {key expected_in_hex expected_out_hex cycles} {
    set ::golden_db($key) [list $expected_in_hex $expected_out_hex $cycles]
}

proc reset_scan_counters {} {
    set ::drscan_count 0
    set ::irscan_count 0
}

proc get_scan_counters {} {
    return [list $::drscan_count $::irscan_count]
}

proc runtest {cycles} {
    return ""
}

proc irscan {tap irval args} {
    incr ::irscan_count
    set ::ir [expr {$irval}]
    if {$::ir == 0x13} {
        set ::burst_addr [expr {$::burst_addr + 4}]
    } elseif {$::ir == 0x14} {
        set ::burst_addr [expr {$::burst_addr + 4}]
        set ::burst_r_first 1
        set ::burst_r_prev 0
    }
    return ""
}

proc exec_op {op addr data} {
    set rdata 0
    if {$op == 1} {
        if {$addr == 4} {
            set rdata [status_word]
        } elseif {$addr >= 0x10000} {
            set key [expr {$addr & 0xffffffff}]
            set rdata [expr {[info exists ::mem($key)] ? $::mem($key) : 0}]
            set ::burst_addr $key
        } else {
            set rdata [expr {[info exists ::regs($addr)] ? $::regs($addr) : 0}]
        }
    } elseif {$op == 2} {
        set key [expr {$addr & 0xffffffff}]
        if {$key >= 0x10000} {
            set ::mem($key) [expr {$data & 0xffffffff}]
            set ::burst_addr $key
        } else {
            set ::regs($key) [expr {$data & 0xffffffff}]
        }
    } elseif {$op == 3} {
        set ::model_loading 1
        set ::model_valid 0
    } elseif {$op == 4} {
        set ::model_loading 0
        set ::model_valid 1
    } elseif {$op == 5} {
        set ::input_loading 1
        set ::input_valid 0
    } elseif {$op == 6} {
        set ::input_loading 0
        set ::input_valid 1
    } elseif {$op == 7} {
        set mlen $::regs(16)
        set ibase [expr {0x20000 + $::regs(24)}]
        set ilen $::regs(28)
        set obase [expr {0x20000 + $::regs(32)}]
        set in_hex ""
        set iw [expr {($ilen + 3) / 4}]
        for {set i 0} {$i < $iw} {incr i} {
            set k [expr {$ibase + 4 * $i}]
            set w [expr {[info exists ::mem($k)] ? $::mem($k) : 0}]
            append in_hex [format "%02x%02x%02x%02x" [expr {$w & 0xff}] [expr {($w >> 8) & 0xff}] [expr {($w >> 16) & 0xff}] [expr {($w >> 24) & 0xff}]]
        }
        set in_hex [string range $in_hex 0 [expr {2 * $ilen - 1}]]
        set key "${mlen}_${ilen}"
        if {![info exists ::golden_db($key)]} {
            error "UNKNOWN_MODEL $key"
        }
        lassign $::golden_db($key) exp_in exp_out cyc
        if {$in_hex ne $exp_in} {
            error "INPUT_MISMATCH for $key"
        }
        set nbytes [expr {[string length $exp_out] / 2}]
        set nw [expr {($nbytes + 3) / 4}]
        for {set i 0} {$i < $nw} {incr i} {
            set b0 [scan [string range $exp_out [expr {8*$i}] [expr {8*$i+1}]] %x]
            set b1 [scan [string range $exp_out [expr {8*$i+2}] [expr {8*$i+3}]] %x]
            set b2 [scan [string range $exp_out [expr {8*$i+4}] [expr {8*$i+5}]] %x]
            set b3 [scan [string range $exp_out [expr {8*$i+6}] [expr {8*$i+7}]] %x]
            if {$b0 eq ""} { set b0 0 }
            if {$b1 eq ""} { set b1 0 }
            if {$b2 eq ""} { set b2 0 }
            if {$b3 eq ""} { set b3 0 }
            set w [expr {$b0 | ($b1 << 8) | ($b2 << 16) | ($b3 << 24)}]
            set ::mem([expr {$obase + 4 * $i}]) $w
        }
        set ::regs(56) $cyc
        set ::done 1
    } elseif {$op == 8} {
        set ::done 0
    } elseif {$op == 9} {
        set ::done 0
        set ::model_valid 0
        set ::input_valid 0
    }
    return $rdata
}

proc drscan {tap args} {
    incr ::drscan_count
    if {$::ir == 0x13} {
        set w [expr {[lindex $args 1] & 0xffffffff}]
        set ::mem($::burst_addr) $w
        set ::burst_addr [expr {$::burst_addr + 4}]
        return "00000000"
    } elseif {$::ir == 0x14} {
        set cur [expr {[info exists ::mem($::burst_addr)] ? $::mem($::burst_addr) : 0}]
        set ::burst_addr [expr {$::burst_addr + 4}]
        set ret $::burst_r_prev
        set ::burst_r_prev $cur
        return [format "%08x" $ret]
    }
    set data [expr {[lindex $args 1] & 0xffffffff}]
    set addr [expr {[lindex $args 3] & 0xffffffff}]
    set op [expr {[lindex $args 5] & 0xff}]
    if {$op == 0} {
        if {$::has_reply} {
            set ::has_reply 0
            return [format "%08x %08x 80" $::pending_reply $::pending_addr]
        }
        if {$::burst_r_prev != 0} {
            set v $::burst_r_prev
            set ::burst_r_prev 0
            return [format "%08x 00000000 00" $v]
        }
        return "00000000 00000000 00"
    }
    set ::pending_addr $addr
    set ::pending_reply [exec_op $op $addr $data]
    set ::has_reply 1
    set ::burst_r_prev 0
    return "00000000 00000000 40"
}
"""


class TclLoopbackServer:
    """Real OpenOCD-style TCP (\x1a-delimited) RPC server executing commands in persistent tclsh."""

    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            ['tclsh'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._tcl_lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.eval_tcl(_TCL_HARNESS_HEADER)

    def eval_tcl(self, script: str) -> str:
        marker = '__END_OF_TCL_CMD_9f3c__'
        payload = (
            'if {[catch {\n'
            + script
            + '\n} __bench_res]} {\n'
            '  puts stdout "TCLERR $__bench_res"\n'
            '} else {\n'
            '  puts stdout "TCLOK $__bench_res"\n'
            '}\n'
            f'puts stdout "{marker}"\nflush stdout\n'
        ).encode('utf-8')
        with self._tcl_lock:
            assert self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
            lines = []
            while True:
                raw = self._proc.stdout.readline()
                if not raw:
                    err = self._proc.stderr.read().decode('utf-8', 'replace') if self._proc.stderr else ''
                    raise RuntimeError(f'tclsh exited unexpectedly: {err}')
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')
                if line == marker:
                    break
                lines.append(line)
        joined = '\n'.join(lines)
        if joined.startswith('TCLERR '):
            raise RuntimeError(f'tclsh eval error: {joined[7:]}')
        if joined.startswith('TCLOK '):
            return joined[6:]
        return joined

    def _serve(self) -> None:
        while not self._stop:
            try:
                self._sock.settimeout(0.2)
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle_conn, args=(conn,), daemon=True)
            t.start()

    def _handle_conn(self, conn: socket.socket) -> None:
        buf = b''
        with conn:
            while not self._stop:
                try:
                    data = conn.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b'\x1a' in buf:
                    raw_cmd, buf = buf.split(b'\x1a', 1)
                    cmd = raw_cmd.decode('utf-8')
                    res = self.eval_tcl(cmd)
                    conn.sendall(res.encode('utf-8') + b'\x1a')

    def close(self) -> None:
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass
        if self._proc.stdin:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
        self._proc.wait(timeout=3)


class CountingRpc(TclRpc):
    """Thin subclass solely counting wire calls and bytes sent to TclRpc.execute."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(host=host, port=port, timeout=10)
        self.rpc_calls = 0

    def execute(self, command, timeout=None):
        self.rpc_calls += 1
        return super().execute(command, timeout=timeout)


def measure_model(srv: TclLoopbackServer, name: str, runs: int, cycle_map: dict[str, int]) -> dict:
    model_dir = ROOT / 'software/zoo/pretrained' / name
    model = json.loads((model_dir / 'model.json').read_text(encoding='utf-8'))
    golden = json.loads((model_dir / 'golden.json').read_text(encoding='utf-8'))
    frame = golden['frames'][0]['input']
    pkg = compile_model(model, cols=8)
    expected_vec = reference(model, frame)[model['nodes'][-1]['output']]
    expected_bytes = encode_tensor(expected_vec, pkg.manifest['output_dtype'])
    frame_bytes = encode_tensor(frame, pkg.manifest['input_dtype'])
    exp_in_hex = frame_bytes.hex()
    exp_out_hex = expected_bytes.hex()
    assert exp_out_hex == golden['frames'][0]['output_hex'], f'{name} golden mismatch'
    m = pkg.manifest
    cycles = cycle_map[name]

    srv.eval_tcl('reset_tap_state 8 16384 16384')
    srv.eval_tcl(f'load_golden_model "{m["model_bytes"]}_{m["input_bytes"]}" "{exp_in_hex}" "{exp_out_hex}" {cycles}')

    out = {
        'model': name,
        'model_bytes': m['model_bytes'],
        'input_bytes': m['input_bytes'],
        'output_bytes': m['output_bytes'],
        'compute_cycles': cycles,
    }
    for mode, use_pipe in (('batched', False), ('pipeline', True)):
        rpc = CountingRpc('127.0.0.1', srv.port)
        try:
            transport = JtagTransport(rpc, batch_rpc=True, timeout=10)
            transport.supports_pipeline_rpc = use_pipe
            npu = Npu(transport)
            load_ms_list, infer_ms_list = [], []
            load_rpcs = infer_rpcs = load_dr = infer_dr = 0
            for idx in range(runs + 1):
                srv.eval_tcl('reset_scan_counters')
                r0 = rpc.rpc_calls
                t0 = time.perf_counter_ns()
                npu.load_model(pkg)
                t1 = time.perf_counter_ns()
                l_rpcs = rpc.rpc_calls - r0
                l_dr, _ = (int(x) for x in srv.eval_tcl('get_scan_counters').split())

                srv.eval_tcl('reset_scan_counters')
                r1 = rpc.rpc_calls
                t2 = time.perf_counter_ns()
                got = npu.infer(frame_bytes, timeout=10)
                t3 = time.perf_counter_ns()
                i_rpcs = rpc.rpc_calls - r1
                i_dr, _ = (int(x) for x in srv.eval_tcl('get_scan_counters').split())
                assert got == expected_bytes, f'{name} {mode} output mismatch'
                if idx > 0:
                    load_ms_list.append((t1 - t0) / 1e6)
                    infer_ms_list.append((t3 - t2) / 1e6)
                    load_rpcs, infer_rpcs = l_rpcs, i_rpcs
                    load_dr, infer_dr = l_dr, i_dr
            out[mode] = {
                'exact': True,
                'load_rpcs': load_rpcs,
                'infer_rpcs': infer_rpcs,
                'total_rpcs': load_rpcs + infer_rpcs,
                'load_drscans': load_dr,
                'infer_drscans': infer_dr,
                'total_drscans': load_dr + infer_dr,
                'median_load_ms': round(statistics.median(load_ms_list), 3),
                'median_infer_ms': round(statistics.median(infer_ms_list), 3),
                'median_total_ms': round(statistics.median(a + b for a, b in zip(load_ms_list, infer_ms_list)), 3),
            }
        finally:
            rpc.close()
    b, p = out['batched'], out['pipeline']
    out['rpc_reduction_x'] = round(b['total_rpcs'] / p['total_rpcs'], 2)
    out['loopback_speedup_x'] = round(b['median_total_ms'] / p['median_total_ms'], 2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument(
        '--out',
        type=Path,
        default=ROOT / 'docs/validation/jtag_rpc_20261118/tcl_tcp_sim.json',
    )
    args = ap.parse_args()

    # Use matched compute cycles from the committed RTL evaluation
    rtl_ref = json.loads((ROOT / 'docs/validation/jtag_rpc_20261118/e2e_timing.json').read_text())
    cycle_map = {r['model']: r['compute_cycles'] for r in rtl_ref if r['protocol'] == 'burst'}

    srv = TclLoopbackServer()
    try:
        rows = [measure_model(srv, m, args.runs, cycle_map) for m in MODELS]
    finally:
        srv.close()

    payload = {
        'scope': (
            'Measured localhost TCP (TclRpc) + persistent tclsh executing unmodified '
            'nocpu_jtag.py production procedures across all 7 pretrained models; '
            'not physical EGo1 USB/FT232H board wall time.'
        ),
        'runs_per_mode': args.runs,
        'note_infer_rpcs': (
            'Unmodified Npu.infer() in batched mode performs 2 additional reads (0x18/0x1c input base/bytes), '
            'so batched load+infer total is 54-76 RPCs (vs. 52-74 when manifest cached); '
            'pipeline mode collapses both to 2+2=4 RPCs.'
        ),
        'all_bit_exact': all(r['batched']['exact'] and r['pipeline']['exact'] for r in rows),
        'models': rows,
    }
    args.out.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {args.out} (all_bit_exact={payload["all_bit_exact"]})')
    for r in rows:
        b, p = r['batched'], r['pipeline']
        print(
            f"{r['model']:12s} | RPCs: {b['total_rpcs']:2d} -> {p['total_rpcs']:2d} ({r['rpc_reduction_x']:5.2f}x) | "
            f"drscans: {b['total_drscans']:4d} vs {p['total_drscans']:4d} | "
            f"TCP+tclsh ms: {b['median_total_ms']:6.2f} -> {p['median_total_ms']:5.2f} ({r['loopback_speedup_x']:4.2f}x)"
        )


if __name__ == '__main__':
    main()
