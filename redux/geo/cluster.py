"""DBSCAN-lite clustering of lat/lon points (AP hotspot finding)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from redux.geo.geoutil import haversine_m

Point = Tuple[float, float]  # lat, lon


@dataclass(frozen=True)
class Cluster:
    points: tuple[Point, ...]
    centroid_lat: float
    centroid_lon: float
    size: int


def _neighbors(i: int, points: Sequence[Point], eps_m: float) -> List[int]:
    lat, lon = points[i]
    return [
        j
        for j, (la, lo) in enumerate(points)
        if haversine_m(lat, lon, la, lo) <= eps_m
    ]


def dbscan_lite(
    points: Sequence[Point],
    *,
    eps_m: float = 50.0,
    min_samples: int = 3,
) -> List[Cluster]:
    """Cluster points within eps_m; noise points are dropped (not returned)."""
    n = len(points)
    if n == 0:
        return []
    labels = [-1] * n  # -1 noise, >=0 cluster id
    cluster_id = 0
    visited = [False] * n

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        neigh = _neighbors(i, points, eps_m)
        if len(neigh) < min_samples:
            continue
        labels[i] = cluster_id
        seeds = [j for j in neigh if j != i]
        k = 0
        while k < len(seeds):
            j = seeds[k]
            if not visited[j]:
                visited[j] = True
                n2 = _neighbors(j, points, eps_m)
                if len(n2) >= min_samples:
                    for x in n2:
                        if x not in seeds:
                            seeds.append(x)
            if labels[j] == -1:
                labels[j] = cluster_id
            k += 1
        cluster_id += 1

    out: List[Cluster] = []
    for cid in range(cluster_id):
        members = [points[i] for i, lab in enumerate(labels) if lab == cid]
        if not members:
            continue
        clat = sum(p[0] for p in members) / len(members)
        clon = sum(p[1] for p in members) / len(members)
        out.append(
            Cluster(
                points=tuple(members),
                centroid_lat=clat,
                centroid_lon=clon,
                size=len(members),
            )
        )
    return out
