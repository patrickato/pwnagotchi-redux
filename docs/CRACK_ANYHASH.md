# Cracking any hash, not just Wi-Fi (`redux crack`)

The live pipeline (`redux pipeline audit`, `redux/crack/audit.py`) only ever runs
hashcat in **one** mode — WPA/WPA2 (`-m 22000`). That is correct for what the
device pulls off the air, but it means the box couldn't help with the *other*
hashes you end up holding on an authorized engagement: an NTLM dump, a
kerberoast ticket, a Linux `/etc/shadow` line, a password-protected archive, an
SSH key.

`redux crack` is a **guided, any-hash front-end** to hashcat. It turns hashcat's
~400 numeric `-m` modes into a small set of named, categorized **recipes** with
smart defaults, so you pick `ntlm` or `linux-shadow` instead of memorizing mode
numbers — and it picks a sensible attack + wordlist + rules for that hash type.

## The gate (same contract as everything else)

Cracking here operates on a **hash file you hand it** — possession is the
authorization, there is no airborne target to Scope-check. To stay true to
"gated by default, turnkey in LAB":

- **FIELD** (shipped default): `redux crack run …` requires `--authorized` — a
  deliberate "yes, this is mine to crack".
- **LAB**: unlocked. It runs immediately, no flag.

`plan`, `id`, and `modes` never fire anything, so they're always open.

## The four verbs

```
redux crack modes [query] [--category windows]   # browse/search the catalog
redux crack id <hashfile>                         # identify a sample, suggest mode + options
redux crack plan <hashfile> --type <slug> [opts]  # show the EXACT hashcat command (glass-box)
redux crack run  <hashfile> --type <slug> [opts]  # run it (gated; unlocked in LAB)
```

### `id` — "what is this, and how should I crack it?"

```
$ redux crack id creds.txt
sample: $krb5tgs$23$*svc_sql$CORP.LOCAL*$a1b2c3...

likely hash types (best first):
  --type kerberoast    -m 13100  Kerberos TGS-REP (kerberoasting)
       suggest: attack 0 + 'best64' rules — kerberoast is fast …
```

It reads the first hash line, recognizes the shape (prefix like `$6$`, `$2y$`,
`$krb5tgs$`; NetNTLMv2's `user::DOMAIN:…`; raw-hex lengths), and for each
candidate prints the recommended attack/rules **with the reason**. 32-hex is
reported as ambiguous (NTLM *and* MD5) so you can try both.

### The "help me pick" smarts

The core heuristic is **cost**. Fast hashes (raw digests, NTLM, kerberoast) can
afford a rule set that multiplies the wordlist many-fold, so the suggestion is
`straight + best64`. Slow KDFs (bcrypt, sha512crypt, 7-Zip, VeraCrypt) are
flagged `slow` and the suggestion drops rules — a straight run of a small,
targeted list is the only sane choice. `plan`/`run` apply that suggestion as the
default when you don't override `--attack`/`--rules`.

### Options you can set per crack

| option | meaning |
|---|---|
| `--type <slug\|-m>` | hash type (see `crack modes`) or a bare hashcat mode number |
| `--attack straight\|combinator\|mask\|hybrid-wm\|hybrid-mw` | attack mode (default: the recipe's own) |
| `--wordlist <name>` | a registered wordlist (else your persisted `redux wordlist select` pick) |
| `--wordlist-path <abs>` | use a wordlist path directly |
| `--rules <preset\|path>` | rule preset (`best64`, `dive`, `onerule`, …) or a `.rule` file — fast hashes only |
| `--mask <preset\|?-mask>` | for mask/hybrid attacks (`digits8`, `?l?l?d?d`, …) |

Rule presets resolve against `/captures/rules`, then the distro hashcat rules
dirs; a preset that isn't on disk is skipped with a reason rather than emitting a
broken `-r` path. Wordlists come from the existing registry
(`redux wordlist add/pull/select`).

## What it supports

Browse the live list with `redux crack modes`. The catalog covers Wi-Fi, Windows
/ AD (NTLM, NetNTLMv1/2, kerberoast, AS-REP, DCC2), Linux shadow (sha512crypt,
sha256crypt, md5crypt, bcrypt), web app auth (phpass/WordPress, MySQL, Django,
LDAP SSHA, JWT), raw digests (MD5/SHA1/SHA256/SHA512), archives (7-Zip, RAR5,
WinZip), documents (Office, PDF), and keys/disks/crypto (SSH key, KeePass,
VeraCrypt, Bitcoin wallet). Any mode hashcat supports is still reachable with a
bare `--type <number>`.

## Everything is glass-box and fail-closed

- `plan` prints the exact argv it would run; nothing hidden.
- A plan that can't run (no wordlist for a straight attack, no mask for a mask
  attack, a non-absolute hash path) refuses **with a reason** and emits no
  command.
- Results are written private (`0600`) under `--results-dir` (default
  `/captures/crack`), potfile disabled.
