#!/usr/bin/env python3
"""INT4 NPU host for the ABI defined by npu_program."""
from __future__ import annotations
import argparse
from enum import IntEnum
import json
from pathlib import Path
import socket
import time

from npu_program import ABI, NUMERIC_FORMAT, encode_tensor, decode_tensor


class Op(IntEnum):
    READ=1; WRITE=2; MODEL_BEGIN=3; MODEL_COMMIT=4; INPUT_BEGIN=5
    INPUT_COMMIT=6; START=7; ACK=8; RESET=9; CLEAR_ERROR=10

ERRORS={1:'opcode',2:'address',3:'alignment',4:'busy',5:'state',6:'order',
        7:'incomplete',8:'parameter',9:'result pending',10:'timeout',
        17:'program header',18:'descriptor',19:'shape',20:'range',21:'uninitialized source',22:'quantization'}

PERF_NAMES=(
    'active_cycles','model_requests','model_stall_cycles','model_max_stall',
    'model_responses','model_wait_response_cycles',
    'activation_requests','activation_stall_cycles','activation_max_stall',
    'activation_responses','activation_wait_response_cycles','writes','mac_issues',
    'mac_wait_activation_cycles','mac_wait_weight_cycles','mac_wait_slot_cycles',
    'kernel_prepare_cycles','result_slots_peak','weight_banks_peak','inflight_peak',
    'post_lanes','gather_contiguous_groups','gather_scalar_groups','gather_zero_groups')


class TclRpc:
    def __init__(self, host='127.0.0.1', port=6666, timeout=5):
        self.socket=socket.create_connection((host,port),timeout)
        self.socket.settimeout(timeout)
        self.timeout = timeout
        self.buffer = b''
        self._poisoned = False
        self._closed = False

    def execute(self, command, timeout=None):
        if self._poisoned or self._closed:
            raise ConnectionError('Tcl RPC connection is closed')
        duration = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + duration
        try:
            self.socket.settimeout(max(0.001, deadline - time.monotonic()))
            self.socket.sendall(command.encode('ascii') + b'\x1a')
            while b'\x1a' not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Tcl RPC deadline expired')
                self.socket.settimeout(remaining)
                block = self.socket.recv(4096)
                if not block:
                    raise ConnectionError('OpenOCD closed Tcl RPC connection')
                self.buffer += block
                if len(self.buffer) > 1_000_000:
                    raise ConnectionError('oversized Tcl RPC response')
                if b'\x1a' not in self.buffer and time.monotonic() >= deadline:
                    raise TimeoutError('Tcl RPC deadline expired')
            result, self.buffer = self.buffer.split(b'\x1a', 1)
            return result.decode('utf-8').strip()
        except (OSError, UnicodeDecodeError):
            self._poisoned = True
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            self.socket.close()


# Execute the existing mailbox handshake inside OpenOCD, without removing
# any scan or replaying a command after an uncertain outcome. Keep 32/32/8
# fields separate: OpenOCD Jim Tcl need not support 72-bit integers.
_EXCHANGE_TCL = r"""
proc chipdesign_scan_v5 {tap data addr op} {
    drscan $tap 32 $data 32 $addr 8 $op -endstate IDLE
}
proc chipdesign_checked_v5 {tap timeout_ms op addr data} {
    set r [chipdesign_exchange_v5 $tap $op $addr $data $timeout_ms]
    if {[lindex $r 0] ne "OK"} { error $r }
    if {[llength $r] != 4} { error "BAD_REPLY $r" }
    foreach field [lrange $r 1 3] pattern {{^[0-9a-fA-F]{1,8}$} {^[0-9a-fA-F]{1,8}$} {^[0-9a-fA-F]{1,2}$}} {
        if {![regexp $pattern $field]} { error "BAD_REPLY $r" }
    }
    set status [expr "0x[lindex $r 3]"]
    if {($status & 0x20) || ($status & 0xc0) != 0x80} { error "BAD_REPLY $r" }
    if {[expr "0x[lindex $r 2]"] != $addr} { error "ADDR_MISMATCH $addr $r" }
    set code [expr {$status & 31}]
    if {$code} { error "NPU_ERR $code $op $addr" }
    return [format 0x%08x [expr {"0x[lindex $r 1]" & 0xffffffff}]]
}
proc chipdesign_reads_v5 {tap timeout_ms addrs} {
    if {[catch {
    set vals {}
    foreach addr $addrs { lappend vals [chipdesign_checked_v5 $tap $timeout_ms 0x01 $addr 0x0] }
    return "OK [join $vals]"
    } err]} { return $err }
}
proc chipdesign_check_scan_fields_v5 {reply} {
    if {[llength $reply] != 3} { error "BAD_REPLY $reply" }
    foreach field $reply pattern {{^(0x)?[0-9a-fA-F]{1,8}$} {^(0x)?[0-9a-fA-F]{1,8}$} {^(0x)?[0-9a-fA-F]{1,2}$}} {
        if {![regexp $pattern $field]} { error "BAD_REPLY $reply" }
    }
}
proc chipdesign_wblock_v5 {tap timeout_ms burst addr words} {
    if {![llength $words]} { return }
    chipdesign_checked_v5 $tap $timeout_ms 0x02 $addr [lindex $words 0]
    if {$burst && [llength $words] > 1} {
        irscan $tap 0x13 -endstate IDLE
        foreach v [lrange $words 1 end] {
            set w [format 0x%08x [expr {$v & 0xffffffff}]]
            drscan $tap 32 $w -endstate IDLE
        }
        irscan $tap 0x12 -endstate IDLE
        set chk [chipdesign_scan_v5 $tap 0x0 0x0 0x0]
        chipdesign_check_scan_fields_v5 $chk
        if {[expr "0x[string map {0x {}} [lindex $chk 2]]"] & 0x20} { error BURST_FAULT }
    } else {
        set i 1
        foreach v [lrange $words 1 end] {
            chipdesign_checked_v5 $tap $timeout_ms 0x02 [format 0x%08x [expr {($addr+4*$i) & 0xffffffff}]] $v
            incr i
        }
    }
}
proc chipdesign_rblock_v5 {tap timeout_ms burst addr count} {
    if {$count <= 0} { return {} }
    set first_hex [chipdesign_checked_v5 $tap $timeout_ms 0x01 $addr 0x0]
    if {$burst && $count > 1} {
        irscan $tap 0x14 -endstate IDLE
        set _r {}
        for {set i 1} {$i < $count} {incr i} { lappend _r [drscan $tap 32 0]; runtest 4 }
        irscan $tap 0x12 -endstate IDLE
        set last [chipdesign_scan_v5 $tap 0x0 0x0 0x0]
        chipdesign_check_scan_fields_v5 $last
        if {[expr "0x[string map {0x {}} [lindex $last 2]]"] & 0x20} { error BURST_FAULT }
        set vals [concat [list $first_hex] [lrange $_r 1 end] [list [lindex $last 0]]]
        set result [list $first_hex]
        foreach v [lrange $vals 1 end] {
            if {![regexp {^(0x)?[0-9a-fA-F]{1,8}$} $v]} { error BAD_REPLY }
            regsub {^0x} $v {} v
            lappend result [format 0x%08x [expr {"0x$v" & 0xffffffff}]]
        }
        return $result
    }
    set vals [list $first_hex]
    for {set i 1} {$i < $count} {incr i} {
        lappend vals [chipdesign_checked_v5 $tap $timeout_ms 0x01 [format 0x%08x [expr {($addr+4*$i) & 0xffffffff}]] 0x0]
    }
    return $vals
}
proc chipdesign_load_v5 {tap timeout_ms burst regs_flat image_words} {
    if {[catch {
    chipdesign_checked_v5 $tap $timeout_ms 0x03 0 0
    foreach {addr data} $regs_flat { chipdesign_checked_v5 $tap $timeout_ms 0x02 $addr $data }
    chipdesign_wblock_v5 $tap $timeout_ms $burst 0x10000 $image_words
    chipdesign_checked_v5 $tap $timeout_ms 0x04 0 0
    return OK
    } err]} { return $err }
}
proc chipdesign_infer_v5 {tap cmd_timeout_ms infer_timeout_ms burst expected_in_bytes frame_words} {
    if {[catch {
    set in_base [chipdesign_checked_v5 $tap $cmd_timeout_ms 1 0x18 0]
    set in_bytes [chipdesign_checked_v5 $tap $cmd_timeout_ms 1 0x1c 0]
    if {$in_bytes != $expected_in_bytes} { return "IN_LEN $in_bytes" }
    chipdesign_checked_v5 $tap $cmd_timeout_ms 5 0 0
    chipdesign_wblock_v5 $tap $cmd_timeout_ms $burst [format 0x%08x [expr {(0x20000+$in_base) & 0xffffffff}]] $frame_words
    chipdesign_checked_v5 $tap $cmd_timeout_ms 6 0 0
    set t0 [clock milliseconds]
    chipdesign_checked_v5 $tap $cmd_timeout_ms 7 0 0
    while {1} {
        set s [chipdesign_checked_v5 $tap $cmd_timeout_ms 1 4 0]
        if {$s & 64} {
            set err [chipdesign_checked_v5 $tap $cmd_timeout_ms 1 8 0]
            set op [chipdesign_checked_v5 $tap $cmd_timeout_ms 1 0x34 0]
            return "NPU_FAULT $err $op"
        }
        if {$s & 8} { break }
        if {[clock milliseconds] >= $t0+$infer_timeout_ms} { return INFER_TIMEOUT }
        after 1
    }
    set wait_ms [expr {[clock milliseconds]-$t0}]
    set base [chipdesign_checked_v5 $tap $cmd_timeout_ms 1 0x20 0]
    set bytes [chipdesign_checked_v5 $tap $cmd_timeout_ms 1 0x24 0]
    set words [chipdesign_rblock_v5 $tap $cmd_timeout_ms $burst [format 0x%08x [expr {(0x20000+$base) & 0xffffffff}]] [expr {($bytes+3)/4}]]
    return "OK $wait_ms $bytes [join $words]"
    } err]} { return $err }
}
proc chipdesign_exchange_v5 {tap op addr data timeout_ms} {
    set previous [chipdesign_scan_v5 $tap 0x0 0x0 0x0]
    set status [expr "0x[lindex $previous 2]"]
    if {$status & 0x40} { return BUSY }
    chipdesign_scan_v5 $tap $data $addr $op
    set deadline [expr {[clock milliseconds] + $timeout_ms}]
    while {[clock milliseconds] < $deadline} {
        set reply [chipdesign_scan_v5 $tap 0x0 0x0 0x0]
        set status [expr "0x[lindex $reply 2]"]
        if {$status & 0x20} { return OVERRUN }
        if {($status & 0xc0) == 0x80} { return "OK $reply" }
    }
    return TIMEOUT
}
"""


class JtagTransport:
    def __init__(self, rpc, tap='npu.tap', timeout=5, batch_rpc=True):
        if not tap or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-' for c in tap):
            raise ValueError('invalid TAP name')
        self.rpc,self.tap,self.timeout=rpc,tap,timeout
        self.batch_rpc=batch_rpc
        self.supports_pipeline_rpc=bool(batch_rpc)
        if batch_rpc:
            reply=self.rpc.execute(_EXCHANGE_TCL)
            # Jim Tcl returns the procedure name; Tcl commonly returns empty.
            if reply not in ('', 'chipdesign_exchange_v5'):
                raise RuntimeError(f'OpenOCD mailbox helper failed: {reply}')
        self.rpc.execute(f'irscan {tap} 0x12 -endstate IDLE')

    @staticmethod
    def _hexlist(values):
        values=list(values)
        if any(not 0 <= int(v) < 2**32 for v in values):
            raise ValueError('word outside uint32 range')
        return '{'+' '.join(f'0x{int(v):08x}' for v in values)+'}'

    def _pipeline(self, command, timeout=None):
        try:
            reply=self.rpc.execute(command, timeout=timeout)
        except (TimeoutError, ConnectionError) as exc:
            raise type(exc)('JTAG RPC failed; operation may have executed') from exc
        fields=reply.split()
        token=fields[0] if fields else ''
        if token=='OK': return fields[1:]
        if token=='IN_LEN': raise ValueError(f'expected {int(fields[1],0)} input bytes')
        if token=='NPU_FAULT': raise RuntimeError(f'NPU fault {int(fields[1],0)}, operator {int(fields[2],0)}')
        if token=='NPU_ERR':
            code,op,addr=(int(v,0) for v in fields[1:])
            raise RuntimeError(f"NPU error {code}: {ERRORS.get(code, 'unknown')} (op={op}, address=0x{addr:x})")
        if token=='BUSY': raise RuntimeError('JTAG mailbox is occupied')
        if token in ('TIMEOUT','INFER_TIMEOUT'): raise TimeoutError(f'{reply}; operation may have executed')
        raise RuntimeError(f'{reply or "unexpected reply"}; operation may have executed')

    @staticmethod
    def _words(fields):
        try:
            words=[int(v,16) for v in fields]
            if any(not 0<=v<2**32 for v in words): raise ValueError()
            return words
        except ValueError as exc:
            raise RuntimeError('BAD_REPLY invalid uint32 output; operation may have executed') from exc

    def read_words_batch(self, addrs):
        return self._words(self._pipeline(f'chipdesign_reads_v5 {self.tap} {max(1,int(self.timeout*1000))} {self._hexlist(addrs)}'))

    def pipeline_load_model(self, reg_pairs, image_words, burst=True):
        self._pipeline(f'chipdesign_load_v5 {self.tap} {max(1,int(self.timeout*1000))} {int(burst)} {self._hexlist(v for pair in reg_pairs for v in pair)} {self._hexlist(image_words)}', max(self.timeout*2,10.0))

    def pipeline_infer(self, frame_words, expected_in_bytes, timeout=10, burst=True):
        fields=self._pipeline(f'chipdesign_infer_v5 {self.tap} {max(1,int(self.timeout*1000))} {max(1,int(timeout*1000))} {int(burst)} {expected_in_bytes} {self._hexlist(frame_words)}', max(self.timeout,timeout)+max(2.0,self.timeout))
        try:
            wait_ms=int(fields[0]); out_bytes=int(fields[1],0)
            words=self._words(fields[2:])
            if out_bytes<0 or len(words)!=(out_bytes+3)//4: raise ValueError()
        except (ValueError,IndexError) as exc:
            raise RuntimeError('BAD_REPLY output length; operation may have executed') from exc
        return b''.join(v.to_bytes(4,'little') for v in words)[:out_bytes],wait_ms

    def scan(self, packet):
        # OpenOCD defaults unprefixed numbers to decimal. Use explicit hexadecimal
        # data/address/opcode fields and reassemble its three returned fields.
        reply=self.rpc.execute(
            f'drscan {self.tap} 32 0x{packet & 0xffffffff:08x} '
            f'32 0x{(packet >> 32) & 0xffffffff:08x} '
            f'8 0x{(packet >> 64) & 0xff:02x} -endstate IDLE')
        return self._decode_scan(reply)

    @staticmethod
    def _decode_scan(reply):
        try:
            words=reply.split()
            if len(words)!=3:
                raise ValueError
            fields=[int(word,16) for word in words]
            if any(len(word)>width//4 or not 0<=value<2**width
                   for word,value,width in zip(words,fields,(32,32,8))):
                raise ValueError
            return fields[0] | (fields[1]<<32) | (fields[2]<<64)
        except ValueError as exc:
            raise RuntimeError(f'OpenOCD drscan failed: {reply}') from exc

    def exchange(self, op, addr=0, data=0):
        if not 0<int(op)<256 or not 0<=addr<2**32 or not -(2**31)<=data<2**32:
            raise ValueError('command field outside packet range')
        if self.batch_rpc:
            command=(f'chipdesign_exchange_v5 {self.tap} 0x{int(op):02x} '
                     f'0x{addr:08x} 0x{int(data)&0xffffffff:08x} '
                     f'{max(1,int(self.timeout*1000))}')
            try:
                reply=self.rpc.execute(command)
            except TimeoutError as exc:
                raise TimeoutError('JTAG RPC timed out; operation may have executed; '
                                   'inspect status before retrying') from exc
            except ConnectionError as exc:
                raise ConnectionError('JTAG RPC connection lost; operation may have executed; '
                                      'inspect status before retrying') from exc
            if reply=='BUSY':
                raise RuntimeError('JTAG mailbox is occupied; poll/recover the previous operation first')
            if reply=='OVERRUN':
                raise RuntimeError('JTAG mailbox overrun; the original operation may have executed')
            if reply=='TIMEOUT':
                raise TimeoutError('JTAG response timed out; operation may have executed; '
                                   'inspect status before retrying')
            if not reply.startswith('OK '):
                raise RuntimeError(f'OpenOCD mailbox transaction failed; operation may have executed: {reply}')
            response=self._decode_scan(reply[3:]);status=response>>64
            if status&0x20 or status&0xc0!=0x80:
                raise RuntimeError('Invalid completed JTAG response; inspect status before retrying')
            if (response>>32)&0xffffffff != addr:
                raise RuntimeError('JTAG response address mismatch')
            code=status&31
            if code:
                raise RuntimeError(f'NPU error {code}: {ERRORS.get(code,"unknown")} (op={int(op)}, address=0x{addr:x})')
            return response&0xffffffff
        # Poll before submission: never overwrite somebody else's pending command.
        previous=self.scan(0)
        if (previous>>70)&1:
            raise RuntimeError('JTAG mailbox is occupied; poll/recover the previous operation first')
        self.scan((int(op)<<64)|(addr<<32)|(int(data)&0xffffffff))
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            response=self.scan(0)
            status=response>>64
            if status&0x20:
                raise RuntimeError('JTAG mailbox overrun; the original operation may have executed')
            if status&0x80 and not status&0x40:
                if (response>>32)&0xffffffff != addr:
                    raise RuntimeError('JTAG response address mismatch')
                code=status&31
                if code:
                    raise RuntimeError(f'NPU error {code}: {ERRORS.get(code,"unknown")} (op={int(op)}, address=0x{addr:x})')
                return response&0xffffffff
        # In particular, do not automatically retry START/COMMIT after timeout.
        raise TimeoutError('JTAG response timed out; operation may have executed; inspect status before retrying')

    # Burst transfers: one 32-bit scan per word at the address following the
    # last IR=12h command; many scans travel in one Tcl RPC round trip.
    def burst_write(self, words):
        self.rpc.execute(f'irscan {self.tap} 0x13 -endstate IDLE')
        for i in range(0,len(words),64):
            self.rpc.execute(' ; '.join(f'drscan {self.tap} 32 0x{w&0xffffffff:08x} -endstate IDLE' for w in words[i:i+64]))
        self.burst_check()

    def burst_read(self, count):
        """Read `count` words after the last command's address."""
        if count<=0:return []
        self.rpc.execute(f'irscan {self.tap} 0x14 -endstate IDLE')
        values=[]
        for i in range(0,count,64):
            n=min(64,count-i)
            # Tcl returns only the last command's value, so collect explicitly.
            # runtest 4: the read must cross back through the TCK synchronizer
            # before the next Capture-DR returns it.
            script='set _r {}; '+' ; '.join(f'lappend _r [drscan {self.tap} 32 0] ; runtest 4' for _ in range(n))+' ; set _r'
            values.extend(int(v,16) for v in self.rpc.execute(script).split())
        last=self.burst_check()
        # Each scan returned the previous read; the first one is stale.
        return (values[1:]+[last&0xffffffff])

    def burst_check(self):
        self.rpc.execute(f'irscan {self.tap} 0x12 -endstate IDLE')
        response=self.scan(0)
        if (response>>69)&1:
            raise RuntimeError('JTAG burst fault (busy mailbox or NPU error); the transfer is incomplete')
        return response


def _ensure_libusb_backend():
    """Wire bundled libusb_package into pyusb when system libusb is absent."""
    import usb.backend.libusb1
    if usb.backend.libusb1.get_backend() is not None:
        return
    try:
        import libusb_package
    except ImportError:
        return
    orig_get_backend = usb.backend.libusb1.get_backend
    if getattr(orig_get_backend, '_chipdesign_libusb_wrapped', False):
        return
    def _wrapped_get_backend(find_library=None):
        backend = orig_get_backend(find_library)
        if backend is None and find_library is None:
            backend = orig_get_backend(find_library=libusb_package.find_library)
        return backend
    _wrapped_get_backend._chipdesign_libusb_wrapped = True
    usb.backend.libusb1.get_backend = _wrapped_get_backend


_IO_GEOMETRY_REGS = (0x18, 0x1c, 0x20, 0x24)
_TIGHT_POLL_S = 0.003
_POLL_SLEEP_S = 0.0005


class FtdiMpsseTransport(JtagTransport):
    """Direct FT232H MPSSE transport; all scans start and finish in RTI."""
    supports_pipeline_rpc = True

    # FT232H has a 1024 B hardware RX FIFO; burst_read queues 5 RX bytes per
    # 32-bit word before draining via _read(), so 128 words = 640 B fits safely.
    def __init__(self, url='ftdi://ftdi:232h/1', frequency=1_000_000, timeout=5.0, *, sys_clk_hz=50_000_000, burst_read_sys_cycles=24, expected_idcode=1, ftdi_dev=None, write_chunk_words=512, read_chunk_words=128):
        import math
        if min(frequency, sys_clk_hz, burst_read_sys_cycles, timeout, write_chunk_words, read_chunk_words) <= 0:
            raise ValueError('frequencies, latency bound, timeout and chunk sizes must be positive')
        self.timeout, self.batch_rpc = timeout, False
        self.write_chunk_words, self.read_chunk_words = write_chunk_words, read_chunk_words
        self.burst_read_sys_cycles = int(burst_read_sys_cycles)
        self._closed = False
        # Last IR shifted into the TAP (None = unknown) and the committed model's
        # (input_base, input_bytes, output_base, output_bytes); both are dropped on
        # any error, RESET, MODEL_BEGIN or close so a stale value is never reused.
        self._cur_ir = None
        self._cached_io_geometry = None
        if ftdi_dev is None:
            _ensure_libusb_backend()
            from pyftdi.ftdi import Ftdi
            ftdi_dev = Ftdi()
            frequency = ftdi_dev.open_mpsse_from_url(url, frequency=frequency, initial=0x18, direction=0x1b)
        self.ftdi = ftdi_dev
        # Worst-case RTL idle/done read roundtrip in clk_sys (3 req_sync + 2 controller
        # + up to 16 activation-store queue drain + 3 SRAM/reply = 24 cycles) plus 4 TCKs
        # so ack_toggle crosses the 2-flop ack_sync chain and latches reply_data before next CDR.
        self.idle_tcks = max(4, math.ceil(self.burst_read_sys_cycles * frequency / sys_clk_hz) + 4)
        try:
            self.ftdi.write_data(bytes([0x80, 0x18, 0x1b, 0x4b, 5, 0x1f]))
            actual = self.read_idcode()
            if expected_idcode is not None and actual != expected_idcode:
                raise RuntimeError(f'JTAG IDCODE mismatch: 0x{actual:08x}')
            self.irscan(0x12)
        except Exception:
            self.close()
            raise

    def _invalidate_residency(self):
        self._cur_ir = None
        self._cached_io_geometry = None

    def exchange(self, op, addr=0, data=0):
        try:
            if op in (Op.RESET, Op.MODEL_BEGIN):
                self._cached_io_geometry = None
            return super().exchange(op, addr, data)
        except Exception:
            self._invalidate_residency()
            raise

    @staticmethod
    def _tms(bits, count):
        return bytes((0x4b, count-1, bits))

    def _encode_scan(self, nbits, value, read=True, ir=False):
        if nbits < 1 or value < 0 or value >= 1 << nbits:
            raise ValueError('scan value outside bit range')
        out = bytearray(self._tms(3 if ir else 1, 4 if ir else 3))
        full, rem = divmod(nbits-1, 8)
        if full:
            # FTDI AN_108: 0x39 = clock bytes in(+ve)/out(-ve) LSB first; 0x19 = bytes out(-ve) LSB first.
            out.extend((0x39 if read else 0x19, (full-1)&255, (full-1)>>8))
            out.extend((value & ((1 << (8*full))-1)).to_bytes(full, 'little'))
        if rem:
            # FTDI AN_108: 0x3b = clock bits in(+ve)/out(-ve) LSB first; 0x1b = bits out(-ve) LSB first.
            out.extend((0x3b if read else 0x1b, rem-1, (value >> (8*full)) & ((1 << rem) - 1)))
        out.extend((0x6b if read else 0x4b, 0, ((value >> (nbits-1)) << 7) | 1))
        out.extend(self._tms(1, 2))
        return bytes(out)

    def _read(self, size):
        result = bytearray()
        deadline = time.monotonic()+self.timeout
        try:
            while len(result) < size:
                result.extend(self.ftdi.read_data_bytes(size-len(result), 1))
                if time.monotonic() >= deadline and len(result) < size:
                    raise TimeoutError('MPSSE USB response timed out; operation may have executed')
            return result
        except Exception:
            self._invalidate_residency()
            raise

    @staticmethod
    def _decode(raw, nbits):
        full, rem = divmod(nbits-1, 8)
        value = int.from_bytes(raw[:full], 'little')
        if rem:
            value |= (raw[full] >> (8-rem)) << (8*full)
        return value | ((raw[-1] >> 7) << (nbits-1))

    def irscan(self, ir_val):
        if getattr(self, '_cur_ir', None) == ir_val:
            return
        self._cur_ir = None
        try:
            self.ftdi.write_data(self._encode_scan(5, ir_val, False, True))
        except Exception:
            self._invalidate_residency()
            raise
        self._cur_ir = ir_val

    def drscan(self, nbits, value, read=True):
        try:
            self.ftdi.write_data(self._encode_scan(nbits, value, read) + (b'\x87' if read else b''))
            return self._decode(self._read((nbits-1)//8 + bool((nbits-1)%8) + 1), nbits) if read else None
        except Exception:
            self._invalidate_residency()
            raise

    def scan(self, packet):
        return self.drscan(72, packet)

    def read_idcode(self):
        self.irscan(1)
        return self.drscan(32, 0)

    def burst_write(self, words):
        words = list(words)
        if not words: return
        try:
            scans = [self._encode_scan(32, int(w)&0xffffffff, False) for w in words]
            self.irscan(0x13)
            prefix = b''
            for i in range(0, len(scans), self.write_chunk_words):
                self.ftdi.write_data(prefix + b''.join(scans[i:i+self.write_chunk_words]))
                prefix = b''
            self.burst_check()
        except Exception:
            self._invalidate_residency()
            raise

    def burst_read(self, count):
        if count <= 0: return []
        try:
            self.irscan(0x14)
            values = []
            idle = b''.join(self._tms(0, min(7, self.idle_tcks-i)) for i in range(0, self.idle_tcks, 7))
            for i in range(0, count, self.read_chunk_words):
                n = min(self.read_chunk_words, count-i)
                self.ftdi.write_data((self._encode_scan(32, 0)+idle)*n+b'\x87')
                raw = self._read(n*5)
                values.extend(self._decode(raw[j:j+5], 32) for j in range(0, n*5, 5))
            return values[1:]+[self.burst_check() & 0xffffffff]
        except Exception:
            self._invalidate_residency()
            raise

    def burst_check(self):
        try:
            self.irscan(0x12)
            deadline = time.monotonic() + self.timeout
            while True:
                response = self.scan(0)
                status = response >> 64
                if status & 0x20:
                    raise RuntimeError('JTAG burst fault: overrun')
                if (status & 0xc0) == 0x80:
                    code = status & 31
                    if code:
                        raise RuntimeError(f'JTAG burst fault: NPU error {code}: {ERRORS.get(code, "unknown")}')
                    return response
                if time.monotonic() >= deadline:
                    raise TimeoutError('JTAG burst completion timed out; transfer state is uncertain')
        except Exception:
            self._invalidate_residency()
            raise

    def read_words_batch(self, addrs):
        return [self.exchange(Op.READ, addr) for addr in addrs]

    def _block(self, addr, words, burst):
        words = list(words)
        if not words: return
        self.exchange(Op.WRITE, addr, words[0])
        if burst and len(words) > 1: self.burst_write(words[1:])
        else:
            for i, word in enumerate(words[1:], 1): self.exchange(Op.WRITE, addr+4*i, word)

    def pipeline_load_model(self, reg_pairs, image_words, burst=True):
        self._cached_io_geometry = None
        try:
            self.exchange(Op.MODEL_BEGIN)
            for addr, data in reg_pairs: self.exchange(Op.WRITE, addr, data)
            self._block(0x10000, image_words, burst)
            self.exchange(Op.MODEL_COMMIT)
            # Read back what the hardware committed, once per model load.
            self._cached_io_geometry = tuple(self.read_words_batch(_IO_GEOMETRY_REGS))
        except Exception:
            self._invalidate_residency()
            raise

    def pipeline_infer(self, frame_words, expected_in_bytes, timeout=10, burst=True):
        try:
            geometry = getattr(self, '_cached_io_geometry', None)
            if geometry is None:
                in_geom = tuple(self.read_words_batch((0x18, 0x1c)))
                out_geom = None
            else:
                in_geom, out_geom = geometry[:2], geometry[2:]
            base, length = in_geom
            if length != expected_in_bytes: raise ValueError(f'expected {length} input bytes')
            self.exchange(Op.INPUT_BEGIN)
            self._block(0x20000+base, frame_words, burst)
            self.exchange(Op.INPUT_COMMIT)
            started = time.monotonic()
            self.exchange(Op.START)
            while True:
                status = self.exchange(Op.READ, 4)
                if status & 64: raise RuntimeError(f'NPU fault {self.exchange(Op.READ, 8)}, operator {self.exchange(Op.READ, 0x34)}')
                if status & 8: break
                elapsed = time.monotonic()-started
                if elapsed >= timeout: raise TimeoutError('inference still active')
                # Tight poll for the first 3 ms (short tiles), then yield the CPU.
                if elapsed >= _TIGHT_POLL_S:
                    time.sleep(_POLL_SLEEP_S)
            wait_ms = int((time.monotonic()-started)*1000)
            base, length = out_geom if out_geom is not None else tuple(self.read_words_batch((0x20, 0x24)))
            count = (length+3)//4
            words = []
            if count:
                words = [self.exchange(Op.READ, 0x20000+base)]
                words += self.burst_read(count-1) if burst else self.read_words_batch(0x20000+base+4*i for i in range(1,count))
            return b''.join(w.to_bytes(4,'little') for w in words)[:length], wait_ms
        except Exception:
            self._invalidate_residency()
            raise

    def close(self):
        self._invalidate_residency()
        self.ftdi.close()


def create_transport(backend='openocd', *, host='127.0.0.1', port=6666, ftdi_url='ftdi://ftdi:232h/1', tck_khz=1000, timeout=5.0, batch_rpc=True, sys_clk_mhz=50.0):
    if backend == 'mpsse':
        transport = FtdiMpsseTransport(ftdi_url, tck_khz*1000, timeout, sys_clk_hz=int(round(sys_clk_mhz * 1e6)))
        return transport, transport.close
    if backend != 'openocd': raise ValueError('unknown JTAG backend')
    rpc = TclRpc(host, port, timeout)
    try:
        return JtagTransport(rpc, timeout=timeout, batch_rpc=batch_rpc), rpc.close
    except Exception:
        rpc.close()
        raise


def signed32(value):
    return value if value<2**31 else value-2**32


class Npu:
    def __init__(self,transport):
        self.transport=transport
        actual_abi = self.read(0)
        if actual_abi != ABI:
            raise RuntimeError(
                f'NPU ABI mismatch: host expects 0x{ABI:08x}, hardware reports 0x{actual_abi:08x}'
            )
    def command(self,op,addr=0,data=0):return self.transport.exchange(op,addr,data)
    def write_block(self,addr,words):
        """Write consecutive words; bursts after the first when the transport can."""
        if not words:return
        self.write(addr,words[0])
        if len(words)>1 and hasattr(self.transport,'burst_write'):self.transport.burst_write(list(words[1:]))
        else:
            for i,w in enumerate(words[1:],1):self.write(addr+4*i,w)
    def read_block(self,addr,count):
        if count<=0:return []
        first=self.read(addr)
        if count>1 and hasattr(self.transport,'burst_read'):return [first]+self.transport.burst_read(count-1)
        return [first]+[self.read(addr+4*i) for i in range(1,count)]
    def read(self,addr):return self.command(Op.READ,addr)
    def write(self,addr,data):return self.command(Op.WRITE,addr,int(data)&0xffffffff)
    def status(self):
        s=self.read(4)
        return dict(busy=bool(s&1),model_ready=bool(s&2),input_ready=bool(s&4),done=bool(s&8),
                    faulted=bool(s&64),error=self.read(8),error_op=self.read(0x34),
                    cycles=self.read(0x38),issues=self.read(0x3c),writes=self.read(0x40))
    def performance(self):
        """Read a completed/idle job's passive counters; never reset or ACK it."""
        if self.read(0x68)!=0x00010018:
            raise RuntimeError('requires performance extension v1 with 24 counters')
        job=self.read(0x2c)
        status=self.read(4)
        if status&1:
            raise RuntimeError('performance counters require an idle/completed NPU')
        values={name:self.read(0x100+4*i) for i,name in enumerate(PERF_NAMES)}
        if self.read(0x2c)!=job or self.read(4)!=status:
            raise RuntimeError('job changed during performance read; discard counters')
        return dict(job=job,**values)
    def configure_trace(self, pc=0, offset=0, trigger_slot=False, enabled=True):
        """Arm the next job; caller must ACK any completed result first."""
        if not 0<=pc<128 or not 0<=offset<2**32:
            raise ValueError('trace PC must be 0..127 and offset must fit uint32')
        self.write(0x188,(pc<<4)|(int(bool(trigger_slot))<<1)|int(bool(enabled)))
        self.write(0x18c,offset)
    def trace(self, include_samples=True):
        from npu_trace import decode_sample
        status=self.read(4);job=self.read(0x2c)
        if status&1:raise RuntimeError('trace requires an idle/completed NPU')
        if self.read(0x180)!=0x00040080:raise RuntimeError('requires INT4 trace extension v4, 128-bit samples')
        header={name:self.read(addr) for name,addr in (
            ('depth',0x184),('configured_control',0x188),('configured_offset',0x18c),
            ('flags',0x190),('count',0x194),('first_cycle',0x198),('last_cycle',0x19c),
            ('sample_stride',0x1a0),('operator_stride',0x1a4),('max_ops',0x1a8),
            ('captured_control',0x1ac),('captured_offset',0x1b0))}
        if header['depth'] not in (0,1024) or header['count']>header['depth'] or header['sample_stride']!=16 or header['operator_stride']!=32 or header['max_ops'] not in (0,128):
            raise RuntimeError('unsupported/corrupt trace geometry')
        if include_samples and header['depth']==0:
            raise RuntimeError('waveform trace is not enabled in this hardware build; use trace(include_samples=False) for operator summaries')
        if not include_samples and header['max_ops']==0:
            raise RuntimeError('per-operator summaries are not enabled in this hardware build (ASIC default); the global performance counters remain available')
        # Summaries absent (max_ops 0): waveform-only capture, no operator rows.
        op_count=self.read(0x14) if header['max_ops'] else 0
        if op_count>128:raise RuntimeError('corrupt trace operator count')
        names=('start_cycle','end_cycle','descriptor','flags','wait_activation_cycles','wait_weight_cycles','wait_slot_cycles','mac_issues')
        operators=[]
        for pc in range(op_count):
            row={name:self.read(0x200+pc*32+i*4) for i,name in enumerate(names)}
            row.update(pc=pc,opcode=row['descriptor']&255)
            row['duration_cycles']=((row['end_cycle']-row['start_cycle'])&0xffffffff) if row['flags']&6 else None
            operators.append(row)
        samples=[]
        if include_samples:
            for index in range(header['count']):
                words=[self.read(0x2000+index*16+i*4) for i in range(4)]
                samples.append(dict(words=words,**decode_sample(words)))
            if samples and (samples[0]['cycle']!=header['first_cycle'] or samples[-1]['cycle']!=header['last_cycle']):
                raise RuntimeError('trace timestamps do not match header')
            if any(((b['cycle']-a['cycle'])&0xffffffff)!=1 for a,b in zip(samples,samples[1:])):
                raise RuntimeError('trace samples are not consecutive')
            if any(x['pc']!=((header['captured_control'] >>4)&255) for x in samples):
                raise RuntimeError('trace PC does not match capture configuration')
        if self.read(0x2c)!=job or self.read(4)!=status:
            raise RuntimeError('job changed during trace read; discard capture')
        return dict(version=4,job=job,status=status,header=header,operators=operators,samples=samples)
    def load_model(self,package):
        m=package.manifest
        # All host checks happen before MODEL_BEGIN invalidates the old model.
        pipeline=getattr(self.transport,'supports_pipeline_rpc',False)
        regs=(0x50,0x54,0x58,0x5c,0x60,0x6c)
        caps=dict(zip(regs,self.transport.read_words_batch(regs))) if pipeline else {r:self.read(r) for r in regs}
        if caps[0x6c]!=NUMERIC_FORMAT or m.get('numeric_format')!=NUMERIC_FORMAT or m.get('packing')!='low_nibble_first':
            raise ValueError('requires packed INT4 / INT32 / Q15 model format')
        if m['abi']!=ABI or m.get('rows')!=(caps[0x58]>>16) or m['cols']!=(caps[0x58]&65535):raise ValueError('ABI/array mismatch; recompile package')
        if len(package.image)!=m['model_bytes'] or len(package.image)%4 or len(package.image)>caps[0x54]:raise ValueError('model capacity/length')
        if not 0<m['op_count']<=caps[0x60] or (m['activation_peak']>caps[0x50] or any(t['base']+t['bytes']>caps[0x50]-len(package.image) for t in m['tensors'].values())):raise ValueError('program/activation capacity')
        if pipeline:
            pairs=list(zip(range(0x10,0x28,4),(m[k] for k in ('model_bytes','op_count','input_base','input_bytes','output_base','output_bytes'))))
            pairs.append((0x64,int(m['input_dtype']=='i32')))
            try:
                self.transport.pipeline_load_model(pairs,[int.from_bytes(package.image[o:o+4],'little') for o in range(0,len(package.image),4)])
            except Exception:
                self.__dict__.pop('manifest',None)
                raise
            self.manifest=dict(m)
            return
        self.command(Op.MODEL_BEGIN)
        for reg,key in zip(range(0x10,0x28,4),('model_bytes','op_count','input_base','input_bytes','output_base','output_bytes')):
            self.write(reg,m[key])
        self.write(0x64,int(m['input_dtype']=='i32'))
        self.write_block(0x10000,[int.from_bytes(package.image[o:o+4],'little') for o in range(0,len(package.image),4)])
        self.command(Op.MODEL_COMMIT)
        self.manifest=dict(m)
    def infer_values(self,values,timeout=10):
        if not hasattr(self,'manifest'):raise RuntimeError('load a model before typed inference')
        m=self.manifest;values=list(values)
        if len(values)!=m['input_elements']:raise ValueError('input element count')
        return decode_tensor(self.infer(encode_tensor(values,m['input_dtype']),timeout),m['output_dtype'],m['output_elements'])
    def infer(self,frame,timeout=10):
        frame=bytes(frame)
        if getattr(self.transport,'supports_pipeline_rpc',False):
            m=getattr(self,'manifest',None)
            if m and len(frame)==m['input_bytes'] and m['input_dtype'] in ('i4','u4') and m['input_elements']%2 and frame[-1]&0xf0:
                raise ValueError('nonzero INT4 input padding nibble')
            words=[int.from_bytes(frame[o:o+4].ljust(4,b'\0'),'little') for o in range(0,len(frame),4)]
            result,_wait_ms=self.transport.pipeline_infer(words,len(frame),timeout=timeout)
            if m and len(result)!=m['output_bytes']: raise ValueError('output length mismatch')
            self.command(Op.ACK)
            return result
        base=self.read(0x18);length=self.read(0x1c)
        if len(frame)!=length:raise ValueError(f'expected {length} input bytes')
        m=getattr(self,'manifest',None)
        if m and m['input_dtype'] in ('i4','u4') and m['input_elements']%2 and frame[-1]&0xf0:
            raise ValueError('nonzero INT4 input padding nibble')
        self.command(Op.INPUT_BEGIN)
        self.write_block(0x20000+base,[int.from_bytes(frame[o:o+4].ljust(4,b'\0'),'little') for o in range(0,len(frame),4)])
        self.command(Op.INPUT_COMMIT);self.command(Op.START)
        deadline=time.monotonic()+timeout
        while True:
            s=self.read(4)
            if s&64:raise RuntimeError(f'NPU fault {self.read(8)}, operator {self.read(0x34)}')
            if s&8:break
            if time.monotonic()>deadline:raise TimeoutError('inference still active; inspect status, do not automatically retry START')
            time.sleep(0.001)
        base=self.read(0x20);length=self.read(0x24)
        result=b''.join(v.to_bytes(4,'little') for v in self.read_block(0x20000+base,(length+3)//4))[:length]
        self.command(Op.ACK)
        return result

def main():
    from npu_program import Package
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['status','perf','trace','trace-config','reset','infer','infer-many'])
    p.add_argument('--package',type=Path);p.add_argument('--input',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--inputs',type=Path,nargs='+',help='ordered input files for infer-many')
    p.add_argument('--legacy-rpc',action='store_true',help='use separate RPC calls for the mailbox handshake; bursts remain enabled')
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=6666)
    p.add_argument('--pc',type=int,default=0);p.add_argument('--offset',type=int,default=0)
    p.add_argument('--trigger-slot',action='store_true');p.add_argument('--disable',action='store_true')
    p.add_argument('--period-ns',type=int,default=20)
    p.add_argument('--backend', choices=['openocd','mpsse'], default='openocd')
    p.add_argument('--ftdi-url', default='ftdi://ftdi:232h/1')
    p.add_argument('--sys-clk-mhz', type=float, default=50.0)
    p.add_argument('--tck-khz', type=int, default=1000)
    args=p.parse_args();transport, closer=create_transport(args.backend, host=args.host, port=args.port, ftdi_url=args.ftdi_url, tck_khz=args.tck_khz, batch_rpc=not args.legacy_rpc, sys_clk_mhz=args.sys_clk_mhz)
    try:
        npu=Npu(transport)
        if args.command=='status':print(json.dumps(npu.status(),indent=2))
        elif args.command=='perf':print(json.dumps(npu.performance(),indent=2))
        elif args.command=='trace-config':npu.configure_trace(args.pc,args.offset,args.trigger_slot,not args.disable)
        elif args.command=='trace':
            from npu_trace import export_trace
            if not args.output:p.error('trace requires --output path.vcd')
            capture=npu.trace()
            if args.package:
                import struct
                package=Package.load(args.package)
                if package.manifest['op_count']!=len(capture['operators']):raise ValueError('package/trace operator count mismatch')
                for row in capture['operators']:
                    if row['flags']&8 and row['descriptor']!=struct.unpack_from('<I',package.image,row['pc']*64)[0]:
                        raise ValueError('package/trace descriptor mismatch')
                    if row['pc']<len(package.manifest['groups']):row['logical_ops']=package.manifest['groups'][row['pc']]
            print(json.dumps(export_trace(capture,args.output,args.period_ns),indent=2))
        elif args.command=='reset':npu.command(Op.RESET)
        elif args.command=='infer-many':
            if not args.package or not args.inputs or not args.output:
                p.error('infer-many requires --package, --inputs and a new --output directory')
            package=Package.load(args.package)
            frames=[path.read_bytes() for path in args.inputs]
            if any(len(frame)!=package.manifest['input_bytes'] for frame in frames):
                raise ValueError('input length differs from package')
            args.output.mkdir(parents=True,exist_ok=False)
            report={'passed':False,'model_loads':0,'runs':[]}
            try:
                started=time.perf_counter_ns();npu.load_model(package)
                report['model_load_ms']=(time.perf_counter_ns()-started)/1e6
                report['model_loads']=1
                for index,(path,frame) in enumerate(zip(args.inputs,frames)):
                    started=time.perf_counter_ns();result=npu.infer(frame)
                    elapsed=(time.perf_counter_ns()-started)/1e6
                    target=args.output/f'{index:04d}.bin';target.write_bytes(result)
                    report['runs'].append({'input':str(path),'output':target.name,'host_ms':elapsed})
                report['passed']=True
            finally:
                (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        else:
            if not args.package or not args.input or not args.output:p.error('infer requires --package --input --output')
            npu.load_model(Package.load(args.package));args.output.write_bytes(npu.infer(args.input.read_bytes()))
    finally:closer()
if __name__=='__main__':main()
