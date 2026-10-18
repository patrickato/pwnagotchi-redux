"""Bounded read-only dual-radio readiness. No setters, agent, packet socket or RF.

Kernel/process/USB identities remain private. Disk policy, loaded attestation,
NM ownership, real framing and decoded reception are separate evidence gates.
"""
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import tomllib
import urllib.error
import urllib.request

from .local_evidence import BoundedReader, Unavailable, inventory, iw_interfaces, read_small
from redux.core.runtime_identity import process_identity


class Reader(BoundedReader):
    @staticmethod
    def allowed(args):
        a = tuple(args)
        if BoundedReader.allowed(args):
            return True
        if a in {('/usr/local/bin/nexutil','-I','wlan0','-m'),
                 ('systemctl','show','--property=MainPID','--value','redux-live.service')}:
            return True
        if (len(a)==6 and a[:5]==('nmcli','-g','GENERAL.DBUS-PATH','device','show')
                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,14}',a[5])):
            return True
        if (len(a)==6 and a[:5]==('ip','-j','addr','show','dev')
                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,14}',a[5])):
            return True
        if a == ('busctl','--system','--auto-start=no','call','org.freedesktop.DBus',
                 '/org/freedesktop/DBus','org.freedesktop.DBus','GetNameOwner','s','org.freedesktop.NetworkManager'):
            return True
        return (len(a)==8 and a[:4]==('busctl','--system','--auto-start=no','get-property')
            and re.fullmatch(r':[0-9]+\.[0-9]+',a[4])
            and re.fullmatch(r'/org/freedesktop/NetworkManager/Devices/[0-9]+',a[5])
            and a[6]=='org.freedesktop.NetworkManager.Device'
            and a[7] in ('Interface','Managed','Autoconnect','State'))


def trusted_file(path, limit=65536):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        s=os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022
                or not 1<=s.st_size<=limit):
            raise Unavailable('file_untrusted')
        raw=os.read(fd,limit+1)
        if len(raw)>limit:raise Unavailable('file_overflow')
        return raw,s
    finally:os.close(fd)


def loaded_policy(attestation, supervisor, boot):
    """None means UNKNOWN. Timestamp/file age alone can never fill this gate."""
    keys=('preferred_wlan0','auto_select','usb_lease_required','allow_connected_capture')
    if (type(attestation)is not dict or type(attestation.get('version'))is not int or attestation.get('version')!=1
            or type(attestation.get('supervisor_pid'))is not int
            or type(attestation.get('start_ticks'))is not int
            or (attestation['supervisor_pid'],attestation['start_ticks'])!=supervisor[:2]
            or attestation.get('boot_id')!=boot
            or any(type(attestation.get(k))is not bool for k in keys)
            or attestation['preferred_wlan0'] and attestation['auto_select']):
        return None
    return {k:attestation[k] for k in keys}


def _root_process(pid, proc):
    identity=process_identity(pid,proc)
    p=proc/str(pid)
    uid=re.search(r'(?m)^Uid:\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',read_small(p/'status',16384))
    if not uid or any(v!='0' for v in uid.groups()) or '/redux-live.service'not in read_small(p/'cgroup'):
        raise Unavailable('process_owner_unknown')
    return identity,read_small(p/'cmdline',16384).split('\x00'),(p/'exe').resolve(strict=True).name


def runtime_facts(run, *, proc=Path('/proc'), live=Path('/captures/live/live.json'),
                  config=Path('/etc/redux/live.toml'), wall=time.time):
    supervisor=int(run(['systemctl','show','--property=MainPID','--value','redux-live.service']).strip())
    sp,args,exe=_root_process(supervisor,proc)
    if (not re.fullmatch(r'python3(?:\.[0-9]+)?',exe) or args.count('-m')!=1
            or args[args.index('-m')+1]!='redux.core.live_runtime'
            or args.count('--config')>1
            or '--config'in args and args[args.index('--config')+1]!=str(config)):
        raise Unavailable('supervisor_arguments_unknown')
    raw,st=trusted_file(config,32768)
    cfg=tomllib.loads(raw.decode()).get('live',{})
    preferred=cfg.get('preferred_iface','')
    if not isinstance(preferred,str):raise Unavailable('config_invalid')
    data=json.loads(trusted_file(live)[0])
    if type(data)is not dict:raise Unavailable('checkpoint_shape_invalid')
    stamp=data.get('updated_utc')
    if (type(stamp)not in (int,float) or not math.isfinite(stamp) or not 0<=wall()-stamp<=12
            or not isinstance(data.get('state'),str)):
        raise Unavailable('checkpoint_unverified')
    child=None
    child_iface=None
    if data.get('pid') is not None:
        child,argv,binary=_root_process(data['pid'],proc)
        if binary!='bettercap' or argv.count('-iface')!=1:
            raise Unavailable('bettercap_arguments_unknown')
        child_iface=argv[argv.index('-iface')+1]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,14}',child_iface):
            raise Unavailable('bettercap_arguments_unknown')
    boot=read_small(proc/'sys/kernel/random/boot_id')
    attested=loaded_policy(data.get('configuration_identity'),sp,boot)
    btime=re.search(r'(?m)^btime ([0-9]+)$',read_small(proc/'stat',65536))
    started=int(btime[1])+sp[1]/os.sysconf('SC_CLK_TCK') if btime else None
    predates=bool(started is not None and max(st.st_mtime,st.st_ctime)<started-2)
    for pid,expected in ((supervisor,sp),)+(((child[0],child),) if child else ()):
        if process_identity(pid,proc)!=expected:raise Unavailable('process_replaced')
    raw2,st2=trusted_file(config,32768)
    if raw!=raw2 or (st.st_ino,st.st_dev,st.st_mtime_ns,st.st_ctime_ns)!=(st2.st_ino,st2.st_dev,st2.st_mtime_ns,st2.st_ctime_ns):
        raise Unavailable('configuration_changed')
    public=dict(supervisor_identity_verified=True, bettercap_identity_verified=child is not None,
        state_running=data['state']=='running',
        effective_onboard_assignment='UNKNOWN'if child is None else'PASS'if child_iface=='wlan0'and data.get('iface')=='wlan0'else'FAIL', bettercap_argument_onboard=child_iface=='wlan0' if child else None,
        checkpoint_iface_matches_argument=data.get('iface')==child_iface if child else None, disk_preference='wlan0' if preferred=='wlan0' else 'auto' if preferred=='' else 'other',
        disk_predates_supervisor=predates, loaded_policy=attested,
        loaded_wlan0_pin='UNKNOWN' if attested is None else 'PASS' if attested['preferred_wlan0'] else 'FAIL')
    public['counters']={k:data.get(k)if type(data.get(k))is int and 0<=data[k]<=10**12 else None
                        for k in ('events_seen','handed_off','failure_streak')}
    return public,(sp,child,boot,raw,child_iface)


def nm_policy(run,iface):
    owner_cmd=['busctl','--system','--auto-start=no','call','org.freedesktop.DBus',
               '/org/freedesktop/DBus','org.freedesktop.DBus','GetNameOwner','s','org.freedesktop.NetworkManager']
    def owner():
        raw=run(owner_cmd)
        if not raw.startswith('s '):raise Unavailable('nm_owner_unknown')
        value=json.loads(raw[2:])
        if not isinstance(value,str) or not re.fullmatch(r':[0-9]+\.[0-9]+',value):raise Unavailable('nm_owner_unknown')
        return value
    unique=owner()
    path=run(['nmcli','-g','GENERAL.DBUS-PATH','device','show',iface]).strip()
    if not re.fullmatch(r'/org/freedesktop/NetworkManager/Devices/[0-9]+',path):
        raise Unavailable('nm_not_enumerated')
    def snapshot():
        result={}
        for name,kind in (('Interface','s'),('Managed','b'),('Autoconnect','b'),('State','u')):
            raw=run(['busctl','--system','--auto-start=no','get-property',unique,path,
                     'org.freedesktop.NetworkManager.Device',name])
            if not raw.startswith(kind+' '):raise Unavailable('nm_property_invalid')
            result[name]=json.loads(raw[2:])
        if (result['Interface']!=iface or type(result['Managed'])is not bool
                or type(result['Autoconnect'])is not bool or type(result['State'])is not int
                or not 0<=result['State']<=120):raise Unavailable('nm_identity_unknown')
        return result
    first=snapshot()
    if first!=snapshot() or unique!=owner():raise Unavailable('nm_changed')
    return dict(managed=first['Managed'],autoconnect=first['Autoconnect'],state=first['State'],
                owner_stable=True)


def packet_bindings(proc,child,onboard_index,usb_indices=()):
    usb_indices=set(usb_indices)
    owned=set()
    for n,fd in enumerate((proc/str(child[0])/'fd').iterdir()):
        if n>=1024:raise Unavailable('fd_overflow')
        try:link=os.readlink(fd)
        except FileNotFoundError:continue
        m=re.fullmatch(r'socket:\[([0-9]+)\]',link)
        if m:owned.add(int(m[1]))
    lines=read_small(proc/'net/packet',131072).splitlines()
    if len(lines)>257 or not lines or lines[0].split()!=['sk','RefCnt','Type','Proto','Iface','R','Rmem','User','Inode']:
        raise Unavailable('packet_inventory_unknown')
    bound=[]
    for line in lines[1:]:
        row=line.split()
        if len(row)!=9:raise Unavailable('packet_inventory_unknown')
        if int(row[8])in owned:bound.append(int(row[4]))
    if process_identity(child[0],proc)!=child:raise Unavailable('process_replaced')
    return dict(bettercap_packet_sockets=len(bound),onboard_bound=sum(v==onboard_index for v in bound),
                wildcard_bound=bound.count(0),usb_bound=sum(v in usb_indices for v in bound),
                other_bound=sum(v not in usb_indices and v not in (0,onboard_index)for v in bound))


def capture_counts(root=Path('/captures')):
    """Existing file metadata only, never read a PCAP byte or emit a filename."""
    result={}
    for name in ('active','incoming','ready'):
        files=total=0;truncated=False
        fd=os.open(Path(root)/name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            with os.scandir(fd)as entries:
                for n,item in enumerate(entries):
                    if n>=256:
                        truncated=True;break
                    info=item.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and (name=='ready' or item.name.endswith('.pcap')):
                        files+=1;total+=info.st_size
        finally:os.close(fd)
        result[name]=dict(files=files,bytes=total,truncated=truncated)
    return result


def api_status():
    """One unauthenticated loopback GET; never read body or extract credentials."""
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:8081/api/session',timeout=1)as response:
            return response.status
    except urllib.error.HTTPError as error:
        code=error.code;error.close();return code
    except (OSError,urllib.error.URLError):return None


def collect_readiness(peer, *, run=None, sys=Path('/sys/class/net'), proc=Path('/proc'),
                      usb=Path('/sys/bus/usb/devices'), live=Path('/captures/live/live.json'),
                      config=Path('/etc/redux/live.toml'), api=api_status):
    ipaddress.ip_address(peer)
    run=run or Reader(seconds=30)
    started=time.monotonic()
    def ethernet():
        routes=json.loads(run(['ip','-j','route','get',peer]))
        addresses=json.loads(run(['ip','-j','addr','show','dev','eth0']))
        if (len(routes)!=1 or routes[0].get('dev')!='eth0' or routes[0].get('type','unicast')!='unicast'
                or len(addresses)!=1 or addresses[0].get('ifname')!='eth0'
                or 'UP'not in addresses[0].get('flags',[])
                or routes[0].get('prefsrc')not in [a.get('local')for a in addresses[0].get('addr_info',[])]):
            raise Unavailable('ethernet_unverified')
        node=Path(sys)/'eth0'
        st=node.stat()
        if int(read_small(node/'ifindex'))!=addresses[0]['ifindex']:raise Unavailable('ethernet_changed')
        return st.st_dev,st.st_ino,addresses[0]['ifindex'],routes[0].get('prefsrc'),tuple(sorted(a['local']for a in addresses[0]['addr_info']))
    result=dict(format='redux-dual-radio-readiness-v1',hardware_changes=False,
        runtime={'loaded_wlan0_pin':'UNKNOWN'},onboard={'decoded_reception':'UNKNOWN'},
        alfa=dict(usb_candidates=None,netdev_count=None,devices=[]))
    try:
        eth=ethernet()
        before=inventory(sys)
        public,private=runtime_facts(run,proc=proc,live=live,config=config)
        result['runtime']=public
        if 'wlan0'in before and private[1] is not None:
            result['runtime'].update(packet_bindings(proc,private[1],before['wlan0'][2],
                    tuple(r[2]for r in before.values()if r[8]=='usb')))
        iw=iw_interfaces(run(['iw','dev']))
        row=before.get('wlan0')
        if row:
            result['onboard'].update(mode=iw.get('wlan0',{}).get('type','unknown'),arphrd=row[4],
                native_framing=row[4]in (801,803),transport_onboard=row[8]=='onboard')
        try:
            fw=re.fullmatch(r'monitor:\s*([0-9]+)\s*',run(['/usr/local/bin/nexutil','-I','wlan0','-m']).strip())
            result['onboard']['firmware_monitor']=int(fw[1])if fw else None
        except (Unavailable,OSError):result['onboard']['firmware_monitor']=None
        try:result['onboard']['nm']=nm_policy(run,'wlan0')
        except (Unavailable,OSError,ValueError):result['onboard']['nm']=dict(managed=None,autoconnect=None,state=None,owner_stable=None)
        candidates=[]
        for n,p in enumerate(Path(usb).iterdir()):
            if n>=256:raise Unavailable('usb_inventory_overflow')
            if not (p/'idVendor').exists():continue
            if (read_small(p/'idVendor'),read_small(p/'idProduct'))==('0e8d','7612'):
                s=p.stat();candidates.append((str(p.resolve()),s.st_dev,s.st_ino,read_small(p/'devnum')))
        devices=[]
        alfa_names=[]
        for name,row in sorted(before.items()):
            device=(Path(sys)/name/'device').resolve(strict=True)
            driver=(device/'driver').resolve(strict=True).name
            if row[8]!='usb' or driver!='mt76x2u':continue
            if not any(str(p)==c[0] for p in (device,*device.parents)for c in candidates):continue
            alfa_names.append(name)
            try:policy=nm_policy(run,name)
            except (Unavailable,OSError,ValueError):policy=dict(managed=None,autoconnect=None,state=None,owner_stable=None)
            addresses=json.loads(run(['ip','-j','addr','show','dev',name]))
            if len(addresses)!=1 or addresses[0].get('ifname')!=name or addresses[0].get('ifindex')!=row[2]:raise Unavailable('adapter_identity_changed')
            info=run(['iw','dev',name,'info'])
            frequency=re.search(r'\bchannel [0-9]+ \(([0-9]+) MHz\)',info)
            devices.append(dict(frequency_mhz=int(frequency[1])if frequency else None,adapter=len(devices)+1,mode=iw.get(name,{}).get('type','unknown'),
                arphrd=row[4],up=bool(row[5]&1),ip_addresses=len(addresses[0].get('addr_info',[])),**policy))
        if before!=inventory(sys):raise Unavailable('radio_changed')
        for path,dev,ino,number in candidates:
            p=Path(path);s=p.stat()
            if (dev,ino,number)!=(s.st_dev,s.st_ino,read_small(p/'devnum')):raise Unavailable('adapter_identity_changed')
        _,after=runtime_facts(run,proc=proc,live=live,config=config)
        if private!=after:raise Unavailable('runtime_changed')
        if eth!=ethernet():raise Unavailable('ethernet_changed')
        if not 0<=time.monotonic()-started<=10:raise Unavailable('observation_stale')
        result['ethernet_verified']=True
        result['runtime']['bettercap_argument_alfa']=private[4] in alfa_names if private[1]else None
        result['alfa']=dict(usb_candidates=len(candidates),netdev_count=len(devices),devices=devices)
        result['runtime']['bettercap_unauth_http']=api()
        try:result['capture_files']=capture_counts()
        except OSError:result['capture_files']=None
        result['observation']='stable'
    except (Unavailable,OSError,ValueError,TypeError,KeyError,IndexError)as error:
        known={'process_owner_unknown','supervisor_arguments_unknown','config_invalid',
               'checkpoint_unverified','checkpoint_shape_invalid','bettercap_arguments_unknown',
               'process_replaced','configuration_changed','usb_inventory_overflow',
               'adapter_identity_changed','radio_changed','runtime_changed','file_untrusted',
               'ethernet_unverified','ethernet_changed','observation_stale',
               'file_overflow','command_denied','command_timeout','command_failed','deadline'}
        reason=str(error)if isinstance(error,Unavailable)and str(error)in known else 'measurement_unavailable'
        # Discard partial affirmations on any generation, permission or input fault.
        return dict(format=result['format'],hardware_changes=False,observation='unverified',
            runtime={'loaded_wlan0_pin':'UNKNOWN'},onboard={'decoded_reception':'UNKNOWN'},reason=reason,
            alfa=dict(usb_candidates=None,netdev_count=None,devices=[]))
    return result
