"""Static reviewed native registry. A manifest cannot select executable code.

Trust comes from this shipped core registry (and verified image), not from import
success or a plugin-supplied digest. No third-party adapter is executed here.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
from types import MappingProxyType
from .control import read_data


@dataclass(frozen=True)
class Implementation:
    identity: str
    source_sha256: str
    manifest_sha256: str

    def create(self):
        # Fixed import only, after checking the reviewed shipped implementation.
        from .native_observer import ResourceSummary
        return ResourceSummary()


REGISTRY = MappingProxyType({'resource-summary': Implementation(
    'redux.resource-summary/v1',
    'af2618f8b8b47ea1da33032f99c8dbe7ba3a5ff27a0c0ca1b71fbe3796bab250',
    'a883c2f404ad2036d459195392ba35bad41997e117a95e7b50eb46a90510884b')})


def resolve(pack):
    spec = REGISTRY.get(pack.name)
    if spec is None or pack.kind != 'plugin' or pack.adapter != 'redux':
        raise ValueError('no reviewed native implementation; tool packs remain manual')
    if pack.manifest_sha256 != spec.manifest_sha256:
        raise ValueError('manifest differs from reviewed registry')
    source = Path(__file__).with_name('native_observer.py')
    if source.is_symlink() or hashlib.sha256(read_data(source)).hexdigest() != spec.source_sha256:
        raise ValueError('implementation differs from reviewed registry')
    return spec


def eligibility(pack):
    try:
        spec = resolve(pack)
        return {'eligible': True, 'implementation': spec.identity,
                'source_sha256': spec.source_sha256, 'reason': 'reviewed registry match; runtime owner required'}
    except ValueError as error:
        return {'eligible': False, 'reason': str(error)}
    except OSError:
        return {'eligible': False, 'reason': 'shipped implementation unavailable'}
