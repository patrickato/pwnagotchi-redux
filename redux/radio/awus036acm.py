"""Linux backend for the opt-in, pre-isolated AWUS036ACM passive trial.

No NM/service/onboard setters, VIFs, hotplug, lease receipts or transmit API.
Private device/process identities never leave this module's snapshots.
"""
import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import platform
import signal
import time
import tomllib
import hashlib

from .local_evidence import BoundedReader, Unavailable, inventory, iw_interfaces, read_small
from .passive_qualification import Observation, Stop, WindowExpired, run_once


class AdapterOps:
    def __init__(self, iface, peer, *, execution_approved=False, sys_root=Path('/sys/class/net'),
                 proc_root=Path('/proc'), live_path=Path('/captures/live/live.json'), config_path=Path('/etc/redux/live.toml')):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,15}', iface) or iface in ('wlan0','eth0','lo'):
            raise ValueError('separate USB interface required')
        self.iface=iface;self.peer=str(ipaddress.ip_address(peer))
        self.execution_approved=execution_approved is True
        self.sys_root=Path(sys_root);self.proc_root=Path(proc_root);self.live_path=Path(live_path);self.config_path=Path(config_path)
        self.end=time.monotonic()+20;self.consumed=False;self.used=set();self.pinned=None
        self.receiver=None;self.baseline_protected=None;self.arm_alarm=False;self.window_end=None;self._pending_command=None

    def deadline(self, end):
        if not math.isfinite(end) or not 0 < end-time.monotonic() <= 121:
            raise Stop('deadline')
        self.end=end
        self.window_end=None
        if self.arm_alarm:
            signal.setitimer(signal.ITIMER_REAL,max(.001,end-time.monotonic()))

    def receive_deadline(self, end):
        self.window_end=end
        if self.arm_alarm:
            signal.setitimer(signal.ITIMER_REAL,max(.001,(end if end is not None else self.end)-time.monotonic()))

    def _run(self,args, *, changing=False):
        left=self.end-time.monotonic()
        if left<=0:raise Stop('deadline')
        allowed_change = tuple(args) if changing and self.execution_approved else None
        if changing and (allowed_change is None or allowed_change!=self._pending_command
                         or list(args) not in self._commands().values()):
            raise Stop('command_denied')
        class Reader(BoundedReader):
            def allowed(self, candidate):
                a=tuple(candidate)
                return (a == allowed_change or super().allowed(candidate) or
                    a == ('systemctl','show','--property=MainPID','--value','redux-live.service'))
        return Reader(seconds=min(45,left),command_seconds=min(2,left))(args)

    def _identity(self):
        rows=inventory(self.sys_root)
        if set(rows)!={self.iface,'wlan0'}:raise Stop('radio_inventory')
        row=rows[self.iface];primary=rows['wlan0']
        if row[8]!='usb' or primary[8]!='onboard' or row[3]==primary[3]:raise Stop('radio_transport')
        node=self.sys_root/self.iface
        driver=(node/'device/driver').resolve(strict=True)
        if driver.name!='mt76x2u':raise Stop('adapter_driver')
        device=(node/'device').resolve(strict=True)
        usb=[p for p in (device,*device.parents) if (p/'idVendor').exists() and (p/'idProduct').exists()]
        if not usb:raise Stop('usb_identity')
        usb=usb[0];st=usb.stat()
        if (read_small(usb/'idVendor'),read_small(usb/'idProduct'))!=('0e8d','7612'):
            raise Stop('adapter_model')
        if (self.sys_root/'wlan0/device/driver').resolve(strict=True).name!='brcmfmac':
            raise Stop('onboard_driver')
        identity=(self.iface,row[:4],row[6:],str(driver),driver.stat().st_ino,str(usb),st.st_dev,st.st_ino,
                  read_small(usb/'busnum'),read_small(usb/'devnum'),read_small(usb/'bcdDevice'),
                  read_small(self.proc_root/'sys/kernel/random/boot_id'))
        return identity, rows

    def pin_check(self, identity):
        if time.monotonic()>=self.end or self._identity()[0]!=identity:
            raise Stop('identity_changed')

    def _pid(self,pid):
        if type(pid)is not int or pid<=1:raise Stop('pid_unknown')
        path=self.proc_root/str(pid)
        st=(path/'exe').stat()
        fields=read_small(path/'stat',16384).rsplit(')',1)[1].split()
        return pid,fields[19],st.st_dev,st.st_ino

    def _live_owner(self):
        fd=os.open(self.live_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022 or st.st_size>65536:
                raise Stop('runtime_untrusted')
            raw=os.read(fd,65537)
        finally:os.close(fd)
        live=json.loads(raw)
        stamp=live.get('updated_utc')
        if (live.get('state')!='running' or live.get('iface')!='wlan0'
                or type(stamp)not in (int,float) or not 0<=time.time()-stamp<=12):
            raise Stop('runtime_unverified')
        pid=live.get('pid');identity=self._pid(pid);p=self.proc_root/str(pid)
        if ((p/'exe').resolve(strict=True).name!='bettercap'
                or '/redux-live.service' not in read_small(p/'cgroup')):
            raise Stop('runtime_owner')
        args=read_small(p/'cmdline',16384).split('\x00')
        if args.count('-iface')!=1 or args[args.index('-iface')+1]!='wlan0':raise Stop('runtime_binding')
        uid=re.search(r'(?m)^Uid:\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',read_small(p/'status',16384))
        if not uid or any(v!='0' for v in uid.groups()):raise Stop('runtime_uid')
        if self._run(['systemctl','show','--property=ActiveState','--value','redux-live.service']).strip()!='active':
            raise Stop('runtime_inactive')
        supervisor=int(self._run(['systemctl','show','--property=MainPID','--value','redux-live.service']).strip())
        supervisor_identity=self._pid(supervisor)
        # Merely observing Bettercap on wlan0 does not pin its NEXT rotation.
        # Require the already-loaded private config to prefer wlan0 explicitly.
        args=read_small(self.proc_root/str(supervisor)/'cmdline',16384).split('\x00')
        if '-m' not in args or args[args.index('-m')+1]!='redux.core.live_runtime':
            raise Stop('supervisor_command')
        if '--config' in args:
            if args[args.index('--config')+1]!=str(self.config_path):raise Stop('runtime_config_path')
        elif self.config_path!=Path('/etc/redux/live.toml'):
            raise Stop('runtime_config_path')
        fd=os.open(self.config_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            cfgstat=os.fstat(fd)
            if (not stat.S_ISREG(cfgstat.st_mode) or cfgstat.st_uid!=0 or cfgstat.st_mode&0o077
                    or cfgstat.st_size>32768):raise Stop('runtime_config_untrusted')
            config=os.read(fd,32769)
        finally:os.close(fd)
        values=tomllib.loads(config.decode('utf-8')).get('live',{})
        boot=re.search(r'(?m)^btime ([0-9]+)$',read_small(self.proc_root/'stat',65536))
        started=(int(boot[1])+int(supervisor_identity[1])/os.sysconf('SC_CLK_TCK')) if boot else 0
        if (values.get('preferred_iface')!='wlan0' or values.get('require_usb_lease',False) is not False
                or values.get('allow_connected_capture',False) is not False or cfgstat.st_mtime>=started-2):
            raise Stop('runtime_not_pinned')
        return identity,supervisor_identity,hashlib.sha256(config).digest()

    def _packets(self, better_pid, onboard_index, own_inode=None, target_index=None):
        # Socket inode ownership, not process-name exemption. No foreign or
        # wildcard packet socket may target the qualification PHY.
        fds=self.proc_root/str(better_pid)/'fd';owned=set()
        for number,item in enumerate(fds.iterdir()):
            if number>=1024:raise Stop('fd_budget')
            try:link=os.readlink(item)
            except FileNotFoundError:continue
            m=re.fullmatch(r'socket:\[([0-9]+)\]',link)
            if m:owned.add(int(m[1]))
        lines=read_small(self.proc_root/'net/packet',131072).splitlines()
        if not lines or lines[0].split()!=['sk','RefCnt','Type','Proto','Iface','R','Rmem','User','Inode'] or len(lines)>257:
            raise Stop('packet_inventory')
        own_found=own_inode is None
        for line in lines[1:]:
            fields=line.split()
            if len(fields)!=9:raise Stop('packet_inventory')
            index,inode=int(fields[4]),int(fields[8])
            if inode==own_inode:
                if index!=target_index:raise Stop('socket_binding')
                own_found=True
            elif inode not in owned or index!=onboard_index:
                raise Stop('foreign_packet_owner')
        if not own_found:raise Stop('socket_binding')

    def observe(self):
        if (platform.machine()!='aarch64' or not re.search(r'Raspberry Pi [45] Model',
                read_small(self.proc_root/'device-tree/model').rstrip('\x00'))):
            raise Stop('platform')
        identity,rows=self._identity()
        if self.pinned is not None and identity!=self.pinned:raise Stop('identity_changed')
        dev=iw_interfaces(self._run(['iw','dev']))
        if set(dev)!=set(rows):raise Stop('iw_inventory')
        for name,row in rows.items():
            if dev[name].get('ifindex')!=str(row[2]) or dev[name].get('phy')!=row[3]:raise Stop('iw_identity')
        phy_raw=self._run(['iw','phy'])
        block=re.search(r'(?ms)^Wiphy '+re.escape(rows[self.iface][3])+r'\n(.*?)(?=^Wiphy |\Z)',phy_raw)
        modes=re.search(r'(?m)^\s*Supported interface modes:\s*\n((?:[ \t]+\*[^\n]*\n?)+)',block[1]) if block else None
        if not modes or not re.search(r'(?m)^\s*\* monitor\s*$',modes[1]):raise Stop('monitor_not_advertised')
        info=self._run(['iw','dev',self.iface,'info'])
        freq=re.search(r'\bchannel \d+ \((\d+) MHz\)',info)
        primary_info=self._run(['iw','dev','wlan0','info'])
        if dev['wlan0'].get('type')!='monitor' or not rows['wlan0'][5]&1:raise Stop('onboard_state')
        nm=self._run(['nmcli','-g','GENERAL.NM-MANAGED,GENERAL.STATE','device','show',self.iface])
        if not re.fullmatch(r'no\n10(?: \([^\n]*\))?\s*',nm):raise Stop('nm_exclusion')
        primary_nm=self._run(['nmcli','-g','GENERAL.NM-MANAGED,GENERAL.STATE','device','show','wlan0'])
        if not re.fullmatch(r'yes\n20(?: \([^\n]*\))?\s*',primary_nm):raise Stop('onboard_nm')
        supp=self._run(['busctl','--system','--auto-start=no','get-property','fi.w1.wpa_supplicant1',
                       '/fi/w1/wpa_supplicant1','fi.w1.wpa_supplicant1','Interfaces'])
        if not re.fullmatch(r'ao 0\s*',supp):raise Stop('supplicant_binding')
        country=self._run(['iw','reg','get']).split('phy#',1)[0]
        if not re.search(r'(?m)^country US:',country):raise Stop('country')
        routes=json.loads(self._run(['ip','-j','route','get',self.peer]))
        eth=json.loads(self._run(['ip','-j','addr','show','dev','eth0']))
        if (len(routes)!=1 or routes[0].get('dev')!='eth0' or routes[0].get('type','unicast')!='unicast'
                or len(eth)!=1 or eth[0].get('ifname')!='eth0' or 'UP'not in eth[0].get('flags',[])
                or routes[0].get('prefsrc') not in [a.get('local') for a in eth[0].get('addr_info',[])]):
            raise Stop('ethernet')
        est=(self.sys_root/'eth0').stat()
        ethernet=(est.st_dev,est.st_ino,eth[0]['ifindex'],routes[0].get('prefsrc'),
                  tuple(sorted(a['local'] for a in eth[0]['addr_info'])))
        owner=self._live_owner()
        known=[]
        for number,p in enumerate(self.proc_root.iterdir()):
            if number>=8192:raise Stop('process_budget')
            if not p.name.isdigit():continue
            try:comm=read_small(p/'comm')
            except FileNotFoundError:continue
            if comm in {'hostapd','iwd','tcpdump','dumpcap','tshark','airodump-ng','hcxdumptool','kismet'}:
                raise Stop('competing_process')
            if comm=='bettercap' and int(p.name)!=owner[0][0]:raise Stop('other_bettercap')
            if comm=='wpa_supplicant':
                if '/wpa_supplicant.service'not in read_small(p/'cgroup'):raise Stop('foreign_supplicant')
                known.append(self._pid(int(p.name)))
        if len(known)!=1:raise Stop('supplicant_owner')
        self._packets(owner[0][0],rows['wlan0'][2],
                      self.receiver.inode if self.receiver else None, rows[self.iface][2])
        if (Path('/run/wpa_supplicant')/self.iface).exists():raise Stop('supplicant_socket')
        self.pin_check(identity)
        mode=dev[self.iface].get('type','unknown')
        protected=(rows['wlan0'],primary_info,primary_nm,ethernet,owner,tuple(known),country)
        self.pinned=identity
        self.baseline_protected=self.baseline_protected or protected
        return Observation(identity,protected,mode,bool(rows[self.iface][5]&1),rows[self.iface][4],
                           int(freq[1]) if freq else None,True,True)

    def _mutate(self, name, args):
        if (not self.execution_approved or not self.consumed or name in self.used or self.pinned is None
                or self._commands().get(name)!=args):
            raise Stop('mutation_denied')
        if name in ('down','managed') and self.receiver is not None:
            raise Stop('cleanup_socket_open')
        self.pin_check(self.pinned)
        # Full gate immediately before every command. Cleanup may follow a live
        # rotation, but must never act through changed physical/NM ownership.
        observed=self.observe()
        if not observed.nm_unmanaged or not observed.safe:raise Stop('cleanup_ownership')
        if name not in ('down','managed') and observed.protected!=self.baseline_protected:
            raise Stop('protected_changed')
        self.used.add(name)  # before a potentially partial command
        self._pending_command=tuple(args)
        try:
            self._run(args,changing=True)
        finally:
            self._pending_command=None
        self.pin_check(self.pinned)

    def _commands(self):
        return {
            'monitor':['iw','dev',self.iface,'set','type','monitor'],
            'passive_flags':['iw','dev',self.iface,'set','monitor','none'],
            'channel_2412':['iw','dev',self.iface,'set','freq','2412','HT20'],
            'up':['ip','link','set','dev',self.iface,'up'],
            'down':['ip','link','set','dev',self.iface,'down'],
            'managed':['iw','dev',self.iface,'set','type','managed']}

    def change(self, name):
        commands=self._commands()
        phases={'monitor':set(),'passive_flags':{'monitor'},'channel_2412':{'monitor','passive_flags'},
                'up':{'monitor','passive_flags','channel_2412'}}
        if name not in phases or self.used!=phases[name]:raise Stop('phase_denied')
        current=self.observe()
        if current.up or current.mode!=('managed' if name=='monitor' else 'monitor'):
            raise Stop('phase_state')
        self._mutate(name,commands[name])

    def cleanup(self,baseline):
        current=self.observe()
        if current.identity!=baseline.identity or not current.nm_unmanaged:raise Stop('cleanup_identity')
        if current.mode not in ('monitor','managed'):raise Stop('cleanup_mode')
        if current.up:
            self._mutate('down',['ip','link','set','dev',self.iface,'down'])
        current=self.observe()
        if current.up:raise Stop('cleanup_down')
        if current.mode!='managed':
            self._mutate('managed',['iw','dev',self.iface,'set','type','managed'])

    def remaining(self):
        """One planned read-only AFTER snapshot; never retries a mutation."""
        value={'identity_verified':None,'adapter_mode':'unknown','up':None,
               'arphrd':None,'nm_unmanaged':None}
        try:
            identity,rows=self._identity()
            value['identity_verified']=identity==self.pinned
            if not value['identity_verified']:
                return value
            value['up']=bool(rows[self.iface][5]&1)
            value['arphrd']=rows[self.iface][4]
            dev=iw_interfaces(self._run(['iw','dev']))
            mode=dev.get(self.iface,{}).get('type')
            value['adapter_mode']=mode if mode in ('managed','monitor') else 'unknown'
            nm=self._run(['nmcli','-g','GENERAL.NM-MANAGED,GENERAL.STATE','device','show',self.iface])
            if re.fullmatch(r'no\n10(?: \([^\n]*\))?\s*',nm):value['nm_unmanaged']=True
            elif re.fullmatch(r'yes\n[0-9]+(?: \([^\n]*\))?\s*',nm):value['nm_unmanaged']=False
            if self._identity()[0]!=self.pinned:
                return dict(identity_verified=False,adapter_mode='unknown',up=None,arphrd=None,nm_unmanaged=None)
        except (OSError,ValueError,KeyError,Stop,Unavailable):
            value['identity_verified']=None
        return value

    def open_receiver(self):
        if not self.execution_approved or self.used!={'monitor','passive_flags','channel_2412','up'}:
            raise Stop('receive_denied')
        self.pin_check(self.pinned)
        self.receiver=Receiver(self)
        return self.receiver


class Receiver:
    """Receive-only facade. Protocol zero until the pinned-name bind completes."""
    def __init__(self,ops):
        self.ops=ops;self.sock=None;self.inode=None;self.close_attempted=False
        ops.receiver=self
        try:
            s=self.sock=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,0)
            self.inode=os.fstat(s.fileno()).st_ino
            s.setsockopt(263,23,1)  # SOL_PACKET / PACKET_IGNORE_OUTGOING, Linux
            s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,262144)
            ops.pin_check(ops.pinned)
            s.bind((ops.iface,3))  # Python AF_PACKET protocol is host order
            ops.pin_check(ops.pinned)
            if s.getsockname()[0]!=ops.iface:raise Stop('socket_binding')
        except BaseException:
            self.close();raise

    def receive(self,timeout):
        self.sock.settimeout(max(.001,min(.1,timeout)))
        try:data,ancillary,flags,address=self.sock.recvmsg(8192,0)
        except socket.timeout:return None
        if len(address)<4 or address[0]!=self.ops.iface:raise Stop('packet_identity')
        return data,address[2] in (0,1,2,3),bool(flags&socket.MSG_TRUNC),address[3]

    def close(self):
        if self.close_attempted and self.sock is not None:
            raise Stop('socket_close_unverified')
        self.close_attempted=True
        if self.sock is not None:
            self.sock.close()
            if self.sock.fileno()!=-1:raise Stop('socket_close')
            self.sock=None
        self.ops.receiver=None


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iface',required=True)
    p.add_argument('--management-peer',required=True)
    p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--execute-approved',action='store_true')
    a=p.parse_args(argv)
    # No network, socket or hardware access merely for import/help.
    if os.geteuid()!=0:
        print(json.dumps({'failure':'root_required','qualified':False,'restored':False}));return 3
    lock=None
    handlers={}
    ops=None
    def interrupted(signum, frame):
        if signum==signal.SIGALRM and ops is not None and ops.window_end is not None and time.monotonic()>=ops.window_end:
            raise WindowExpired('receive_window_expired')
        raise Stop('interrupted')
    try:
        for signum in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGALRM):
            handlers[signum]=signal.signal(signum,interrupted)
        lock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        lock.bind('\0redux-awus036acm-passive-v1')
        ops=AdapterOps(a.iface,a.management_peer,execution_approved=a.execute_approved)
        ops.arm_alarm=True
        result=run_once(ops,approved=a.execute_approved,seconds=a.seconds)
    except (OSError,ValueError,Stop,Unavailable):
        result={'failure':'preflight_unavailable','qualified':False,'restored':False}
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        for signum,handler in handlers.items():signal.signal(signum,handler)
        if lock is not None:lock.close()
    print(json.dumps(result,sort_keys=True))
    return 0 if result.get('qualified') else 2 if result.get('restored') else 3


if __name__=='__main__':
    raise SystemExit(main())
