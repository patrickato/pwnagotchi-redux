"""Host-only candidate transactions around the existing builder and verifiers.

No sudo, installation, flashing, SSH or host boot configuration. Failed attempts
are retained; only a final immutable receipt marks a completed factory run.
"""
from __future__ import annotations
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time

from host_preflight import report as host_report
from qualify_candidate import qualify
from update_safety import read, safe_path, snapshot_file, json_record, public_keyring
from verify_release import verify as verify_image
from offline_payload import run as parser_run, ToolUnavailable

REV = re.compile(r'[0-9a-f]{40}')
HASH = re.compile(r'[0-9a-f]{64}')
VERSION = re.compile(r'[A-Za-z0-9._+-]{1,128}')
TOOLS = ('git', 'bash', 'python3', 'debugfs', 'mtype')
AB_TOOLS = ('openssl', 'rauc', 'mksquashfs', 'unsquashfs', 'losetup', 'mount', 'umount', 'tar', 'blkid', 'mountpoint')


def path(value):
    p = Path(value).absolute()
    safe_path(p.parent, p.name)
    if p == Path('/') or any(c.isspace() for c in str(p)):
        raise ValueError('choose a non-root path without whitespace')
    return p


def git(root, *args):
    value = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=20, check=False)
    if value.returncode:
        raise ValueError('Git source check failed')
    return value.stdout.decode('utf-8', errors='strict').strip()


def source(root, revision):
    if not isinstance(revision, str) or not REV.fullmatch(revision):
        raise ValueError('a full lowercase source revision is required')
    if git(root, 'rev-parse', 'HEAD') != revision:
        raise ValueError('selected revision differs from source HEAD')
    if git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('source worktree must be clean, including untracked files')
    return {'revision': revision, 'tree': git(root, 'rev-parse', 'HEAD^{tree}'),
            'source_date_epoch': int(git(root, 'show', '-s', '--format=%ct', revision))}


def pins(root):
    text = read(root, 'image/sources.sh').decode()
    names = ('PI_GEN_URL', 'PI_GEN_REV', 'NEXMON_URL', 'NEXMON_REV')
    result = {}
    for name in names:
        matches = re.findall(r'^' + name + r'=([^\s]+)$', text, re.M)
        if len(matches) != 1:
            raise ValueError('missing/ambiguous pinned source: ' + name)
        value = matches[0]
        if (name.endswith('_REV') and not REV.fullmatch(value)) or (name.endswith('_URL') and not re.fullmatch(r'https://[A-Za-z0-9./_-]+\.git', value)):
            raise ValueError('invalid immutable upstream input: ' + name)
        result[name] = value
    return result


def signing(cert, key, keyring, root):
    """External PEM files only. Never copy or return private key bytes/paths."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    locations = [path(item) for item in (cert, key, keyring)]
    for location in locations:
        if location == root or root in location.parents:
            raise ValueError('signing credentials/trust must be external to the source checkout')
    cert_raw, key_raw, trust = [read(item.parent, item.name, 1024 * 1024) for item in locations]
    public_keyring(cert_raw)
    trust_digest = public_keyring(trust)
    if locations[1].stat().st_mode & 0o077:
        raise ValueError('external private signing key must have private permissions (0600 or stricter)')
    private = serialization.load_pem_private_key(key_raw, password=None)
    certificate = x509.load_pem_x509_certificate(cert_raw)
    encode = lambda obj: obj.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if encode(private.public_key()) != encode(certificate.public_key()):
        raise ValueError('signing certificate and private key do not match')
    return {'certificate_sha256': hashlib.sha256(cert_raw).hexdigest(), 'keyring_sha256': trust_digest}


def preflight(root, *, revision, profile, country, workspace, output,
              cert=None, key=None, keyring=None, version=None, captures_mib=512):
    checks = []
    data = {}
    try:
        root, workspace, output = path(root), path(workspace), path(output)
        if profile not in ('lite', 'ab') or not re.fullmatch(r'[A-Z]{2}', country or '') or country in ('AA', 'ZZ'):
            raise ValueError('select lite/ab and an explicit supported country')
        if type(captures_mib) is not int or not 64 <= captures_mib <= 32768:
            raise ValueError('factory captures capacity must be 64..32768 MiB (64 GiB total inspection budget)')
        for item in (workspace, output):
            if item.parts[1] in ('boot', 'efi', 'dev', 'proc', 'sys', 'etc', 'usr', 'bin', 'sbin', 'lib', 'lib64', 'run', 'captures', 'media', 'mnt'):
                raise ValueError('workspace/output must not target a boot, system, device, captures or removable-media tree')
            if not item.parent.is_dir():
                raise ValueError('create workspace/output parent directories first')
            if root in item.parents:
                ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '--quiet', '--', str(item)], check=False).returncode
                if ignored != 0:
                    raise ValueError('paths inside source must be Git-ignored build outputs')
            if item.exists() or item.is_symlink():
                raise ValueError('workspace/output must be new; previous attempts are never overwritten')
            if item == root or item in root.parents:
                raise ValueError('workspace/output may not contain the source checkout')
        if workspace in output.parents or output in workspace.parents or workspace == output:
            raise ValueError('workspace and publication output must be disjoint')
        data.update(source=source(root, revision), upstream=pins(root),
                    inputs={'profile': profile, 'country': country, 'captures_mib': captures_mib,
                            'tft_profile': 'none', 'kiosk_profile': 'none', 'dev_login': False, 'update_version': version,
                            'image_config_sha256': hashlib.sha256(read(root, 'image/config')).hexdigest()})
        if profile == 'ab':
            if not all((cert, key, keyring, version)) or not VERSION.fullmatch(version):
                raise ValueError('A/B requires external --cert, --key, --keyring and a simple --version')
            data['signing'] = signing(cert, key, keyring, root)
            for secret in (cert, key, keyring):
                p = path(secret)
                if workspace in p.parents or output in p.parents:
                    raise ValueError('signing inputs must be external to workspace/output')
        elif any(item is not None for item in (cert, key, keyring, version)):
            raise ValueError('Lite does not support update-signing arguments')
        checks.append({'name': 'Source and input policy', 'status': 'PASS', 'detail': 'exact clean revision and pinned inputs; no ambient build profiles'})
        missing = [tool for tool in TOOLS + (AB_TOOLS if profile == 'ab' else ()) if shutil.which(tool) is None]
        checks.append({'name': 'Factory tools', 'status': 'FAIL' if missing else 'PASS', 'detail': ', '.join(missing) if missing else 'required inspection/signing tools present'})
        host = host_report(workspace / 'assembly')
        data['host'] = host
        versions = {}
        for command in ('git', 'python3', 'xz', 'debugfs', 'rauc', 'mksquashfs'):
            if shutil.which(command):
                try:
                    versions[command] = parser_run([command, '--version'], limit=8192, timeout=3).decode('utf-8', errors='replace')[:1024].strip()
                except (ValueError, ToolUnavailable):
                    versions[command] = 'version probe unavailable (tool presence is checked separately)'
        data['tool_versions'] = versions
        data['reproducibility'] = 'source pinned; live APT/network inputs and build-local random credentials prevent a bit-identical guarantee'
        checks.append({'name': 'Existing builder prerequisites', 'status': 'PASS' if host['build_ready'] else 'FAIL',
                       'detail': 'ready' if host['build_ready'] else 'blockers: ' + ', '.join(host['blockers'] + host['unverified']) + '; inspect host checks; no sudo or installation attempted'})
    except (OSError, ValueError, TypeError, ImportError, subprocess.SubprocessError) as exc:
        checks.append({'name': 'Factory preflight', 'status': 'FAIL', 'detail': str(exc)})
    return dict(schema=1, operation='preflight', result='FAIL' if any(c['status']=='FAIL' for c in checks) else 'PASS',
                checks=checks, **data, factory_completed=False, image_built=False,
                physical_boot_verified=False, automatic_rollback_verified=False)


def write_record(directory, name, value):
    # Atomic journal replacement inside a new private transaction, never an
    # existing candidate directory. Complete receipt is written only once.
    temporary = directory / ('.' + name + '.pending')
    with temporary.open('x', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    if name == 'factory.json':
        os.link(temporary, directory / name)  # atomic no-replace publication marker
        temporary.unlink()
    else:
        os.replace(temporary, directory / name)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def execute(args, *, env, cwd, log, timeout=21600, limit=64*1024*1024):
    """Bounded private combined log; cancellation terminates only our group."""
    with log.open('xb') as stream:
        os.chmod(log, 0o600)
        child = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        def stop():
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise ValueError('stage deadline exceeded; inspect private logs/mounts before choosing a fresh workspace')
                    for entry, _ in selector.select(min(1, max(0, deadline-time.monotonic()))):
                        chunk = os.read(entry.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(entry.fileobj)
                            continue
                        if stream.tell() + len(chunk) > limit:
                            raise ValueError('private build log budget exceeded')
                        stream.write(chunk)
                code = child.wait(timeout=max(.01, deadline-time.monotonic()))
                if code:
                    raise ValueError('stage failed (exit ' + str(code) + '); inspect private stage log')
        except BaseException:
            stop()
            raise
        finally:
            child.stdout.close()


def environment():
    # No arbitrary shell config, PYTHONPATH, REDUX profiles, SSH/proxy secrets
    # or git environment are inherited into the assembly builder.
    return {name: os.environ[name] for name in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'PYTHONPYCACHEPREFIX') if name in os.environ}


def digest(file):
    safe_path(file.parent, file.name)
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 64*1024*1024*1024:
            raise ValueError('artifact is not a singly-linked regular file')
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or identity(before) != identity(file.lstat()):
            raise ValueError('artifact changed during hash')
        return {'sha256': value, 'bytes': before.st_size}


def completed_validation(result, profile):
    # Checksums generated in this transaction are NOT independent authenticity.
    allowed = {'Independent artifact authenticity', 'Physical boot/update/recovery acceptance'}
    if not result.get('checks') or any(row['status'] == 'FAIL' or (row['status'] != 'PASS' and row['name'] not in allowed) for row in result['checks']):
        raise ValueError('actual candidate verification incomplete or failed; inspect attempt.json')
    required = {'Image integrity/layout', 'Release source identity', 'Embedded runtime contract', 'Embedded boot contract'}
    if profile == 'ab':
        required |= {'RAUC readiness', 'Authenticated update bundle', 'Paired runtime consistency'}
    else:
        required.add('Update safety')
    passed = {row['name'] for row in result['checks'] if row['status'] == 'PASS'}
    if not required <= passed:
        raise ValueError('missing required actual-artifact validation evidence')


def build(root, *, revision, profile, country, workspace, output, cert=None, key=None,
          keyring=None, version=None, captures_mib=512, runner=execute):
    report = preflight(root, revision=revision, profile=profile, country=country, workspace=workspace,
                       output=output, cert=cert, key=key, keyring=keyring, version=version, captures_mib=captures_mib)
    if report['result'] == 'FAIL':
        return report
    root, workspace, output = path(root), path(workspace), path(output)
    workspace.mkdir(mode=0o700)  # exclusive reservation; parents must already exist
    journal = dict(report, operation='build', state='assembling', stage='source_snapshot', result='UNKNOWN', factory_completed=False)
    write_record(workspace, 'attempt.json', journal)
    env = environment()
    clone = workspace / 'source'
    try:
        runner(['git', 'clone', '--no-hardlinks', '--no-checkout', '--', str(root), str(clone)], env=env, cwd=root, log=workspace/'clone.log', timeout=120)
        runner(['git', '-C', str(clone), 'checkout', '--detach', revision], env=env, cwd=root, log=workspace/'checkout.log', timeout=120)
        source(clone, revision)
        assembly = workspace / 'assembly'
        env.update(REDUX_BUILD_DIR=str(assembly), REDUX_REGULATORY_COUNTRY=country,
                   REDUX_CAPTURES_MIB=str(captures_mib), SOURCE_DATE_EPOCH=str(report['source']['source_date_epoch']), REDUX_TFT_PROFILE='none', REDUX_KIOSK_PROFILE='none', REDUX_DEV_LOGIN='0')
        if profile == 'ab':
            snapshot_file(path(cert), workspace/'certificate.pem', limit=1024*1024)
            snapshot_file(path(keyring), workspace/'keyring.pem', limit=1024*1024)
            env['REDUX_OTA_CERT'] = str(workspace/'keyring.pem')
        journal['stage'] = 'image_assembly'
        write_record(workspace, 'attempt.json', journal)
        runner(['bash', str(clone/'build.sh')], env=env, cwd=clone, log=workspace/'build.log')
        source(root, revision); source(clone, revision)
        deploy = path(assembly / 'pi-gen/deploy')
        images = list(deploy.glob('*.img.xz'))
        if len(images) != 1:
            raise ValueError('builder must produce exactly one compressed image')
        image = path(images[0])
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\.img\.xz', image.name):
            raise ValueError('builder image filename is unsafe')
        journal['stage'] = 'image_integrity'
        measured = verify_image(image, expected_profile=profile)
        if measured['image_metadata']['partitions'][-1]['sector_count'] * 512 != captures_mib * 1024 * 1024:
            raise ValueError('actual captures partition differs from selected capacity')
        journal.update(image_built=True, image_integrity={key: measured[key] for key in ('sha256', 'disk_bytes', 'image_profile')})
        manifest = json_record(read(deploy, image.name+'.release.json', 256*1024))
        if manifest['revisions'] != {'redux': revision, 'pi_gen': report['upstream']['PI_GEN_REV'], 'nexmon': report['upstream']['NEXMON_REV']}:
            raise ValueError('builder manifest revisions contradict selected immutable inputs')
        bundle = None
        if profile == 'ab':
            journal['stage'] = 'bundle_signing'
            write_record(workspace, 'attempt.json', journal)
            # The existing bundle builder consumes a raw image and performs its
            # own root-only read-only mounts. Never mount a host device here.
            raw = workspace / 'bundle-input.img'
            with lzma.open(image, 'rb') as source_stream, raw.open('xb') as target:
                remaining = measured['disk_bytes']
                while remaining:
                    chunk = source_stream.read(min(1024*1024, remaining))
                    if not chunk:
                        raise ValueError('truncated compressed bundle input')
                    target.write(chunk); remaining -= len(chunk)
                if source_stream.read(1):
                    raise ValueError('bundle input exceeded measured disk size')
            if signing(cert, key, keyring, root) != report['signing']:
                raise ValueError('external signing inputs changed during assembly')
            if digest(workspace/'certificate.pem')['sha256'] != report['signing']['certificate_sha256'] or digest(workspace/'keyring.pem')['sha256'] != report['signing']['keyring_sha256']:
                raise ValueError('public signing snapshots changed during assembly')
            bundle = workspace / 'update.raucb'
            runner(['bash', str(clone/'image/make-bundle.sh'), str(raw), str(bundle),
                    str(workspace/'certificate.pem'), str(path(key)), version, str(workspace/'keyring.pem')], env=env, cwd=clone, log=workspace/'bundle.log')
        # Reserve a fresh publication directory; no earlier candidate is ever
        # overwritten or renamed. Failed partial directories have no receipt.
        journal['stage'] = 'publication_and_validation'
        write_record(workspace, 'attempt.json', journal)
        output.mkdir(mode=0o700)
        for name in (image.name, image.name+'.release.json', image.name+'.sha256'):
            snapshot_file(deploy/name, output/name)
        if bundle is not None:
            snapshot_file(bundle, output/'update.raucb')
            snapshot_file(workspace/'keyring.pem', output/'keyring.pem', limit=1024*1024)
        if profile == 'ab' and signing(cert, key, keyring, root) != report['signing']:
            raise ValueError('external trust changed before publication')
        result = qualify(output/image.name, revision=revision, profile=profile, country=country,
                         bundle=output/'update.raucb' if bundle else None,
                         keyring=path(keyring) if bundle else None, version=version, temporary_root=workspace)
        journal['validation'] = result
        completed_validation(result, profile)
        source(root, revision); source(clone, revision)
        expected_files = {image.name, image.name+'.release.json', image.name+'.sha256'} | ({'update.raucb', 'keyring.pem'} if bundle else set())
        if {item.name for item in output.iterdir()} != expected_files:
            raise ValueError('publication directory changed during verification')
        files = {item.name: digest(item) for item in output.iterdir()}
        journal.update(state='completed', stage='completed', result='UNKNOWN', factory_completed=True, image_built=True,
                       image=image.name, files=files, version=version, completed_utc=time.time(),
                       release_authenticated=False, update_bundle_authenticated=(profile=='ab'), note='Unsigned image transport is not independently authenticated; physical acceptance remains open. Source pinned, not bit-for-bit reproducible APT.')
        write_record(output, 'factory.json', journal)
        try:
            write_record(workspace, 'attempt.json', journal)
        except OSError as error:
            journal['private_journal_warning'] = type(error).__name__
            journal['checks'].append({'name':'Private attempt journal','status':'UNKNOWN','detail':'Candidate receipt is committed; private journal update failed. Verify the output directory before any further action.'})
        return journal
    except BaseException as error:
        journal['factory_completed'] = False
        journal.update(state='interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'failed',
                       result='FAIL', error=type(error).__name__, failure_detail=str(error)[:300], recovery='Preserve workspace/logs. Inspect for leftover build mounts/processes; never reuse a failed workspace or output. Correct prerequisites and choose new paths.')
        try:
            write_record(workspace, 'attempt.json', journal)
        except (OSError, ValueError) as journal_error:
            journal['private_journal_warning'] = type(journal_error).__name__
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return journal


def inspect(directory):
    directory = path(directory)
    value = json_record(read(directory, 'factory.json', 1024*1024))
    if value.get('schema') != 1 or value.get('state') != 'completed' or value.get('factory_completed') is not True:
        raise ValueError('candidate has no completed factory receipt')
    if not isinstance(value.get('image'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\.img\.xz',value['image']):
        raise ValueError('invalid factory image name')
    inputs = value.get('inputs')
    if not isinstance(inputs, dict) or inputs.get('profile') not in ('lite','ab'):
        raise ValueError('invalid factory profile')
    recorded_source = value.get('source')
    if not isinstance(recorded_source, dict) or not REV.fullmatch(recorded_source.get('revision', '')) or not REV.fullmatch(recorded_source.get('tree', '')):
        raise ValueError('invalid recorded source identity')
    files = value.get('files')
    if not isinstance(files, dict) or not 3 <= len(files) <= 5:
        raise ValueError('invalid factory file inventory')
    expected = {value['image'], value['image']+'.release.json', value['image']+'.sha256'} | ({'update.raucb','keyring.pem'} if inputs['profile']=='ab' else set())
    if set(files) != expected:
        raise ValueError('incomplete or contradictory candidate inventory')
    for name, record in files.items():
        location = safe_path(directory, name)
        if '/' in name or not isinstance(record, dict) or digest(location) != record:
            raise ValueError('candidate file inventory mismatch')
    if value.get('image') not in files:
        raise ValueError('factory image is missing')
    return dict(schema=1, operation='inspect', result='UNKNOWN', factory_completed=True,
                recorded_source=value['source'], files=files, inputs=value['inputs'], image=value['image'],
                note='Inventory hashes match an unsigned local receipt; embedded payload/signature verification has not been rerun',
                physical_boot_verified=False, automatic_rollback_verified=False)


def verify(directory, *, trusted_sha256=None, keyring=None, temporary_root=None):
    inventory = inspect(directory)
    inputs, revision = inventory['inputs'], inventory['recorded_source']['revision']
    if inputs['profile'] == 'ab' and keyring is None:
        raise ValueError('A/B re-verification requires an independently supplied --keyring')
    directory = path(directory)
    receipt = json_record(read(directory, 'factory.json', 1024*1024))
    result = qualify(directory/inventory['image'], revision=revision, profile=inputs['profile'], country=inputs['country'],
        trusted_sha256=trusted_sha256, bundle=directory/'update.raucb' if inputs['profile']=='ab' else None,
        keyring=keyring, version=receipt.get('version'), temporary_root=temporary_root)
    return dict(result, operation='verify', factory_completed=True)
