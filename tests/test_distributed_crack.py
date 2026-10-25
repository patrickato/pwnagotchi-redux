"""Distributed cracking: disjoint/exhaustive keyspace partitioning + per-node argv."""
import pytest

from redux.crack.distributed import (
    partition, distribute, count_lines, collect_results, Slice,
)


# ---- partition: the core invariant -------------------------------------- #

def test_partition_is_disjoint_and_exhaustive():
    for total in (0, 1, 7, 100, 1000, 999983):
        for n in (1, 2, 3, 8, 16):
            slices = partition(total, n)
            assert len(slices) == n
            # exhaustive: covers exactly [0, total) with no gap/overlap
            cursor = 0
            for s in slices:
                assert s.skip == cursor
                cursor += s.limit
            assert cursor == total
            # balanced: sizes differ by at most 1
            sizes = [s.limit for s in slices]
            assert max(sizes) - min(sizes) <= 1


def test_partition_more_nodes_than_work_gives_idle_slices():
    slices = partition(3, 5)
    assert [s.limit for s in slices] == [1, 1, 1, 0, 0]


def test_partition_rejects_bad_node_count():
    with pytest.raises(ValueError):
        partition(10, 0)


# ---- distribute: per-node commands -------------------------------------- #

def test_distribute_wordlist_uses_skip_limit():
    base = ["hashcat", "-m", "22000", "-a", "0", "h.22000", "rockyou.txt"]
    jobs = distribute(base, total=1000, attack=0, n_nodes=4)
    assert len(jobs) == 4
    j0 = jobs[0]
    assert "--skip" in j0.argv and "--limit" in j0.argv
    assert j0.argv[j0.argv.index("--skip") + 1] == "0"
    assert j0.argv[j0.argv.index("--limit") + 1] == "250"
    # second node starts where the first ended
    j1 = jobs[1]
    assert j1.argv[j1.argv.index("--skip") + 1] == "250"


def test_distribute_mask_uses_s_l():
    base = ["hashcat", "-m", "22000", "-a", "3", "h.22000", "?d?d?d?d?d?d?d?d"]
    jobs = distribute(base, total=100_000_000, attack=3, n_nodes=2)
    assert "-s" in jobs[0].argv and "-l" in jobs[0].argv
    assert jobs[0].argv[jobs[0].argv.index("-s") + 1] == "0"
    assert jobs[1].argv[jobs[1].argv.index("-s") + 1] == "50000000"


def test_distribute_with_named_nodes():
    jobs = distribute(["hashcat"], total=10, attack=0, node_ids=["pi-a", "pi-b"])
    assert [j.node_id for j in jobs] == ["pi-a", "pi-b"]
    assert all(str(5) in j.reason for j in jobs)   # 5 each


def test_distribute_idle_node_has_empty_argv_and_reason():
    jobs = distribute(["hashcat"], total=1, attack=0, n_nodes=3)
    assert jobs[0].argv and jobs[0].slice.limit == 1
    assert jobs[1].argv == [] and "idle" in jobs[1].reason
    assert jobs[2].argv == [] and "idle" in jobs[2].reason


def test_distribute_requires_nodes():
    with pytest.raises(ValueError):
        distribute(["hashcat"], total=10)


# ---- helpers ------------------------------------------------------------- #

def test_count_lines(tmp_path):
    p = tmp_path / "wl.txt"
    p.write_text("a\nb\nc\n")
    assert count_lines(p) == 3
    p.write_text("a\nb\nc")               # no trailing newline
    assert count_lines(p) == 3
    p.write_text("")
    assert count_lines(p) == 0


def test_collect_results_dedups_preserving_order():
    node_a = "hashAAAA:password1\nhashBBBB:letmein\n"
    node_b = "hashBBBB:letmein\nhashCCCC:hunter2\n"   # BBBB duplicated
    merged = collect_results([node_a, node_b])
    assert merged == ["hashAAAA:password1", "hashBBBB:letmein", "hashCCCC:hunter2"]
