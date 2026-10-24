"""Check actual OpenOCD field encoding/decoding for the 72-bit fabric protocol."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from nocpu_jtag import JtagTransport


class Rpc:
    def __init__(self, reply):
        self.reply = reply
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return self.reply if command.startswith("drscan ") else ""


class JtagScanTests(unittest.TestCase):
    def test_packet_above_64_bits_uses_three_hex_fields(self):
        rpc = Rpc("89abcdef\n12345678\n80")
        transport = JtagTransport(rpc)
        result = transport.scan((0xab << 64) | (0x89abcdef << 32) | 0x01234567)
        self.assertEqual(rpc.commands[-1],
                         "drscan npu.tap 32 0x01234567 32 0x89abcdef 8 0xab -endstate IDLE")
        self.assertEqual(result, (0x80 << 64) | (0x12345678 << 32) | 0x89abcdef)

    def test_zero_poll_and_space_separated_reply(self):
        rpc = Rpc("00000000 00000000 00")
        self.assertEqual(JtagTransport(rpc).scan(0), 0)
        self.assertIn("32 0x00000000 32 0x00000000 8 0x00", rpc.commands[-1])

    def test_malformed_or_out_of_range_reply_is_rejected(self):
        for reply in ("error", "00000000 00000000", "00000000 00000000 100",
                      "00000000 100000000 00", "nothex 00000000 00", "00000000 00000000 -1"):
            with self.subTest(reply=reply):
                with self.assertRaisesRegex(RuntimeError, "OpenOCD drscan failed"):
                    JtagTransport(Rpc(reply)).scan(0)


if __name__ == "__main__":
    unittest.main()
