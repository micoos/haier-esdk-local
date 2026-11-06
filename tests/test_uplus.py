"""Unit tests for the uplus/KZW dialect (offline, no hardware).

Run: python3 -m unittest discover -s tests -v

The frames below were captured off two real units; the expectations come from
homebridge-haier-ac@0.2.1, which drove those units and logged what it decoded
(room 22 C, setpoint 21 C, heat, fan auto, power on for the first frame). If
these pass, the dialect map agrees with code that demonstrably worked.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import component_loader  # noqa: F401,E402

from haier_esdk import uplus  # noqa: E402
from haier_esdk.protocol import HaierStatus, parse_epp  # noqa: E402

# Captured whole (47 bytes: ff ff + L body + 3-byte trailer). Kantoor unit,
# powered on, heating, setpoint 21, fan auto, room 18.0.
KANTOOR = bytes.fromhex("ffff2a400000000000066d010508850002010006000024004400000300000000000000000000000000000000e45286")
# Slaapkamer unit, off, setpoint 19, fan low, room 12.5.
SLAAPKAMER = bytes.fromhex("ffff2a400000000000066d010300830002000000000019004400000100000000000000000000000000000000c4e58f")
# A first-generation frame, to prove the old path still works.
GEN1 = bytes.fromhex(
    "ffff22000000000001066d01001b003a000300000000000100020000000900000000000b06"
)


class TestUplusDecode(unittest.TestCase):
    def test_kantoor_matches_what_homebridge_reported(self):
        st = uplus.read_status(KANTOOR)
        self.assertIsNotNone(st)
        self.assertEqual(st["room_temperature"], 18.0)
        self.assertEqual(st["target_temperature"], 21.0)
        self.assertEqual(st["mode"], "heat")
        self.assertEqual(st["fan"], "auto")
        self.assertTrue(st["power"])
        self.assertFalse(st["health"])

    def test_slaapkamer_reads_as_off(self):
        st = uplus.read_status(SLAAPKAMER)
        self.assertFalse(st["power"])
        self.assertEqual(st["target_temperature"], 19.0)
        # Half-degree resolution: the room really was 12.5, not 25.
        self.assertEqual(st["room_temperature"], 12.5)
        self.assertEqual(st["fan"], "low")

    def test_other_dialect_is_refused_not_misread(self):
        """A gen-1 frame must not be decoded with these byte offsets.

        Both dialects answer subcommand 6d01, so the opcode alone cannot tell
        them apart; reading a gen-1 frame here yielded humidity 133% and mode
        45 before the header byte became part of the test.
        """
        self.assertIsNone(uplus.read_status(GEN1))


class TestUplusTrailer(unittest.TestCase):
    def test_crc16_reproduces_the_shipped_frames(self):
        """The literal frames carry checksums computed by working code."""
        for frame in (uplus.FRAME_POLL, uplus.FRAME_POWER_OFF):
            end = 2 + frame[2]
            self.assertEqual(uplus.crc16(frame[2:end]),
                             (frame[end + 1] << 8) | frame[end + 2])

    def test_add_trailer_length(self):
        body = uplus.build_control(mode="heat", fan="auto",
                                   target_temperature=22, power=True)
        self.assertEqual(len(body), 2 + body[2] + 3)


class TestUplusControl(unittest.TestCase):
    def test_fields_land_where_the_reader_expects_them(self):
        cmd = uplus.build_control(mode="cool", fan="low",
                                  target_temperature=24, power=True,
                                  health=True)
        self.assertEqual(cmd[uplus.SET_POINT_OFFSET] + 16, 24)
        self.assertEqual(uplus.MODE_NAME[cmd[uplus.MODE_OFFSET] & uplus.MODE_MSK], "cool")
        self.assertEqual(uplus.FAN_NAME[cmd[uplus.MODE_OFFSET] & uplus.FAN_MSK], "low")
        self.assertTrue(cmd[uplus.STATUS_DATA_OFFSET] & 0x01)
        self.assertTrue(cmd[uplus.STATUS_DATA_OFFSET] & 0x02)

    def test_setpoint_is_clamped(self):
        low = uplus.build_control(mode="heat", fan="auto",
                                  target_temperature=5, power=True)
        high = uplus.build_control(mode="heat", fan="auto",
                                   target_temperature=40, power=True)
        self.assertEqual(low[uplus.SET_POINT_OFFSET] + 16, 16)
        self.assertEqual(high[uplus.SET_POINT_OFFSET] + 16, 30)

    def test_matches_the_frame_the_device_accepted(self):
        """Byte-for-byte the command a real unit took during the live test."""
        cmd = uplus.build_control(mode="heat", fan="auto",
                                  target_temperature=22, power=True,
                                  health=False)
        self.assertEqual(cmd.hex(),
                         "ffff144000000000000160010608850002010006000cb68333")


class TestDialectRouting(unittest.TestCase):
    def test_parse_epp_keeps_the_trailer_and_checks_crc(self):
        frame = parse_epp(KANTOOR)
        self.assertIsNotNone(frame)
        self.assertTrue(frame.checksum_ok)
        self.assertGreaterEqual(len(frame.raw), 2 + frame.raw[2] + 3)

    def test_status_picks_the_uplus_dialect(self):
        st = HaierStatus.from_frame(parse_epp(KANTOOR))
        self.assertEqual(st.dialect, "uplus")
        self.assertTrue(st.power)
        self.assertEqual(st.mode, "heat")
        self.assertEqual(st.room_temperature, 18.0)
        self.assertEqual(st.target_temperature, 21.0)
        # Not carried in this dialect; 0 beats inventing a measurement.
        self.assertEqual(st.room_humidity, 0)

    def test_gen1_path_unchanged(self):
        st = HaierStatus.from_frame(parse_epp(GEN1))
        self.assertEqual(st.dialect, "esdk")
        self.assertEqual(st.room_temperature, 27.0)
        self.assertEqual(st.target_temperature, 27.0)
        self.assertEqual(st.mode, "cool")
        self.assertEqual(st.room_humidity, 58)


if __name__ == "__main__":
    unittest.main()
