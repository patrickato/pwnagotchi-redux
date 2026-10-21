"""Synthetic integration contracts only; no D-Bus, radios or RF."""
from dataclasses import replace

import pytest

from redux.radio.orchestrator import Radio
from redux.radio.dual_radio import RadioEvidence
from redux.radio.split_integration import (
    MeasuredRadio, plan_measured_split, capture_lease, lease_present,
)
from redux.connectivity.bluez_pan import (
    PanPeer, plan_pan_connect, pan_management_ready, pairing_properties,
)
from redux.connectivity.phone import PhoneSession, PhonePolicy, PhoneIntent
from redux.radio.manager import RadioManager
import importlib
probe = importlib.import_module('redux.radio.probe')


def inventory():
    return [MeasuredRadio(Radio('wlan0', phy='phy0', monitor=True),
                          RadioEvidence('phy0', mode='managed', owner_verified=True,
                                        management_connected=True), 3, 'onboard', True, 'networkmanager'),
            MeasuredRadio(Radio('wlan1', phy='phy1', monitor=True, inject=True),
                          RadioEvidence('phy1', mode='monitor', owner_verified=True,
                                        arphrd=803, decoded_frames=4), 8, 'usb', True, 'capture_exclusive')]


def test_measured_split_uses_physical_bus_not_driver_or_name():
    radios = inventory()
    plan = plan_measured_split(radios)
    assert plan.state == 'receive_verified' and not plan.activation_authorized
    assert plan.capture_iface == 'wlan1'
    assert capture_lease(plan, radios).ifindex == 8
    for bad in ('unknown', 'pci', ''):
        assert plan_measured_split([radios[0], replace(radios[1], transport=bad)]).state == 'blocked'


@pytest.mark.parametrize('field,value', [('fresh', False), ('ifindex', True), ('ifindex', 0)])
def test_incomplete_measurements_block(field, value):
    radios = inventory()
    radios[1] = replace(radios[1], **{field: value})
    assert plan_measured_split(radios).state == 'blocked'


def test_lease_cannot_reuse_cached_ethernet_verification():
    radios = inventory()
    radios[0] = replace(radios[0], evidence=replace(radios[0].evidence, management_connected=False))
    plan = plan_measured_split(radios, ethernet_management_verified=True)
    assert plan.state == 'receive_verified'
    assert capture_lease(plan, radios) is None
    assert capture_lease(plan, radios, ethernet_management_verified=True)


def test_lease_removal_reuse_owner_loss_and_shared_phy_fail_closed():
    radios = inventory()
    lease = capture_lease(plan_measured_split(radios), radios)
    assert lease_present(lease, radios, management_verified=True)
    assert not lease_present(lease, radios[:1], management_verified=True)
    for changed in (replace(radios[1], ifindex=9),
                    replace(radios[1], evidence=replace(radios[1].evidence, owner_verified=False)),
                    replace(radios[1], evidence=replace(radios[1].evidence, arphrd=1))):
        assert not lease_present(lease, [radios[0], changed], management_verified=True)
    other = replace(radios[1], capabilities=replace(radios[1].capabilities, iface='wlan2'), ifindex=10)
    assert not lease_present(lease, [*radios, other], management_verified=True)
    assert not lease_present(lease, radios, management_verified='yes')


def test_duplicate_phy_is_not_collapsed_into_a_false_split():
    radios = inventory()
    radios[1] = replace(radios[1], capabilities=replace(radios[1].capabilities, phy='phy0'),
                        evidence=replace(radios[1].evidence, phy='phy0'))
    assert plan_measured_split(radios).state == 'blocked'


def peer():
    return PanPeer('/org/bluez/hci0/dev_00_11_22_33_44_55', True, True, True, True)


def test_pan_requires_explicit_enable_due_intent_and_authenticated_bond():
    p = peer()
    assert plan_pan_connect(p) is None
    request = plan_pan_connect(p, enabled=True, reconnect_due=True)
    assert request.method == 'Connect' and request.arguments == ('nap',) and request.timeout_s == 10
    assert '00_11' not in repr(request) and '00_11' not in repr(p)
    for key in ('paired', 'trusted', 'authenticated_bond', 'nap_advertised'):
        assert plan_pan_connect(replace(p, **{key: False}), enabled=True, reconnect_due=True) is None
    assert plan_pan_connect(replace(p, path='/untrusted'), enabled=True, reconnect_due=True) is None


def test_pan_outcome_and_route_must_both_be_verified():
    props = dict(Connected=True, Interface='bnep0', UUID='nap')
    assert pan_management_ready(peer(), 'bnep0', props, route_verified=True)
    assert not pan_management_ready(peer(), 'bnep0', props)
    for props_bad in (dict(props, Connected=False), dict(props, Interface='eth0'), dict(props, UUID='panu')):
        assert not pan_management_ready(peer(), 'bnep0', props_bad, route_verified=True)
    assert not pan_management_ready(peer(), 'wlan0', props, route_verified=True)


def test_pairing_prepares_timeouts_before_flags_and_closes_both():
    changes = pairing_properties(window_open=True, seconds_left=17)
    assert changes == (('PairableTimeout', 17), ('DiscoverableTimeout', 17),
                       ('Pairable', True), ('Discoverable', True))
    assert pairing_properties(window_open=False, seconds_left=0) == (
        ('Pairable', False), ('Discoverable', False))
    for bad in (0, 301, True, None):
        with pytest.raises(ValueError):
            pairing_properties(window_open=True, seconds_left=bad)


def reconnecting():
    p = PhoneSession(PhonePolicy(connect_timeout_s=10))
    p.enable(now=0)
    p.open_pairing(owner_authorized=True, now=1)
    p.pairing_result(authenticated=True, trusted=True, now=2)
    p.link_connected(authenticated=True, trusted=True, now=3)
    p.link_lost(now=4)
    assert p.tick(now=9) == (PhoneIntent.CONNECT_TRUSTED,)
    return p


def test_hung_reconnect_is_cancelled_and_backed_off_without_discovery():
    p = reconnecting()
    with pytest.raises(PermissionError):
        p.link_connected(authenticated=True, trusted=True, now=19)
    assert p.tick(now=19) == (PhoneIntent.DISCONNECT,)
    assert p.status(now=19)['phase'] == 'backoff'
    assert not p.status(now=19)['discoverable']
    assert p.tick(now=28) == ()
    assert p.tick(now=29) == (PhoneIntent.CONNECT_TRUSTED,)


def test_disable_cancels_pending_reconnect():
    assert reconnecting().disable(now=10) == (PhoneIntent.DISCONNECT, PhoneIntent.DISABLE_ADAPTER)


def test_signal_during_transition_marks_manager_dirty():
    def interrupted(action):
        raise KeyboardInterrupt
    manager = RadioManager([inventory()[0].capabilities], applier=interrupted)
    with pytest.raises(KeyboardInterrupt):
        manager.realize()
    assert manager.out_of_sync
    with pytest.raises(RuntimeError, match='re-probe'):
        manager.realize()


def test_usb_speed_walk_resolves_device_symlink_before_walking(tmp_path, monkeypatch):
    usb = tmp_path / 'usb-device'
    (usb / 'net' / 'wlan1').mkdir(parents=True)
    (usb / 'speed').write_text('5000\n')
    monkeypatch.setattr(probe.os.path, 'realpath', lambda _: str(usb / 'net' / 'wlan1'))
    assert probe._usb_gen_of('wlan1') == 3


def test_raw_capability_bridge_preserves_shared_phy_and_does_not_infer_ownership():
    from redux.radio.split_integration import bind_probe_observations
    phy = 'Wiphy phy0\n * monitor\n * 2412 MHz\nWiphy phy1\n * monitor\n * 5180 MHz\n'
    dev = ('phy#0\n Interface wlan0\n  ifindex 3\n  type managed\n'
           'phy#1\n Interface wlan1\n  ifindex 8\n  type monitor\n')
    metadata = {'wlan0': {'transport': 'onboard'}, 'wlan1': {'transport': 'usb', 'usb_gen': 3}}
    evidence = {m.capabilities.iface: m.evidence for m in inventory()}
    owners = {"wlan0": "networkmanager", "wlan1": "capture_exclusive"}
    measured = bind_probe_observations(phy, dev, metadata, evidence, owners=owners, fresh=True)
    assert measured[1].capabilities.bands == frozenset({'5'})
    assert measured[1].capabilities.inject is False
    assert plan_measured_split(measured).state == 'receive_verified'
    assert plan_measured_split(bind_probe_observations(phy, dev, metadata, {}, fresh=True)).state == 'blocked'
    extra = ' Interface wlan1mon\n  ifindex 9\n  type monitor\n'
    evidence['wlan1mon'] = evidence['wlan1']
    measured = bind_probe_observations(phy, dev + extra, metadata, evidence, fresh=True)
    assert len(measured) == 3  # never hide the extra VIF behind a preferred name
    assert plan_measured_split(measured).state == 'blocked'


@pytest.mark.parametrize('speed,expected', [('480', 2), ('5000', 3), ('nan', None),
                                           ('inf', None), ('-1', None), ('12', None)])
def test_usb_generation_requires_measured_supported_speed(tmp_path, monkeypatch, speed, expected):
    (tmp_path / 'speed').write_text(speed)
    monkeypatch.setattr(probe.os.path, 'realpath', lambda _: str(tmp_path))
    assert probe._usb_gen_of('wlan1') == expected


@pytest.mark.parametrize('field,value', [('monitor', 'yes'), ('onboard', 'yes'),
                                        ('phy', 1), ('iface', '../wlan1')])
def test_split_planner_rejects_truthy_or_malformed_radio_claims(field, value):
    from redux.radio.dual_radio import plan_dual_radio
    measured = inventory()
    radios = [m.capabilities for m in measured]
    radios[1] = replace(radios[1], **{field: value})
    assert plan_dual_radio(radios, {m.capabilities.iface: m.evidence for m in measured}).state == 'blocked'


def test_nm_owned_usb_cannot_qualify_as_exclusive_capture():
    radios = inventory()
    radios[1] = replace(radios[1], owner='networkmanager')
    plan = plan_measured_split(radios)
    assert plan.state == 'candidate'
    assert capture_lease(plan, radios) is None
    radios = inventory()
    lease = capture_lease(plan_measured_split(radios), radios)
    radios[1] = replace(radios[1], owner='networkmanager')
    assert not lease_present(lease, radios, management_verified=True)


@pytest.mark.parametrize('timeout', [0, -1, 121, True, None])
def test_reconnect_timeout_policy_is_finite_and_bounded(timeout):
    with pytest.raises(ValueError):
        PhonePolicy(connect_timeout_s=timeout)
