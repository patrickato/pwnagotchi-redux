"""Private runtime configuration attestation; never publish kernel identities."""
import os
from pathlib import Path


def process_identity(pid, proc_root=Path('/proc')):
    if type(pid) is not int or pid <= 1:
        raise ValueError('invalid process identity')
    path = Path(proc_root)/str(pid)
    with (path/'stat').open() as stream:
        raw=stream.read(16385)
    if len(raw)>16384:raise ValueError('process identity oversized')
    fields = raw.rsplit(')', 1)[1].split()
    exe = (path/'exe').stat()
    return (pid, int(fields[19]), exe.st_dev, exe.st_ino)


def configuration_checkpoint(config, *, pid=None, proc_root=Path('/proc')):
    """Self-reported in-memory fields tied to boot and process generation.

    Read-only collectors independently corroborate the process. This is not a
    cryptographic signature or proof that a privileged adversary cannot forge it.
    Missing /proc information leaves the entire attestation unavailable.
    """
    try:
        identity = process_identity(os.getpid() if pid is None else pid, proc_root)
        with (Path(proc_root)/'sys/kernel/random/boot_id').open() as stream:
            boot=stream.read(129).strip()
        if not boot or len(boot)>128:raise ValueError('boot identity invalid')
        return dict(version=1, supervisor_pid=identity[0], start_ticks=identity[1], boot_id=boot,
                    preferred_wlan0=config.preferred_iface == 'wlan0',
                    auto_select=config.preferred_iface == '',
                    usb_lease_required=config.require_usb_lease,
                    allow_connected_capture=config.allow_connected_capture)
    except (OSError, ValueError, IndexError):
        return None
