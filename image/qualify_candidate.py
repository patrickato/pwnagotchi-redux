"""Consolidated artifact/embedded-runtime/update gate; physical success stays UNKNOWN."""
from __future__ import annotations
import hashlib
import lzma
import tarfile
from contextlib import ExitStack
from pathlib import Path
import re
import tempfile

import boot_payload
import root_payload
from bundle_audit import verify_bundle
from offline_payload import extract_image, ToolUnavailable, contract_paths
from update_safety import (check, report, read, safe_path, json_record,
                           inspect_configuration, snapshot_file, REVISION)
from verify_release import verify


def _identity(path):
    s=path.lstat()
    return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns


def _compare_snapshot(a,b,names):
    for name in sorted(names):
        left=safe_path(a,name,allow_leaf_link=True)
        right=safe_path(b,name,allow_leaf_link=True)
        if left.is_symlink() or right.is_symlink():
            if not (left.is_symlink() and right.is_symlink() and left.readlink()==right.readlink()):
                raise ValueError('bundle/image symlink contract differs: '+name)
            continue
        if left.exists()!=right.exists():
            raise ValueError('bundle/image runtime files differ: '+name)
        if not left.exists():
            continue
        def digest(p):
            with p.open('rb') as stream:
                return hashlib.file_digest(stream,'sha256').hexdigest()
        if digest(left)!=digest(right) or (left.stat().st_mode & 0o777)!=(right.stat().st_mode & 0o777):
            raise ValueError('bundle payload does not match the release image: '+name)


def qualify(image, *, revision, profile, country, trusted_sha256=None,
            bundle=None,keyring=None,version=None,minimum_bytes=512*1024*1024):
    stack=ExitStack()
    checks=[]
    evidence={}
    required=[]
    def passed(name,detail):
        checks.append(check(name,'PASS',detail)); required.append(name)
    try:
        if not isinstance(revision,str) or not REVISION.fullmatch(revision):
            raise ValueError('expected full source revision is required')
        if profile not in ('lite','ab'):
            raise ValueError('unsupported release profile')
        if not isinstance(country,str) or not re.fullmatch(r'[A-Z]{2}',country) or country in ('AA','ZZ'):
            raise ValueError('operator-selected build country is required')
        if profile=='lite' and (bundle is not None or keyring is not None or version is not None):
            raise ValueError('Lite images have no supported A/B update path; refusing update bundle arguments')
        image=Path(image).absolute()
        for name in (image.name,image.name+'.release.json',image.name+'.sha256'):
            safe_path(image.parent,name)
        identities={name:_identity(image.parent/name) for name in (image.name,image.name+'.release.json',image.name+'.sha256')}
        source=image
        transport=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='redux-release-source-')))
        # Tools never reopen an operator-controlled input. Hold a no-follow
        # descriptor and copy one immutable inspection snapshot into private tmp.
        image=snapshot_file(source,transport/source.name)
        for suffix in ('.release.json','.sha256'):
            (transport/(image.name+suffix)).write_bytes(read(source.parent,source.name+suffix,256*1024 if suffix=='.release.json' else 256))
        transferred=verify(image,expected_profile=profile,trusted_sha256=trusted_sha256,minimum_bytes=minimum_bytes)
        passed('Image integrity/layout','xz, exact bytes, primary/backup layout, filesystem labels and companion metadata verified')
        manifest=json_record(read(image.parent,image.name+'.release.json',256*1024))
        if manifest['revisions']['redux'] != revision:
            raise ValueError('release manifest source revision differs from requested revision')
        passed('Release source identity','manifest matches the selected source revision')
        if trusted_sha256 is None:
            checks.append(check('Independent artifact authenticity','UNKNOWN','supply an independently trusted compressed-image SHA256; sidecars are not signatures'))
        else:
            passed('Independent artifact authenticity','exact artifact matches operator-supplied trusted SHA256')
        # Reuse stream inspection for measured partition offsets and UUIDs.
        measured=transferred["image_metadata"]
        evidence['image']={k:transferred[k] for k in ('sha256','image_profile','disk_bytes')}
        with tempfile.TemporaryDirectory(prefix='redux-release-') as scratch_name:
            scratch=Path(scratch_name)
            roots,context=extract_image(image,measured,scratch)
            for root in roots:
                if read(root,'usr/share/redux/redux-revision',256).decode().strip()!=revision:
                    raise ValueError('embedded root revision contradicts release identity')
            kwargs={'profile':profile,'other_root':roots[1] if profile=='ab' else None}
            root_payload.verify(roots[0],expected_country=country,**kwargs)
            passed('Embedded runtime contract','actual image roots preserve immutable overlay, captures separation, passive radio policy, lease/PAN defaults and service contracts')
            boot_args={}
            if context is not None:
                boot_args={'a_root_partuuid':context['devices']['4'].rsplit('/',1)[-1],
                           'b_root_partuuid':context['devices']['5'].rsplit('/',1)[-1]}
            boot_payload.verify(roots[0],**kwargs,**boot_args)
            passed('Embedded boot contract','actual Pi 4/5 payloads, selector and paired root PARTUUIDs checked; no firmware executed')
            if profile=='ab':
                paired_required,paired_optional=contract_paths('ab')
                _compare_snapshot(roots[0],roots[1],paired_required|paired_optional)
                passed('Paired runtime consistency','selected immutable runtime/configuration files match across factory roots')
                configs=[inspect_configuration(root,revision=revision,context=context) for root in roots]
                evidence['rauc']=configs
                if any(any(c['status']=='FAIL' for c in item['checks']) for item in configs):
                    raise ValueError('embedded RAUC readiness failed: '+ '; '.join(c['detail'] for item in configs for c in item['checks'] if c['status']=='FAIL'))
                if any(item['condition']!='configured' for item in configs):
                    checks.append(check('RAUC readiness','UNKNOWN','embedded RAUC configuration could not be fully verified'))
                else:
                    passed('RAUC readiness','both actual roots use the supported generated custom-backend policy and public keyring')
                if bundle is None or keyring is None:
                    checks.append(check('Authenticated update bundle','UNKNOWN','supply both a complete verity bundle and independently trusted public keyring'))
                else:
                    keyring=Path(keyring).absolute()
                    trusted=read(keyring.parent,keyring.name)
                    if any(read(root,'etc/rauc/keyring.pem')!=trusted for root in roots):
                        raise ValueError('embedded update trust differs from supplied independent keyring')
                    trusted_copy=scratch/'trusted-update-keyring.pem'
                    trusted_copy.write_bytes(trusted)
                    signed=verify_bundle(Path(bundle).absolute(),trusted_copy,scratch,revision=revision,version=version)
                    required_paths,optional_paths=contract_paths('ab')
                    _compare_snapshot(roots[0],signed['root'],required_paths|optional_paths)
                    # Archive root modes matter; FAT payloads have no Unix mode semantics.
                    for name in boot_payload._KERNEL_AND_FIRMWARE+('config.txt','cmdline.txt'):
                        if read(roots[0]/'boot/firmware',name,64*1024*1024)!=read(signed['boot'],name,64*1024*1024):
                            raise ValueError('bundle boot payload differs from the release image: '+name)
                    passed('Authenticated update bundle','RAUC signature and complete verity payload, archive paths/digests, baked build and actual image runtime binding verified')
                    evidence['bundle']={k:v for k,v in signed.items() if k not in ('root','boot')}
            else:
                mask=safe_path(roots[0],'etc/systemd/system/rauc.service',allow_leaf_link=True)
                if not mask.is_symlink() or str(mask.readlink())!='/dev/null':
                    raise ValueError('Lite update service must remain masked')
                passed('Update safety','Lite update path explicitly disabled; no unsupported single-slot update advertised')
        for name,identity in identities.items():
            if _identity(source.parent/name)!=identity:
                raise ValueError('release artifact/companions changed during qualification')
    except (ToolUnavailable,ImportError) as exc:
        checks.append(check('Offline inspection capability','UNKNOWN',str(exc)))
    except (OSError,ValueError,KeyError,TypeError,UnicodeError,EOFError,tarfile.TarError,lzma.LZMAError) as exc:
        checks.append(check('Release candidate consistency','FAIL',str(exc)))
    finally:
        stack.close()
    offline_qualified=bool(required) and all(c['status']=='PASS' for c in checks)
    checks.append(check('Physical boot/update/recovery acceptance','UNKNOWN',
                        'Pi 4/5 firmware tryboot/watchdog, cold boot, failed trial, selector power loss and preserved captures are unverified'))
    return report(checks,offline_qualified=offline_qualified,evidence=evidence,
                  install_permitted=False,rollback_permitted=False)
