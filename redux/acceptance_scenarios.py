"""Deterministic synthetic acceptance inputs, never hardware observations.

No packet bytes, credentials, MACs, SSIDs, paths to captures or signing keys.
The public replay command always retains synthetic provenance.
"""
from copy import deepcopy
from .hardware_acceptance import FORMAT

NOW = 1800000000.0
TARGET = dict(revision='a'*40, release_sha256='b'*64, device_id='d'*64, model='pi4', profile='ab')
MOUNTS = ('1 0 0:1 / / rw - overlay overlay rw\n'
          '2 1 179:4 / /media/root-ro ro - ext4 /dev/mmcblk0p4 ro\n'
          '3 1 179:2 / /boot/firmware ro - vfat /dev/mmcblk0p2 ro\n'
          '5 1 179:1 / /boot/redux-control ro - vfat /dev/mmcblk0p1 ro\n'
          '4 1 179:6 / /captures rw - ext4 /dev/mmcblk0p6 rw\n')


def scan():return {'scanned':1,'completed_utc':NOW,'outcomes':{'ready':1}}


def scenario(name='healthy'):
    units = {unit:dict(LoadState='loaded',ActiveState='active',UnitFileState='enabled')
             for unit in ('redux.service','redux-live.service','redux-capture-ingest.timer')}
    units['bettercap.service']=dict(LoadState='masked',ActiveState='inactive',UnitFileState='masked')
    points=[dict(raw_x=x,raw_y=y,pixel_x=x,pixel_y=y) for x,y in ((0,0),(479,0),(0,319),(479,319),(239.5,159.5))]
    event=lambda at,boot,current,trial,action,slot,value:dict(at=NOW-at,boot_id=str(boot)*64,current=current,trial=trial,action=action,slot=slot,value=value)
    staged=event(90,2,'A',False,'set-primary','B',None)
    committed=event(60,3,'B',True,'set-state','B','good')
    failed=event(60,3,'B',True,'set-state','B','bad')
    fallback=event(30,4,'A',False,'set-state','A','good')
    base_update=dict(initial_slot='A',bundle_sha256='9'*64,captures_before='e'*64,captures_after='e'*64)
    data={
        'platform':dict(model='pi4',architecture='aarch64',firmware_sha256='f'*64,eeprom_sha256='c'*64),
        'storage':dict(mountinfo=MOUNTS,label_major_minor='179:6',boot_uid=999,boot_gid=999,boot_mode=0o700,redux_uid=999,redux_gid=999,
            current_slot='A',partitions=[dict(index=i,label=label,uuid=f'00000000-0000-0000-0000-{i:012d}',major_minor=f'179:{i}',disk_major_minor='179:0') for i,label in
                enumerate(('REDUXCTRL','REDUXBOOTA','REDUXBOOTB','REDUXROOTA','REDUXROOTB','REDUXCAP'),1)]),
        'services':dict(units=units,live_state='running',worker_scan=scan()),
        'radio':dict(arphrd=803,pcap_linktype=127,rx_before=10,rx_after=20,tx_before=0,tx_after=0,decoded_rx_frames=5,header_classification='data'),
        'processing':dict(handed_off=2,closed_artifacts=2,converted_artifacts=1,hash_records=1,worker_scan=scan()),
        'display':dict(framebuffer_width=480,framebuffer_height=320,width=480,height=320,rotation=0,points=points,
            observed_points=[dict(pixel_x=p['pixel_x'],pixel_y=p['pixel_y']) for p in points],navigation=['next','previous','select','back','scroll']),
        'resources':dict(samples=[dict(measured_at=NOW-delta,cpu_temp_c=55,cpu_load_pct=60,ram_pct=40,cpu_frequency_mhz=1500,throttled_mask=0) for delta in (30,0)]),
        'persistence':dict(before_boot='1'*64,after_boot='2'*64,before_storage='6'*64,after_storage='6'*64,before_inventory='e'*64,after_inventory='e'*64,before_count=2,after_count=2,service_observations=deepcopy(units),before_at=NOW-120,after_at=NOW-30),
        'update_install':dict(base_update,events=[staged]),
        'trial_boot':dict(base_update,events=[staged,committed]),
        'rollback':dict(base_update,events=[staged,failed,fallback]),
    }
    data['recovery']=dict(deepcopy(data['persistence']),medium_sha256=TARGET['release_sha256'],
        restored_revision=TARGET['revision'],restored_release_sha256=TARGET['release_sha256'],bundle_sha256='9'*64)
    if name=='no_radio':
        data.pop('radio');data['services']['live_state']='degraded'
    elif name=='missing_captures':data['storage']['mountinfo']='\n'.join(MOUNTS.splitlines()[:-1])
    elif name=='stale_sensors':
        for sample in data['resources']['samples']:sample['measured_at']-=1000
    elif name=='display_mapping':data['display']['rotation']=90
    elif name=='broken_processing':data['processing']['worker_scan']['outcomes']={'error':1}
    elif name=='ethernet_framing':data['radio'].update(arphrd=1,pcap_linktype=1)
    elif name=='interrupted_update':
        for gate in ('trial_boot','rollback'):data[gate]['events']=[staged]
        data.pop('recovery')
    elif name=='ambiguous_rollback':
        data['rollback']['events']=[staged,fallback];data.pop('recovery')
    elif name!='healthy':raise ValueError('unknown synthetic scenario')
    p=dict(format=FORMAT,target=deepcopy(TARGET),origin='synthetic',session_id='7'*64,
           collector_sha256='8'*64,observed_at=NOW,
           observations=[dict(gate=gate,target=deepcopy(TARGET),boot_id=values['events'][-1]['boot_id'] if 'events' in values else '2'*64,observed_at=NOW,data=values) for gate,values in data.items()])
    return {'payload':p}
