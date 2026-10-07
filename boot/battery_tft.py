"""Draw measured battery state in a reserved framebuffer strip, monochrome safe."""
import ctypes
import fcntl
import os
import struct


class Fixed(ctypes.Structure):
    # Linux fb_fix_screeninfo ABI; c_ulong follows the running arm64 userspace.
    _fields_ = [("id", ctypes.c_char * 16), ("smem_start", ctypes.c_ulong),
                ("smem_len", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("type_aux", ctypes.c_uint32), ("visual", ctypes.c_uint32),
                ("xpanstep", ctypes.c_uint16), ("ypanstep", ctypes.c_uint16),
                ("ywrapstep", ctypes.c_uint16), ("line_length", ctypes.c_uint32),
                ("mmio_start", ctypes.c_ulong), ("mmio_len", ctypes.c_uint32),
                ("accel", ctypes.c_uint32), ("capabilities", ctypes.c_uint16),
                ("reserved", ctypes.c_uint16 * 2)]


def panel(reading):
    from PIL import Image, ImageDraw
    canvas = Image.new("1", (160, 48), 0)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((2, 4, 42, 22), outline=1)
    draw.rectangle((43, 9, 46, 17), fill=1)
    if reading.percent is not None:
        width = int(36 * reading.percent / 100)
        if width:
            draw.rectangle((4, 6, 3 + width, 20), fill=1)
        caption = f"{reading.percent:.0f}%"
    else:
        caption = "Unknown"
        draw.text((15, 7), "?", fill=1)
    draw.text((52, 5), caption, fill=1)
    draw.text((52, 18), f"{reading.voltage_v:.2f}V" if reading.voltage_v is not None else "Voltage unknown", fill=1)
    draw.text((2, 34), reading.status, fill=1)
    return canvas


def rows(canvas, variable, fixed):
    xres, yres, xv, yv, xo, yo, bits = variable[:7]
    if (xres, yres) not in {(480, 320), (320, 480)} or bits not in {16, 32}:
        raise ValueError("TFT must expose 480x320/320x480, 16/32-bit truecolor")
    if variable[7] or variable[20] or fixed.type != 0 or fixed.visual != 2:
        raise ValueError("unsupported grayscale, nonstandard or non-truecolor framebuffer")
    white = 0
    occupied = 0
    for offset, length, msb_right in (variable[8:11], variable[11:14], variable[14:17]):
        if msb_right or not 0 < length <= 8 or offset + length > bits:
            raise ValueError("invalid framebuffer color bitfield")
        mask = ((1 << length) - 1) << offset
        if occupied & mask:
            raise ValueError("overlapping color bitfields")
        occupied |= mask
        white |= mask
    alpha_offset, alpha_length, alpha_right = variable[17:20]
    if alpha_length:
        if alpha_right or alpha_offset + alpha_length > bits:
            raise ValueError("invalid alpha field")
        white |= ((1 << alpha_length) - 1) << alpha_offset
    size = bits // 8
    if xo + xres > xv or yo + yres > yv or fixed.line_length < xv * size:
        raise ValueError("framebuffer stride/offset cannot contain visible screen")
    result = []
    for y in range(canvas.height):
        offset = (yo + y) * fixed.line_length + xo * size
        data = b"".join((white if canvas.getpixel((x, y)) else 0).to_bytes(size, "little") for x in range(canvas.width))
        if offset + len(data) > fixed.smem_len:
            raise ValueError("battery strip exceeds framebuffer memory")
        result.append((offset, data))
    return result


def show(reading, device):
    with open(device, "r+b", buffering=0) as frame:
        variable = bytearray(160)
        fixed_data = bytearray(ctypes.sizeof(Fixed))
        fcntl.ioctl(frame, 0x4600, variable, True)  # FBIOGET_VSCREENINFO
        fcntl.ioctl(frame, 0x4602, fixed_data, True)  # FBIOGET_FSCREENINFO
        for offset, data in rows(panel(reading), struct.unpack("40I", variable), Fixed.from_buffer_copy(fixed_data)):
            if os.pwrite(frame.fileno(), data, offset) != len(data):
                raise OSError("short framebuffer write")
