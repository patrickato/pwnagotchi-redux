"""Distributed cracking — split one crack job across the mesh.

A single box crawls a big wordlist or mask keyspace alone. With several redux
nodes on the swarm, the job can be sliced so each node works a disjoint slice in
parallel — near-linear speedup, and no slice is ever worked twice.

hashcat already supports exactly this, per node:
  * wordlist (``-a 0``): ``--skip <start> --limit <count>``
  * mask / keyspace (``-a 3``): ``-s <start> -l <count>`` over the keyspace
    (the total comes from ``hashcat --keyspace``)

This module is the pure, glass-box partitioner: given a total and a node count
it produces disjoint, exhaustive slices, and turns a base hashcat command into
one argv per node. It does not move bytes — dispatch rides the existing swarm
(``redux/mesh``). Real data only: slices always cover the whole space with no
gap and no overlap (a property the tests pin down).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Slice:
    node_index: int
    skip: int          # start offset (words, or keyspace units)
    limit: int         # how many to process (0 only if the space is empty)

    @property
    def end(self) -> int:
        return self.skip + self.limit


def partition(total: int, n_nodes: int) -> List[Slice]:
    """Split ``total`` units into ``n_nodes`` disjoint, contiguous, exhaustive
    slices. The remainder is spread over the first slices (so sizes differ by at
    most 1). Empty nodes (more nodes than units) get zero-length slices at the
    end — honest, not dropped, so a caller can see a node had nothing to do.
    """
    if n_nodes <= 0:
        raise ValueError("n_nodes must be >= 1")
    total = max(0, int(total))
    base, rem = divmod(total, n_nodes)
    out: List[Slice] = []
    start = 0
    for i in range(n_nodes):
        count = base + (1 if i < rem else 0)
        out.append(Slice(node_index=i, skip=start, limit=count))
        start += count
    return out


def _slice_args(attack: int, s: Slice) -> List[str]:
    """hashcat slice flags for an attack mode."""
    if attack == 3:                         # mask / keyspace
        return ["-s", str(s.skip), "-l", str(s.limit)]
    return ["--skip", str(s.skip), "--limit", str(s.limit)]   # wordlist/straight


@dataclass(frozen=True)
class NodeJob:
    node_index: int
    node_id: str
    argv: List[str]
    slice: Slice
    reason: str

    def to_dict(self) -> dict:
        return {"node_index": self.node_index, "node_id": self.node_id,
                "argv": list(self.argv), "skip": self.slice.skip,
                "limit": self.slice.limit, "reason": self.reason}


def distribute(base_argv: Sequence[str], total: int, *, attack: int = 0,
               node_ids: Optional[Sequence[str]] = None,
               n_nodes: Optional[int] = None) -> List[NodeJob]:
    """Turn one base hashcat argv into one argv per node, each carrying its
    disjoint slice. Provide either explicit ``node_ids`` or an ``n_nodes`` count.

    A zero-length slice (idle node) still produces a NodeJob with a reason, so
    the placement is fully visible rather than silently dropped.
    """
    if node_ids is not None:
        ids = list(node_ids)
        n = len(ids)
    elif n_nodes is not None:
        n = int(n_nodes)
        ids = [f"node{i}" for i in range(n)]
    else:
        raise ValueError("provide node_ids or n_nodes")
    if n <= 0:
        raise ValueError("need at least one node")

    slices = partition(total, n)
    jobs: List[NodeJob] = []
    for s, nid in zip(slices, ids):
        if s.limit <= 0:
            jobs.append(NodeJob(s.node_index, nid, [], s,
                                f"{nid}: idle — more nodes than work units"))
            continue
        argv = list(base_argv) + _slice_args(attack, s)
        jobs.append(NodeJob(
            s.node_index, nid, argv, s,
            f"{nid}: units [{s.skip}, {s.end}) of {total} "
            f"({s.limit}, {100.0 * s.limit / total:.0f}%)"))
    return jobs


def count_lines(path) -> int:
    """Count candidate lines in a wordlist (for --skip/--limit partitioning).
    Counts real newlines; a trailing non-terminated line still counts."""
    p = Path(path)
    n = 0
    with open(p, "rb") as f:
        trailing = False
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += chunk.count(b"\n")
            trailing = not chunk.endswith(b"\n")
        if trailing:
            n += 1
    return n


def collect_results(found_texts: Sequence[str]) -> List[str]:
    """Merge recovered-key output from several nodes into a de-duped list,
    preserving first-seen order. Each input is a node's --outfile contents."""
    seen = set()
    out: List[str] = []
    for text in found_texts:
        for line in (text or "").splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                out.append(line)
    return out
