"""All frames synthetic; no RF, MAC identifiers or payload capture."""
import random
import struct
import zlib
import pytest
from redux.radio.frame_headers import validate_header


def frame(fc=0x80,size=24):
    return fc.to_bytes(2,'little')+b'\0'*(size-2)


def rt(payload, flags=None):
    return struct.pack('<BBHI',0,0,8 if flags is None else 9,0 if flags is None else 2)+(bytes([flags]) if flags is not None else b'')+payload


@pytest.mark.parametrize('fc,size,label',[(0x80,24,'management'),(0x0088,26,'data'),
    (0x8388,36,'data'),(0x0008,24,'data'),(0x00d4,10,'control'),(0x00b4,16,'control'),
    (0x8080,28,'management'),(0x8008,24,'data')])
@pytest.mark.parametrize('link',[801,803])
def test_valid_header_forms(fc,size,label,link):
    data=frame(fc,size)
    assert validate_header(rt(data) if link==803 else data,link)==label
    assert validate_header(rt(data[:-1]) if link==803 else data[:-1],link)=='invalid_80211'


@pytest.mark.parametrize('data,label',[(b'\0'*7,'invalid_radiotap'),
    (struct.pack('<BBHI',1,0,8,0),'invalid_radiotap'),
    (struct.pack('<BBHI',0,0,7,0),'invalid_radiotap'),
    (struct.pack('<BBHI',0,0,200,0),'invalid_radiotap'),
    (struct.pack('<BBHI',0,0,8,1<<31),'invalid_radiotap'),
    (struct.pack('<BBHI',0,0,8,1<<22),'unsupported_radiotap'),
    (struct.pack('<BBHII',0,0,12,1<<31,2),'unsupported_radiotap'),
    (struct.pack('<BBHI',0,0,8,1),'invalid_radiotap')])
def test_invalid_or_unsupported_radiotap_never_counts(data,label):
    assert validate_header(data,803)==label


def test_tsft_flags_alignment_and_extended_zero_bitmap():
    header=struct.pack('<BBHII',0,0,25,(1<<31)|3,0)+b'\0'*4+b'\0'*8+b'\0'
    assert validate_header(header+frame(),803)=='management'
    assert validate_header(header[:24]+frame(),803)!='management'


def test_fcs_present_is_validated_not_assumed():
    data=frame();crc=(zlib.crc32(data)&0xffffffff).to_bytes(4,'little')
    assert validate_header(rt(data+crc,0x10),803)=='management'
    assert validate_header(rt(data+b'\0'*4,0x10),803)=='bad_fcs'
    assert validate_header(rt(data+crc,0x50),803)=='bad_fcs'
    assert validate_header(rt(data,0x40),803)=='bad_fcs'


def test_transmit_and_bad_phy_metadata_never_counts():
    assert validate_header(struct.pack('<BBHIH',0,0,10,1<<15,0)+frame(),803)=='transmit_metadata'
    assert validate_header(struct.pack('<BBHIH',0,0,10,1<<14,2)+frame(),803)=='bad_phy'


@pytest.mark.parametrize('fc',[1,0x0380,0x0084,0x0094,0x00d8,0x000c])
def test_invalid_reserved_and_variable_control_frames_do_not_pass(fc):
    assert validate_header(rt(frame(fc,64)),803) not in ('management','control','data')


def test_random_inputs_never_raise_or_expose_bytes():
    rng=random.Random(20261017)
    allowed={'management','control','data','invalid_size','invalid_radiotap',
             'unsupported_radiotap','bad_fcs','bad_phy','transmit_metadata',
             'wrong_link_type','invalid_80211','unsupported_80211'}
    for _ in range(3000):
        data=rng.randbytes(rng.randrange(0,1000))
        assert validate_header(data,rng.choice([1,801,803])) in allowed
    assert validate_header(b'x'*8193,803)=='invalid_size'
