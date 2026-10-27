"""Phone UI over Bluetooth PAN — bring-up plan, readiness, gated execute."""
from redux.web.btpan import (
    PanBringup, bring_up_plan, dashboard_url, DEFAULT_IP, DEFAULT_PORT,
)


def test_dashboard_url():
    assert dashboard_url() == "http://172.20.10.1:8080/"
    assert dashboard_url("10.0.0.1", 9000) == "http://10.0.0.1:9000/"


def test_plan_is_glassbox_and_serves_the_dashboard():
    plan = bring_up_plan()
    joined = " ".join(plan.steps)
    assert "bt-network -s nap pan0" in joined          # registers the NAP
    assert "ip addr add 172.20.10.1/24 dev pan0" in joined
    assert "dnsmasq" in joined and "172.20.10.10,172.20.10.50" in joined
    assert "redux web" in joined                        # the dashboard, on the PAN
    assert plan.dashboard_url == "http://172.20.10.1:8080/"


def test_readiness_needs_a_bluetooth_adapter():
    b = PanBringup()
    assert b.readiness(adapter_present=False)[0] == "needs-hardware"
    assert b.readiness(adapter_present=True)[0] == "ready"


def test_up_is_dry_run_by_default():
    res = PanBringup().up()                             # no apply
    assert res["applied"] is False and "DRY RUN" in res["note"]
    assert res["dashboard_url"] == "http://172.20.10.1:8080/"


def test_up_refuses_without_adapter():
    res = PanBringup().up(apply=True, adapter_present=False)
    assert res["applied"] is False and "no Bluetooth adapter" in res["note"]


def test_up_runs_the_plan_when_applied():
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        import subprocess
        return subprocess.CompletedProcess(argv, 0, "", "")
    res = PanBringup(runner=runner).up(apply=True, adapter_present=True)
    assert res["applied"] is True
    assert all(r["ok"] for r in res["results"])
    assert ["bt-network", "-s", "nap", "pan0"] in calls
    assert "172.20.10.1:8080" in res["dashboard_url"]


def test_up_reports_a_failed_step_without_crashing():
    def runner(argv, **kw):
        import subprocess
        rc = 1 if argv[0] == "dnsmasq" else 0
        return subprocess.CompletedProcess(argv, rc, "", "boom")
    res = PanBringup(runner=runner).up(apply=True, adapter_present=True)
    assert res["applied"] is True
    assert any(not r["ok"] for r in res["results"])     # the failed step is recorded


def test_custom_subnet_and_port_flow_through():
    b = PanBringup(ip="10.1.2.1", cidr="10.1.2.0/24", port=9090)
    plan = b.plan()
    assert plan.dashboard_url == "http://10.1.2.1:9090/"
    assert "10.1.2.10,10.1.2.50" in " ".join(plan.steps)
