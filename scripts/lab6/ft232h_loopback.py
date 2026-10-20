"""FT232H MPSSE internal loopback check; run in WSL with OpenOCD stopped.

Uses libftdi1/ftdi.h constants. This verifies the USB/MPSSE path, not external
wires. TMS is held high, and loopback is disabled again before closing USB.
"""
import ctypes as C
import time
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--drive-directory', type=Path)
args = parser.parse_args()

lib = C.CDLL('libftdi1.so.2')
lib.ftdi_new.restype = C.c_void_p
lib.ftdi_free.argtypes = [C.c_void_p]
lib.ftdi_get_error_string.argtypes = [C.c_void_p]
lib.ftdi_get_error_string.restype = C.c_char_p
lib.ftdi_usb_open_desc.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_char_p, C.c_char_p]
for name in ('ftdi_usb_reset', 'ftdi_usb_purge_buffers', 'ftdi_usb_close'):
    getattr(lib, name).argtypes = [C.c_void_p]
lib.ftdi_set_bitmode.argtypes = [C.c_void_p, C.c_ubyte, C.c_ubyte]
lib.ftdi_set_latency_timer.argtypes = [C.c_void_p, C.c_ubyte]
lib.ftdi_write_data.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
lib.ftdi_read_data.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
ctx = lib.ftdi_new()
if not ctx:
    raise SystemExit('ftdi_new failed')
opened = False

def checked(result):
    if result < 0:
        raise RuntimeError(lib.ftdi_get_error_string(ctx).decode())
    return result

def write(data):
    buf = (C.c_ubyte * len(data)).from_buffer_copy(data)
    if checked(lib.ftdi_write_data(ctx, buf, len(data))) != len(data):
        raise RuntimeError('Short MPSSE write')

try:
    checked(lib.ftdi_usb_open_desc(ctx, 0x0403, 0x6014, None, b'210512180081'))
    opened = True
    checked(lib.ftdi_usb_reset(ctx))
    checked(lib.ftdi_set_bitmode(ctx, 0, 0))
    checked(lib.ftdi_set_bitmode(ctx, 0x1b, 2))
    checked(lib.ftdi_set_latency_timer(ctx, 2))
    time.sleep(0.05)
    checked(lib.ftdi_usb_purge_buffers(ctx))
    # No divide-by-5/adaptive/3-phase; 10 kHz divisor 2999. TMS=1, TCK=0.
    write(bytes([0x8a, 0x97, 0x8d, 0x86, 0xb7, 0x0b, 0x80, 0x18, 0x1b]))
    if args.drive_directory:
        directory = args.drive_directory
        request = directory / 'drive_request.txt'
        ack = directory / 'drive_ack.txt'
        previous = ''
        deadline = time.monotonic() + 180
        print('LAB6_FT232H_STATIC_DRIVE_READY', flush=True)
        while time.monotonic() < deadline:
            try:
                value = request.read_text().strip() if request.exists() else ''
            except OSError:
                time.sleep(0.02)
                continue
            if value == 'STOP':
                break
            if value and value != previous:
                seq, level = value.split(',')
                level = int(level, 0)
                if level & ~0x1b:
                    raise ValueError('Only configured TCK/TDI/TMS/nTRST outputs may be driven')
                write(bytes([0x80, level, 0x1b, 0x81, 0x87]))
                pin = (C.c_ubyte * 1)()
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    if checked(lib.ftdi_read_data(ctx, pin, 1)) == 1:
                        break
                else:
                    raise RuntimeError('GPIO read timed out')
                ack.write_text(f'{seq},{pin[0]}')
                print(f'DRIVE seq={seq} value={level:#04x} pins={pin[0]:#04x}', flush=True)
                previous = value
            time.sleep(0.02)
        print('LAB6_FT232H_STATIC_DRIVE_STOPPED', flush=True)
        raise SystemExit(0)
    write(bytes([0x84]))
    pattern = bytes([0x00, 0xff, 0x55, 0xaa, 0x81, 0x7e, 0x13, 0xc9] * 8)
    write(bytes([0x39, len(pattern)-1, 0]) + pattern + bytes([0x87]))
    received = bytearray()
    deadline = time.monotonic() + 5
    while len(received) < len(pattern) and time.monotonic() < deadline:
        buf = (C.c_ubyte * 512)()
        count = checked(lib.ftdi_read_data(ctx, buf, len(buf)))
        received.extend(bytes(buf[:count]))
    if bytes(received) != pattern:
        raise RuntimeError(f'Loopback mismatch: expected={pattern.hex()} actual={received.hex()}')
    print(f'LAB6_FT232H_INTERNAL_LOOPBACK=PASS bytes={len(pattern)}')
finally:
    if opened:
        try:
            write(bytes([0x85, 0x80, 0x18, 0x1b, 0x87]))
            lib.ftdi_set_bitmode(ctx, 0, 0)
        finally:
            lib.ftdi_usb_close(ctx)
    lib.ftdi_free(ctx)
