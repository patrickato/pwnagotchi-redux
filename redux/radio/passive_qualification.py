"""One explicitly approved AWUS036ACM qualification; no live lease issuance.

Only a previously isolated unmanaged managed/DOWN adapter is accepted. The
backend never changes NM, services, onboard radio or persistent configuration.
"""
from dataclasses import dataclass, field
import math
import time

from .frame_headers import validate_header
from .local_evidence import Unavailable


class Stop(RuntimeError):
    pass


class WindowExpired(Stop):
    pass


@dataclass(frozen=True)
class Observation:
    identity: tuple = field(repr=False)
    protected: tuple = field(repr=False)  # Ethernet/US/onboard/runtime/process owner
    mode: str = 'unknown'
    up: bool = False
    arphrd: int = 0
    frequency: int | None = None
    nm_unmanaged: bool = False
    safe: bool = False


def run_once(ops, *, approved=False, seconds=30, clock=time.monotonic):
    """Bounded main/cleanup phases, one attempt and exact baseline restoration.

    Exceptions/interrupts close the receive socket BEFORE identity-pinned cleanup.
    A command may partially apply: intent is recorded before calling the backend.
    Unknown identity/ownership refuses mutations; no guessed recovery or retry.
    """
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError('receive window must be finite and at most 60 seconds')
    result = dict(format='redux-awus036acm-passive-v1', before='UNKNOWN', test='NOT_RUN',
                  after='UNKNOWN', restored=False, qualified=False, counts={}, failure=None, receive_limit_seconds=seconds,
                  restoration_scope='preisolated_adapter_and_protected_runtime', original_nm_return_verified=None)
    if getattr(ops, 'consumed', False):
        result['failure']='attempt_consumed'
        return result
    baseline, changed, receiver = None, False, None
    closed, close_attempted = True, False
    start = previous = clock()
    def now():
        nonlocal previous
        current = clock()
        if not math.isfinite(current) or current < previous:
            raise Stop('clock_invalid')
        previous = current
        return current
    try:
        now()
        ops.deadline(start + 120)
        baseline = ops.observe()
        if not (baseline.safe is True and baseline.nm_unmanaged is True and baseline.mode == 'managed'
                and baseline.up is False and baseline.arphrd == 1 and baseline.frequency is None):
            raise Stop('baseline_gate')
        result['before']='PASS'
        if not approved:
            return result
        ops.consumed = True  # before first potentially partial mutation
        link_type_expected=None
        def guard(mode, up, *, frequency=None):
            if now() >= start + 120:
                raise Stop('main_deadline')
            observed=ops.observe()
            result['last_gate'] = dict(identity_preserved=observed.identity==baseline.identity,
                protected_preserved=observed.protected==baseline.protected,
                nm_excluded=observed.nm_unmanaged is True, mode_expected=observed.mode==mode,
                up_expected=observed.up is up, frequency_expected=frequency is None or observed.frequency==frequency,
                link_expected=link_type_expected is None or observed.arphrd==link_type_expected,
                arphrd=observed.arphrd)
            if (observed.identity != baseline.identity or observed.protected != baseline.protected
                    or observed.safe is not True or observed.nm_unmanaged is not True or observed.mode != mode
                    or observed.up is not up or link_type_expected is not None and observed.arphrd!=link_type_expected
                    or frequency is not None and observed.frequency != frequency):
                raise Stop('observation_gate')
            return observed
        guard('managed', False)
        changed=True
        ops.change('monitor')
        guard('monitor', False)
        ops.change('passive_flags')
        guard('monitor', False)
        ops.change('channel_2412')
        guard('monitor', False, frequency=2412)
        ops.change('up')
        active=guard('monitor', True, frequency=2412)
        if active.arphrd not in (801,803):
            raise Stop('native_link_gate')
        result['link_type']=active.arphrd
        link_type_expected=active.arphrd
        sample_start=now()
        end=min(sample_start+seconds-min(.1,seconds/2),start+120)
        ops.receive_deadline(end)
        receiver=ops.open_receiver()
        closed=False
        guard('monitor', True, frequency=2412)
        counts={k:0 for k in ('management','control','data','invalid','unsupported','outgoing','truncated')}
        result['counts']=counts
        received=total_bytes=0
        last_guard=now()
        try:
            while now() < end:
                # Identity pinned per receive; full ownership/runtime gate <=1s old.
                ops.pin_check(baseline.identity)
                if now()-last_guard >= 1:
                    guard('monitor',True,frequency=2412)
                    last_guard=now()
                item=receiver.receive(min(.1,max(0,end-now())))
                if item is None:
                    continue
                data, incoming, truncated, link_type = item
                ops.pin_check(baseline.identity)
                if now() > end:
                    break  # late results cannot qualify
                received+=1;total_bytes+=len(data)
                if received > 20000 or total_bytes > 32*1024*1024:
                    raise Stop('receive_budget')
                if incoming is not True:
                    counts['outgoing']+=1
                    continue
                if truncated:
                    counts['truncated']+=1
                    continue
                if link_type != active.arphrd:
                    raise Stop('packet_link_changed')
                label=validate_header(data,active.arphrd)
                counts[label if label in ('management','control','data') else
                       'unsupported' if label.startswith('unsupported') else 'invalid']+=1
        except WindowExpired:
            pass
        close_attempted=True
        receiver.close()
        closed=True
        ops.receive_deadline(None)
        result['receive_elapsed_seconds']=round(now()-sample_start,6)
        if now()-sample_start>seconds:
            raise Stop('receive_deadline_overrun')
        guard('monitor',True,frequency=2412)
        result['counts']=counts
        result['test']='PASS' if sum(counts[k] for k in ('management','control','data')) else 'UNKNOWN'
    except BaseException as exc:
        # No raw exception text, packet data, private interface or USB identifier.
        known={'clock_invalid','main_deadline','baseline_gate','observation_gate',
               'native_link_gate','receive_budget','packet_link_changed','receive_deadline_overrun'}
        known.update({
            'adapter_driver', 'adapter_model', 'cleanup_down', 'cleanup_identity', 'cleanup_mode',
            'cleanup_ownership', 'command_denied', 'competing_process', 'country', 'deadline',
            'ethernet', 'fd_budget', 'foreign_packet_owner', 'foreign_supplicant', 'identity_changed',
            'interrupted', 'iw_identity', 'iw_inventory', 'monitor_not_advertised', 'mutation_denied',
            'nm_exclusion', 'onboard_driver', 'onboard_nm', 'onboard_state', 'other_bettercap',
            'packet_identity', 'packet_inventory', 'phase_denied', 'phase_state', 'pid_unknown',
            'platform', 'process_budget', 'protected_changed', 'radio_inventory', 'radio_transport',
            'receive_denied', 'runtime_binding', 'runtime_config_path', 'runtime_config_untrusted',
            'runtime_inactive', 'runtime_not_pinned', 'runtime_owner', 'runtime_uid',
            'runtime_untrusted', 'runtime_unverified', 'socket_binding', 'socket_close',
            'supervisor_command', 'supplicant_binding', 'supplicant_owner', 'supplicant_socket',
            'usb_identity'
        })
        known.update({'command_timeout','command_overflow','command_unavailable','command_failed','command_denied','cleanup_socket_open','socket_close_unverified'})
        result['failure']=str(exc) if isinstance(exc,(Stop,Unavailable)) and str(exc) in known else 'operation_stopped'
        result['test']='FAIL' if approved and changed else 'NOT_RUN'
    finally:
        # Constructor failure can still leave an unverified live socket. Never
        # assume that failure to return a receiver proves there is no resource.
        if receiver is None and getattr(ops,'receiver',None) is not None:
            receiver=ops.receiver
            close_attempted=getattr(receiver,'close_attempted',False)
            closed=False
            if close_attempted:
                result['failure']='socket_close_unverified'
        if receiver is not None and not close_attempted:
            try:
                close_attempted=True
                receiver.close()
                closed=True
            except BaseException:
                closed=False
                result['failure']='socket_close_unverified'
        if receiver is not None and close_attempted and not closed:
            result['failure']='socket_close_unverified'
        if baseline is not None and result['before']=='PASS':
            try:
                # Cleanup has its OWN bounded budget; it never changes managers.
                ops.deadline(now()+20)
                if not closed:
                    raise Stop('close_failed')
                if changed:
                    # Backend independently pins identity/ownership immediately
                    # before EACH command, including after a partial failure.
                    ops.cleanup(baseline)
                after=ops.observe()
                result['after_checks']=dict(identity_unchanged=after.identity==baseline.identity,
                    protected_unchanged=after.protected==baseline.protected, mode_restored=after.mode==baseline.mode,
                    up_restored=after.up is baseline.up, link_restored=after.arphrd==baseline.arphrd,
                    frequency_restored=after.frequency==baseline.frequency, nm_exclusion_preserved=after.nm_unmanaged is True)
                result['restored']=(after == baseline)
                result['after']='PASS' if result['restored'] else 'FAIL'
            except BaseException:
                result['after']='UNKNOWN'
                result['restored']=False
                if hasattr(ops,'remaining'):
                    try:
                        result['remaining']=ops.remaining()
                    except BaseException:
                        result['remaining']={'identity_verified':None,'adapter_mode':'unknown',
                                             'up':None,'arphrd':None,'nm_unmanaged':None}
    result['qualified']=(result['test']=='PASS' and result['restored'] is True and result['failure'] is None)
    return result
