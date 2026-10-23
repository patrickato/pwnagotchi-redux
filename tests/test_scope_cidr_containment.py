"""The CIDR scope bypass is closed: a requested network must be CONTAINED in scope."""
import pytest

from redux.core.scope import Scope
from redux.netrecon import recon_subnet, ReconRefused, ReplayScanner


def _armed(*cidrs):
    s = Scope()
    s.arm_lab(cidrs=list(cidrs))
    return s


def test_broad_cidr_whose_first_address_is_in_scope_is_refused():
    s = _armed("192.168.1.0/24")
    # the exact bypass: /8 whose network address (192.168.1.0) sits inside the /24
    with pytest.raises(ReconRefused, match="not contained"):
        recon_subnet("192.168.1.0/8", scope=s, scanner=ReplayScanner())


def test_armed_network_and_subranges_are_allowed():
    s = _armed("10.0.0.0/16")
    assert recon_subnet("10.0.0.0/16", scope=s, scanner=ReplayScanner()).available
    assert recon_subnet("10.0.5.0/24", scope=s, scanner=ReplayScanner()).available  # sub-range


def test_authorize_network_containment_semantics():
    s = _armed("192.168.1.0/24")
    assert s.authorize_network("192.168.1.0/8")[0] is False      # broader -> refused
    assert s.authorize_network("192.168.1.128/25")[0] is True    # sub-range -> ok
    assert s.authorize_network("192.168.1.42")[0] is True        # single host /32 -> ok
    assert s.authorize_network("10.0.0.0/8")[0] is False         # unrelated -> refused
    assert s.authorize_network("not-a-network")[0] is False      # garbage -> refused


def test_empty_scope_refuses_any_network():
    assert Scope().authorize_network("10.0.0.0/24")[0] is False


def test_operator_cidr_target_requires_containment():
    from redux.operator.operator import Operator
    op = Operator(_armed("172.16.0.0/16"), offense_enabled=True)
    ok, _ = op._authorized("172.16.0.0/8")      # broad range starting in scope
    assert ok is False
    ok, _ = op._authorized("172.16.9.0/24")     # sub-range
    assert ok is True
