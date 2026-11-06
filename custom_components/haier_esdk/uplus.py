"""The `uplus` / KZW frame dialect, as spoken by `uplus-haier-ac-*` modules.

zheng59/haier-esdk-local targets first-generation eSDK modules whose status
frame is `ff ff 22 00 ...` with a 24-byte control block of big-endian words
and a byte-sum checksum. These modules answer `ff ff 2a 40 ...`, carry their
fields as single bytes at fixed offsets, use different opcodes for control,
and trailer their frames with an XOR byte plus a CRC16. Sending the upstream
frames to them gets every write refused with frame type 0x03 (INVALID).

Ground truth for this map comes from homebridge-haier-ac@0.2.1 (the patched
haier-ac-remote bundled with it), which drove these exact units under
HomeKit, cross-checked against its own log: for the frame

    ff ff 2a 40 00 00 00 00 00 06 6d 01 05 00 85 00 02 01 ...
                                     ^12   ^14   ^16^17      ^22

it reported room 22 C, setpoint 21 C, mode heat, fan auto, power on -- which
is what read_status() below returns for it.

Field layout (byte offsets into the frame, not words):

    12  setpoint, raw value = degrees C - 16
    14  high nibble = mode, low nibble = fan speed
    17  status bits: bit0 power, bit1 purify ("health")
    22  room temperature in HALF degrees, so value / 2

Opcodes differ too: status poll is 4d01 as upstream, but control is 6001
(upstream 4d5f) and power-off has its own frame starting 5d01 (upstream
4d03).

Trailer: after the length-counted body (bytes[2:2+L]) comes one XOR byte over
that body, then CRC16 (poly 0xa001, init 0) over the same span, high byte
first. Verified against the two literal frames the plugin ships, whose CRC16
bytes this reproduces exactly.
"""
from __future__ import annotations

POLY = 0xA001

# Byte offsets inside the frame.
SET_POINT_OFFSET = 12
MODE_OFFSET = 14
STATUS_DATA_OFFSET = 17
TEMPERATURE_OFFSET = 22

MODE_MSK = 0xF0
FAN_MSK = 0x0F
POWER_BIT = 0
PURIFY_BIT = 1

# Wire encodings. Names match the upstream integration's vocabulary so the
# climate entity needs no translation table of its own.
MODE_WIRE = {"auto": 0x00, "cool": 0x10, "dry": 0x40, "heat": 0x80, "fan_only": 0xC0}
FAN_WIRE = {"high": 0x01, "medium": 0x02, "low": 0x03, "auto": 0x05}
MODE_NAME = {v: k for k, v in MODE_WIRE.items()}
FAN_NAME = {v: k for k, v in FAN_WIRE.items()}

# Literal frames from the working plugin, trailers included.
FRAME_POLL = bytes.fromhex("ffff0a400000000000014d0199b3b4")
FRAME_POWER_OFF = bytes.fromhex("ffff0c400000000000015d010000ab7d3a")

# Control template; field bytes get overwritten and the trailer recomputed.
CONTROL_TEMPLATE = bytes([
    0xFF, 0xFF, 0x14, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x60, 0x01,
    0x09, 0x08, 0x25, 0x00, 0x02, 0x03, 0x00, 0x06, 0x00, 0x0C, 0x03, 0x0B,
    0x70,
])

STATUS_OPCODE = 0x6D01
# Byte 3 of every frame in this dialect. Upstream's gen-1 frames carry 0x00
# there, so this tells the two apart even though both use opcode 6d01.
HEADER_MARKER = 0x40


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc >> 1) ^ POLY) if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def add_trailer(body: bytes) -> bytes:
    """Re-trailer a frame: XOR byte, then CRC16 high/low, over bytes[2:2+L]."""
    end = 2 + body[2]
    core = body[:end]
    span = core[2:end]
    xor = 0
    for byte in span:
        xor ^= byte
    checksum = crc16(span)
    return core + bytes([xor & 0xFF, (checksum >> 8) & 0xFF, checksum & 0xFF])


def is_status_frame(frame: bytes) -> bool:
    """A status frame in THIS dialect, long enough to carry every field.

    The opcode alone cannot tell the dialects apart: upstream's gen-1 frames
    answer 6d01 too, but theirs are `ff ff 22 00` with word-aligned fields.
    Reading one of those with the byte offsets above yields silent nonsense
    (humidity 133%, mode 45), so the header byte is part of the test -- a
    frame from another dialect must be refused, not misread.
    """
    return (len(frame) > TEMPERATURE_OFFSET
            and frame[:2] == b"\xff\xff"
            and frame[3] == HEADER_MARKER
            and int.from_bytes(frame[10:12], "big") == STATUS_OPCODE)


def read_status(frame: bytes) -> dict | None:
    """Decode a status frame into the keys the integration already uses."""
    if not is_status_frame(frame):
        return None
    mode_fan = frame[MODE_OFFSET]
    bits = frame[STATUS_DATA_OFFSET]
    return {
        "power": bool(bits & (1 << POWER_BIT)),
        "health": bool(bits & (1 << PURIFY_BIT)),
        "mode": MODE_NAME.get(mode_fan & MODE_MSK, "auto"),
        "fan": FAN_NAME.get(mode_fan & FAN_MSK, "auto"),
        "target_temperature": float(frame[SET_POINT_OFFSET] + 16),
        # Half-degree resolution: the only field here that is not a plain byte.
        "room_temperature": frame[TEMPERATURE_OFFSET] / 2.0,
    }


def build_control(*, mode: str, fan: str, target_temperature: float,
                  power: bool, health: bool = False) -> bytes:
    """Build a 6001 control frame carrying the whole desired state."""
    cmd = bytearray(CONTROL_TEMPLATE)
    cmd[MODE_OFFSET] &= (~MODE_MSK) & 0xFF
    cmd[MODE_OFFSET] |= MODE_WIRE.get(mode, 0x00)
    cmd[MODE_OFFSET] &= (~FAN_MSK) & 0xFF
    cmd[MODE_OFFSET] |= FAN_WIRE.get(fan, 0x05)
    degrees = max(16, min(30, int(round(target_temperature))))
    cmd[SET_POINT_OFFSET] = degrees - 16
    for bit, on in ((POWER_BIT, power), (PURIFY_BIT, health)):
        mask = 1 << bit
        if on:
            cmd[STATUS_DATA_OFFSET] |= mask
        else:
            cmd[STATUS_DATA_OFFSET] &= (~mask) & 0xFF
    return add_trailer(bytes(cmd))


def build_power_off() -> bytes:
    return FRAME_POWER_OFF


def build_poll() -> bytes:
    return FRAME_POLL
