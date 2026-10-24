# Distributed cracking (`redux crack-distribute`)

One box crawls a big wordlist or mask keyspace alone. With several redux nodes on
the swarm, the job can be **sliced** so each node works a disjoint slice in
parallel — near-linear speedup, and no slice is ever worked twice.

hashcat already supports this per node:

- **wordlist** (`-a 0`): `--skip <start> --limit <count>`
- **mask / keyspace** (`-a 3`): `-s <start> -l <count>` over the keyspace
  (total from `hashcat -m <m> -a 3 <mask> --keyspace`)

`redux crack-distribute` is the glass-box partitioner that turns one job into one
command per node.

```
$ redux crack-distribute hashes.22000 --mode 22000 --attack 0 --wordlist rockyou.txt --nodes 3
splitting 14344391 units across 3 node(s):
  [node0] units [0, 4781464) of 14344391 (33%)
      hashcat -m 22000 -a 0 hashes.22000 rockyou.txt --skip 0 --limit 4781464
  [node1] units [4781464, 9562928) ...
  [node2] units [9562928, 14344391) ...
```

```
$ redux crack-distribute h.22000 --mode 22000 --attack 3 --mask '?d?d?d?d?d?d?d?d' --keyspace 100000000 --nodes 2
  [node0] -s 0 -l 50000000
  [node1] -s 50000000 -l 50000000
```

## Guarantees

- **Disjoint + exhaustive**: the slices cover exactly `[0, total)` with no gap
  and no overlap (pinned by a property test across many totals/node counts).
- **Balanced**: slice sizes differ by at most 1; the remainder is spread over the
  first slices.
- **Honest about idle nodes**: more nodes than work units produces zero-length
  slices with a reason, rather than silently dropping them.

## Collecting results

`collect_results([...])` merges each node's `--outfile` contents into a
de-duplicated list (first-seen order), so the recovered keys from every node fold
into one answer.

## Scope

This module is the **pure partitioner** (`redux/crack/distributed.py`); it moves
no bytes. Dispatch rides the existing swarm (`redux/mesh`), and every worker
re-gates the target against its own local Scope at execution time — distribution
decides *where compute runs*, never *what is authorized*.
