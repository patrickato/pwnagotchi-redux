"""Bounded passive header validation, never identifiers/payload in results.

Conservative radiotap v0 subset (fields 0..21). Unknown namespaces/fields and
802.11 extensions are unsupported, never counted as decoded reception. This is
header evidence, not a handshake, payload decoder or independent RF provenance.
"""
import struct
import zlib

# alignment, size: radiotap specification, offsets relative to header start.
_FIELDS = ((8,8),(1,1),(1,1),(2,4),(2,2),(1,1),(1,1),(2,2),(2,2),(2,2),
           (1,1),(1,1),(1,1),(1,1),(2,2),(2,2),(1,1),(1,1),(4,8),(1,3),(4,8),(2,12))


def validate_header(packet, arphrd):
    """Return fixed classification only; no byte/string derived from addresses."""
    if not isinstance(packet, bytes) or not 1 <= len(packet) <= 8192:
        return 'invalid_size'
    flags, offset = 0, 0
    if arphrd == 803:
        if len(packet) < 8 or packet[0] != 0:
            return 'invalid_radiotap'
        length, present = struct.unpack_from('<HI', packet, 2)
        if not 8 <= length <= min(512, len(packet)):
            return 'invalid_radiotap'
        bitmap, cursor = present, 8
        for _ in range(3):
            if not bitmap & (1 << 31):
                break
            if cursor + 4 > length:
                return 'invalid_radiotap'
            bitmap = struct.unpack_from('<I', packet, cursor)[0]
            cursor += 4
            if bitmap & 0x7fffffff:
                return 'unsupported_radiotap'
        if bitmap & (1 << 31):
            return 'unsupported_radiotap'
        if present & 0x7fc00000:  # no field>=22, vendor namespace or reset
            return 'unsupported_radiotap'
        for bit, (alignment, size) in enumerate(_FIELDS):
            if not present & (1 << bit):
                continue
            cursor = (cursor + alignment - 1) & -alignment
            if cursor + size > length:
                return 'invalid_radiotap'
            if bit == 1:
                flags = packet[cursor]
            if bit == 14 and struct.unpack_from('<H', packet, cursor)[0] & 2:
                return 'bad_phy'
            if bit == 15:
                return 'transmit_metadata'
            cursor += size
        if flags & 0x40:
            return 'bad_fcs'
        offset = length
    elif arphrd != 801:
        return 'wrong_link_type'
    frame = packet[offset:]
    if flags & 0x10:
        if len(frame) < 4 or (zlib.crc32(frame[:-4]) & 0xffffffff) != int.from_bytes(frame[-4:], 'little'):
            return 'bad_fcs'
        frame = frame[:-4]
    if len(frame) < 2:
        return 'invalid_80211'
    fc = int.from_bytes(frame[:2], 'little')
    if fc & 3:
        return 'invalid_80211'
    kind, subtype = (fc >> 2) & 3, (fc >> 4) & 15
    ds, order = (fc >> 8) & 3, bool(fc & 0x8000)
    if kind == 0:  # management, optional HT control with Order bit
        if ds or subtype not in (0,1,2,3,4,5,6,8,9,10,11,12,13,14):
            return 'invalid_80211'
        needed = 24 + (4 if order else 0)
        label = 'management'
    elif kind == 1:
        # BAR/BA/trigger/extension forms have variable layouts: fail closed.
        if ds or order or subtype not in (10,11,12,13,14,15):
            return 'unsupported_80211'
        needed = 10 if subtype in (12,13) else 16
        label = 'control'
    elif kind == 2:
        if subtype in (13,):
            return 'unsupported_80211'
        qos = bool(subtype & 8)
        needed = 24 + (6 if ds == 3 else 0) + (2 if qos else 0) + (4 if qos and order else 0)
        if len(frame) >= 24 + (6 if ds == 3 else 0) + 2 and qos:
            qos_offset = 24 + (6 if ds == 3 else 0)
            # Mesh-control layout is deliberately outside this bounded parser.
            if frame[qos_offset+1] & 1:
                return 'unsupported_80211'
        label = 'data'
    else:
        return 'unsupported_80211'
    if len(frame) < needed:
        return 'invalid_80211'
    return label
