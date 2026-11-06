"""The health switch on a uplus board: read it right, and write it at all.

Both faults measured 06-11-2026 on a unit that had health ON:
  - HA showed "off", because switch.is_on reads byte 17 bit 3 (the gen-1
    position) while this dialect keeps health in bit 1;
  - turning it on raised ValueError "control block must be 24 bytes, got
    47", because copy() dropped the dialect and then re-validated a whole
    frame as if it were a 24-byte control block.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import component_loader  # noqa: F401,E402

from haier_esdk import uplus  # noqa: E402
from haier_esdk.protocol import HaierStatus, parse_epp  # noqa: E402

# Captured with health ON: byte 17 = 0x03 (power bit0 + health bit1).
AAN = "ffff2a400000000000066d010508850002030006000c2a004300000300000000000000000000000000000000f779f6"
GEN1 = "ffff22000000000001066d01001b003a000300000000000100020000000900000000000b06"


def status(hex_frame):
    return HaierStatus.from_frame(parse_epp(bytes.fromhex(hex_frame)))


class TestHealthOnUplus(unittest.TestCase):
    def setUp(self):
        self.st = status(AAN)

    def test_health_is_read_from_the_right_bit(self):
        self.assertEqual(self.st.dialect, "uplus")
        self.assertTrue(self.st.power)
        self.assertTrue(self.st.health)

    def test_copy_keeps_the_dialect(self):
        """copy() losing the dialect is what raised the 24-byte ValueError."""
        self.assertEqual(self.st.copy().dialect, "uplus")

    def test_turning_health_off_keeps_everything_else(self):
        uit = self.st.with_bits(17, 0x08, False)   # gen-1 coordinates in
        self.assertEqual(uit.dialect, "uplus")
        self.assertFalse(uit.health)
        self.assertTrue(uit.power)
        self.assertEqual(uit.mode, "heat")
        self.assertEqual(uit.target_temperature, 21.0)

    def test_turning_health_on_yields_a_sendable_frame(self):
        aan = self.st.with_health(True)
        self.assertTrue(aan.health)
        self.assertEqual(len(aan.raw), 2 + aan.raw[2] + 3)

    def test_unsupported_bit_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            self.st.with_bits(19, 0x20, True)      # display off: not here


class TestGen1Unchanged(unittest.TestCase):
    def test_old_dialect_still_works(self):
        g = status(GEN1)
        self.assertEqual(g.dialect, "esdk")
        self.assertEqual(g.copy().dialect, "esdk")
        self.assertTrue(g.with_bits(17, 0x08, True).health)


if __name__ == "__main__":
    unittest.main()
