"""Desktop preview of the *actual* Redux TFT renderer; no Pi access required.

Defaults to honest OFFLINE/UNKNOWN telemetry. An explicit bounded local status
snapshot or current loopback API may be selected. Never enables a framebuffer,
input device, RF service, or graphical session.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import stat

from . import fbui

from .gesture_nav import PAGES


def _snapshot(path: str) -> dict:
    """Read one existing JSON status snapshot, not captures or device files."""
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError("status snapshot needs an absolute path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 2 <= info.st_size <= fbui.MAX_STATUS:
            raise ValueError("invalid status snapshot type or size")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(fbui.MAX_STATUS + 1)
    finally:
        os.close(fd)
    if len(raw) > fbui.MAX_STATUS:
        raise ValueError("status snapshot too large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid status JSON") from exc
    if (not isinstance(result, dict)
            or not isinstance(result.get("runtime"), dict)
            or not isinstance(result.get("doctor"), dict)):
        raise ValueError("not a Redux live status snapshot")
    return result


def _png(image) -> bytes:
    if image.size != fbui.RESOLUTION:
        raise ValueError("unsupported preview resolution")
    out = BytesIO()
    image.save(out, format="PNG")
    blob = out.getvalue()
    if len(blob) > 2_000_000:
        raise ValueError("preview exceeds size limit")
    return blob


def publish(directory: str, images: dict[str, bytes]) -> list[str]:
    """Exclusive, private, atomic no-overwrite PNG files in an existing dir.

    A failed multi-page batch removes only files this call successfully linked.
    Source images are encoded before creating anything in the output directory.
    """
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise ValueError("absolute output directory required")
    if not images or not set(images).issubset(PAGES):
        raise ValueError("unrecognized preview pages")
    for name, data in images.items():
        if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG data")
        if len(data) > 2_000_000:
            raise ValueError("preview exceeds size limit")
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    published = []
    try:
        if not stat.S_ISDIR(os.fstat(dirfd).st_mode):
            raise ValueError("output is not a directory")
        for name, data in images.items():
            target = f"redux-{name}.png"
            temporary = f".redux-preview-{secrets.token_hex(12)}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=dirfd)
            try:
                with os.fdopen(fd, "wb", closefd=True) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target, src_dir_fd=dirfd, dst_dir_fd=dirfd,
                        follow_symlinks=False)
                published.append(target)
            finally:
                os.unlink(temporary, dir_fd=dirfd)
        os.fsync(dirfd)
        return published
    except BaseException:
        for name in published:
            os.unlink(name, dir_fd=dirfd)
        raise
    finally:
        os.close(dirfd)


def build(*, report=None, pages=PAGES, rotate=0, doctor_offset=0) -> dict[str, bytes]:
    """Render exactly the same source pixels as the actual framebuffer path."""
    if rotate not in (0, 180):
        raise ValueError("rotation must be 0 or 180")
    if type(doctor_offset) is not int or not 0 <= doctor_offset <= 61:
        raise ValueError("invalid bounded Doctor scroll offset")
    if not pages or len(set(pages)) != len(pages) or not set(pages).issubset(PAGES):
        raise ValueError("unsupported pages")
    output = {}
    for page in pages:
        img = (fbui.render(report, page=page, doctor_offset=doctor_offset)
               if page == "doctor" else fbui.render(report, page=page))
        if rotate == 180:
            from PIL import Image
            img = img.transpose(Image.Transpose.ROTATE_180)
        output[page] = _png(img)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render real Redux 480x320 UI as private PNGs")
    parser.add_argument("--output-dir", required=True, help="existing absolute destination directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--status-json", help="absolute file containing measured Redux status JSON")
    source.add_argument("--acceptance-json",
                        help="sanitized read-only acceptance JSON; historical Radios page only")
    source.add_argument("--live", action="store_true", help="read current loopback Redux API only")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--page", choices=PAGES, help="one page; default: all available pages")
    parser.add_argument("--doctor-offset", type=int, default=0,
                        help="preview a scrolled Doctor finding window (0-61)")
    parser.add_argument("--rotate", type=int, choices=(0, 180), default=0)
    args = parser.parse_args(argv)
    if args.port < 1 or args.port > 65535:
        parser.error("invalid port")
    if args.doctor_offset and args.page != "doctor":
        parser.error("--doctor-offset requires --page doctor")
    if not 0 <= args.doctor_offset <= 61:
        parser.error("--doctor-offset must be 0-61")
    if args.acceptance_json and args.page not in (None, "radios"):
        parser.error("historical acceptance can preview only the Radios page")
    try:
        if args.acceptance_json:
            from .acceptance_snapshot import load as read_acceptance
            report = {"radio_acceptance_snapshot": read_acceptance(args.acceptance_json)}
            source_name = "historical_readonly_acceptance"
        elif args.status_json:
            report = _snapshot(args.status_json)
            source_name = "saved_status"
        elif args.live:
            report = fbui.read_status(args.port)
            if report is None:
                raise ValueError("live loopback status unavailable")
            source_name = "live_loopback"
        else:
            report = None
            source_name = "offline_unverified"
        selected_pages = ((args.page,) if args.page else
                          ("radios",) if args.acceptance_json else PAGES)
        names = publish(args.output_dir, build(report=report,
                                              pages=selected_pages,
                                              rotate=args.rotate,
                                              doctor_offset=args.doctor_offset))
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f"preview refused: {type(exc).__name__}")
    print(json.dumps({"source": source_name, "files": names, "resolution": [480, 320],
                      "physical_display_verified": False, "touch_verified": False},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
