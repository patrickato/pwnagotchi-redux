"""Guided catalog for cracking ANY hash — not just Wi-Fi.

The live pipeline (`audit.py`) only ever runs hashcat in one mode: WPA/WPA2
(`-m 22000`). That is the right default for what the device captures off the
air, but it means the box can't help with the *other* hashes an operator ends
up holding on an authorized engagement: an NTLM dump, a kerberoast ticket, a
Linux ``/etc/shadow`` line, a password-protected archive, an SSH key.

This module turns hashcat's ~400 numeric ``-m`` modes into a small set of
**named, categorized recipes** with smart defaults, so the operator picks
``ntlm`` or ``linux-shadow`` instead of memorizing mode numbers, and gets a
sensible attack + wordlist + rules choice for that hash type. Everything here
is pure and glass-box:

* :func:`find` / :func:`by_name` — browse or search the catalog.
* :func:`identify` — look at a real hash sample and suggest the mode(s).
* :func:`plan` — assemble the exact hashcat argv for a recipe + options, with a
  human-readable explanation and a list of problems (fail closed).
* :func:`suggest` — the "help me pick" smarts: given a recipe, recommend an
  attack mode + whether to use rules, with the reason.

Real data only: :func:`identify` reports what a sample *looks* like, never a
guarantee; :func:`plan` refuses (with a reason) rather than emit a command that
can't run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# ---- attack modes (hashcat -a) ------------------------------------------- #

ATTACKS: Dict[int, str] = {
    0: "straight (wordlist, optionally + rules)",
    1: "combinator (two wordlists joined)",
    3: "mask / brute-force (a character-class pattern)",
    6: "hybrid wordlist + mask",
    7: "hybrid mask + wordlist",
}

# Rule presets map a friendly name to the filename hashcat ships (or that the
# operator dropped in a rules dir). A rule set multiplies each wordlist
# candidate into many mangled variants — huge uplift on FAST hashes, pointless
# cost on slow ones. Resolution searches real directories; a preset that isn't
# present on disk is simply skipped with a reason (never a broken -r path).
RULE_PRESETS: Dict[str, str] = {
    "best64": "best64.rule",            # small, bundled, great default
    "rockyou-30000": "rockyou-30000.rule",
    "dive": "dive.rule",                # large, bundled
    "d3ad0ne": "d3ad0ne.rule",
    "toggles": "toggles5.rule",
    "onerule": "OneRuleToRuleThemAll.rule",   # not bundled; a popular download
}

_RULE_DIRS: Tuple[str, ...] = (
    "/captures/rules",                  # our own store (operator-managed)
    "/usr/share/hashcat/rules",         # distro hashcat
    "/usr/local/share/hashcat/rules",
    "/opt/hashcat/rules",
)

# A few handy named masks for -a 3 so the operator doesn't hand-write ?d?d?d…
MASK_PRESETS: Dict[str, str] = {
    "digits8": "?d?d?d?d?d?d?d?d",            # 8-digit PIN / phone tail
    "digits6": "?d?d?d?d?d?d",
    "lower-digits8": "?l?l?l?l?l?l?d?d",
    "upper-lower6-digit2": "?u?l?l?l?l?l?d?d",
    "wpa-default8": "?d?d?d?d?d?d?d?d",       # many default router PSKs
}


@dataclass(frozen=True)
class Recipe:
    """One named hash type the operator can target by slug."""
    name: str                 # friendly slug, e.g. "ntlm"
    mode: int                 # hashcat -m
    label: str                # human description
    category: str             # wifi|windows|linux|web|network|archive|document|disk|generic|crypto
    example: str = ""         # a sample shape (docs + identify hints)
    attack: int = 0           # sensible default attack mode
    rules: str = ""           # default rule preset name ("" == none)
    slow: bool = False        # a deliberately slow KDF — don't pile on rules/brute
    note: str = ""

    def describe(self) -> str:
        bits = [f"-m {self.mode}", self.category]
        if self.slow:
            bits.append("slow-KDF")
        return f"{self.name}: {self.label} ({', '.join(bits)})"


# Curated — the hashes an operator actually runs into, one obvious slug each.
# Ordered roughly by how often they come up on an engagement.
RECIPES: Tuple[Recipe, ...] = (
    # --- Wi-Fi (what the live pipeline already does) --------------------- #
    Recipe("wifi", 22000, "WPA/WPA2 PMKID + EAPOL handshake", "wifi",
           example="WPA*01*… / WPA*02*…", attack=0,
           note="exactly what `redux pipeline audit` cracks automatically"),
    Recipe("wifi-pmkid", 16800, "WPA-PMKID-PBKDF2 (legacy PMKID-only)", "wifi",
           attack=0, note="older format; prefer 22000 when you have it"),
    # --- Windows / AD ---------------------------------------------------- #
    Recipe("ntlm", 1000, "Windows NTLM (NT hash)", "windows",
           example="b4b9b02e6f09a389…", attack=0, rules="best64"),
    Recipe("ntlmv2", 5600, "NetNTLMv2 (Responder / SMB relay captures)", "network",
           example="user::DOMAIN:1122334455667788:…", attack=0, rules="best64"),
    Recipe("ntlmv1", 5500, "NetNTLMv1", "network", attack=0, rules="best64"),
    Recipe("kerberoast", 13100, "Kerberos TGS-REP (kerberoasting)", "network",
           example="$krb5tgs$23$*user$REALM*…", attack=0, rules="best64"),
    Recipe("asreproast", 18200, "Kerberos AS-REP (AS-REP roasting)", "network",
           example="$krb5asrep$23$user@REALM:…", attack=0, rules="best64"),
    Recipe("dcc2", 2100, "Domain Cached Credentials 2 (mscash2)", "windows",
           example="$DCC2$10240#user#…", attack=0, slow=True),
    # --- Linux / Unix shadow -------------------------------------------- #
    Recipe("linux-shadow", 1800, "Linux shadow sha512crypt $6$", "linux",
           example="$6$salt$hash…", attack=0, slow=True,
           note="/etc/shadow on modern Linux"),
    Recipe("sha256crypt", 7400, "sha256crypt $5$", "linux",
           example="$5$salt$hash…", attack=0, slow=True),
    Recipe("md5crypt", 500, "md5crypt $1$ (old Linux / Cisco IOS)", "linux",
           example="$1$salt$hash…", attack=0, rules="best64"),
    Recipe("bcrypt", 3200, "bcrypt $2*$ (BSD / modern app auth)", "linux",
           example="$2y$10$…", attack=0, slow=True,
           note="very slow by design — use a small, targeted list"),
    Recipe("macos", 7100, "macOS 10.8+ PBKDF2-SHA512", "disk",
           example="$ml$…", attack=0, slow=True),
    # --- Web app auth ---------------------------------------------------- #
    Recipe("phpass", 400, "phpass (WordPress / Joomla / phpBB)", "web",
           example="$P$B…", attack=0, rules="best64"),
    Recipe("mysql", 300, "MySQL4.1+ (double-SHA1)", "web",
           example="*E6CC90B878B948…", attack=0, rules="best64"),
    Recipe("django-sha1", 124, "Django salted SHA1", "web",
           example="sha1$salt$hash", attack=0, rules="best64"),
    Recipe("ldap-ssha", 111, "LDAP {SSHA} salted SHA1", "web",
           example="{SSHA}base64…", attack=0, rules="best64"),
    # --- Generic raw digests -------------------------------------------- #
    Recipe("md5", 0, "raw MD5", "generic", example="32 hex chars", attack=0, rules="best64"),
    Recipe("sha1", 100, "raw SHA1", "generic", example="40 hex chars", attack=0, rules="best64"),
    Recipe("sha256", 1400, "raw SHA-256", "generic", example="64 hex chars", attack=0, rules="best64"),
    Recipe("sha512", 1700, "raw SHA-512", "generic", example="128 hex chars", attack=0, rules="best64"),
    Recipe("ntlm-or-md5", 1000, "32-hex ambiguous (try NTLM then MD5)", "generic",
           example="32 hex chars", attack=0, rules="best64",
           note="32-hex could be NTLM (1000) or MD5 (0); try both"),
    # --- Archives / documents ------------------------------------------- #
    Recipe("7zip", 11600, "7-Zip archive", "archive", example="$7z$…", attack=0, slow=True),
    Recipe("rar5", 13000, "RAR5 archive", "archive", example="$rar5$…", attack=0, slow=True),
    Recipe("winzip", 13600, "WinZip AES", "archive", example="$zip2$…", attack=0),
    Recipe("office2013", 9600, "MS Office 2013+", "document", example="$office$*2013*…", attack=0, slow=True),
    Recipe("office2010", 9500, "MS Office 2010", "document", example="$office$*2010*…", attack=0, slow=True),
    Recipe("pdf17", 10700, "PDF 1.7 Level 8 (Acrobat X+)", "document", example="$pdf$5*6*…", attack=0, slow=True),
    Recipe("pdf-old", 10500, "PDF 1.4–1.6 (Acrobat 5–8)", "document", example="$pdf$2*3*…", attack=0),
    # --- Keys / disks / crypto ------------------------------------------ #
    Recipe("sshkey", 22921, "SSH private key (RSA/DSA/EC, new hashcat)", "disk",
           example="$sshng$…", attack=0, slow=True),
    Recipe("keepass", 13400, "KeePass 1/2 database", "disk", example="$keepass$…", attack=0, slow=True),
    Recipe("veracrypt", 13721, "VeraCrypt SHA-512 + XTS", "disk",
           example="(raw volume via veracrypt2hashcat)", attack=0, slow=True,
           note="extremely slow — realistically only a short/known-shape password"),
    Recipe("bitcoin", 11300, "Bitcoin / Litecoin wallet.dat", "crypto",
           example="$bitcoin$…", attack=0, slow=True),
    Recipe("jwt", 16500, "JWT (HMAC) signature", "web", example="eyJ…\\.eyJ…\\.sig", attack=0, rules="best64"),
)

CATEGORIES: Tuple[str, ...] = (
    "wifi", "windows", "network", "linux", "web",
    "generic", "archive", "document", "disk", "crypto",
)

_BY_NAME: Dict[str, Recipe] = {r.name: r for r in RECIPES}


def by_name(name: str) -> Optional[Recipe]:
    """Exact slug lookup, or a bare hashcat mode number as a string."""
    if not name:
        return None
    key = name.strip().lower()
    if key in _BY_NAME:
        return _BY_NAME[key]
    if key.isdigit():                   # allow `--type 1000`
        mode = int(key)
        for r in RECIPES:
            if r.mode == mode:
                return r
    return None


def find(query: str = "") -> List[Recipe]:
    """Search the catalog by slug / label / category / mode substring. Empty
    query returns the whole catalog (catalog order)."""
    q = (query or "").strip().lower()
    if not q:
        return list(RECIPES)
    out = []
    for r in RECIPES:
        hay = f"{r.name} {r.label} {r.category} {r.mode}".lower()
        if q in hay:
            out.append(r)
    return out


# ---- identify: look at a real sample and suggest modes ------------------- #

_HEXLINE = re.compile(r"^[0-9a-fA-F]+$")
# ordered (regex-or-predicate, recipe-slug) prefix rules — first wins, but we
# collect ALL that match so ambiguous shapes can offer more than one.
_PREFIX_RULES: Tuple[Tuple[str, str], ...] = (
    ("$6$", "linux-shadow"),
    ("$5$", "sha256crypt"),
    ("$1$", "md5crypt"),
    ("$2a$", "bcrypt"), ("$2b$", "bcrypt"), ("$2y$", "bcrypt"), ("$2x$", "bcrypt"),
    ("$p$", "phpass"), ("$h$", "phpass"),
    ("$apr1$", "md5crypt"),
    ("$ml$", "macos"),
    ("$dcc2$", "dcc2"),
    ("$krb5tgs$", "kerberoast"),
    ("$krb5asrep$", "asreproast"),
    ("$7z$", "7zip"),
    ("$rar5$", "rar5"),
    ("$zip2$", "winzip"),
    ("$office$", "office2013"),
    ("$pdf$", "pdf17"),
    ("$sshng$", "sshkey"),
    ("$keepass$", "keepass"),
    ("$bitcoin$", "bitcoin"),
    ("{ssha}", "ldap-ssha"),
    ("sha1$", "django-sha1"),
    ("wpa*01*", "wifi"), ("wpa*02*", "wifi"),
)


def identify(sample: str) -> List[Recipe]:
    """Best-effort: given one hash *line*, return candidate recipes, best first.

    This is a heuristic on shape, never a guarantee — it exists to save the
    operator from guessing mode numbers. Ambiguous shapes (e.g. 32 hex) return
    more than one candidate so the operator can try each.
    """
    s = (sample or "").strip()
    if not s:
        return []
    low = s.lower()
    hits: List[str] = []

    for prefix, slug in _PREFIX_RULES:
        if low.startswith(prefix):
            hits.append(slug)

    # NetNTLMv2 looks like user::DOMAIN:...:...:... (contains "::" and colons)
    if "::" in s and s.count(":") >= 4 and not s.startswith("$"):
        hits.append("ntlmv2")

    # JWT: three base64url segments split by dots
    if s.count(".") == 2 and all(seg for seg in s.split(".")) and s.startswith("ey"):
        hits.append("jwt")

    # MySQL4.1 is 40 hex with a leading '*'
    if s.startswith("*") and _HEXLINE.match(s[1:] or "") and len(s) == 41:
        hits.append("mysql")

    # raw hex digests by length
    if _HEXLINE.match(s):
        n = len(s)
        if n == 32:
            hits += ["ntlm", "md5"]        # ambiguous: offer both, NTLM first (common on AD jobs)
        elif n == 40:
            hits.append("sha1")
        elif n == 64:
            hits.append("sha256")
        elif n == 128:
            hits.append("sha512")

    # de-dup preserving order, map to recipes
    seen = set()
    out: List[Recipe] = []
    for slug in hits:
        if slug in seen:
            continue
        seen.add(slug)
        r = _BY_NAME.get(slug)
        if r:
            out.append(r)
    return out


# ---- rule / mask resolution --------------------------------------------- #

def resolve_rules(preset_or_path: str, *, dirs: Sequence[str] = _RULE_DIRS,
                  exists=None) -> Tuple[Optional[str], str]:
    """Resolve a rule preset name OR an absolute path to a real file on disk.

    Returns (abs_path, reason) or (None, reason). `exists` is an injectable
    predicate (Path -> bool) so this is testable without a real hashcat install.
    """
    _exists = exists or (lambda p: Path(p).is_file())
    if not preset_or_path:
        return None, "no rules requested"
    cand = preset_or_path.strip()
    # explicit path wins
    if "/" in cand or cand.endswith(".rule"):
        p = Path(cand)
        if p.is_absolute() and _exists(p):
            return str(p), f"using rule file {p}"
        # maybe it's a bare filename to look up in the dirs
    fname = RULE_PRESETS.get(cand.lower(), cand if cand.endswith(".rule") else cand + ".rule")
    for d in dirs:
        p = Path(d) / fname
        if _exists(p):
            return str(p), f"using rules '{cand}' -> {p}"
    return None, (f"refused: rules '{cand}' not found (looked for {fname} in "
                  + ", ".join(dirs) + ") — skipping rules")


def resolve_mask(mask_or_preset: str) -> Tuple[Optional[str], str]:
    if not mask_or_preset:
        return None, "no mask given"
    m = mask_or_preset.strip()
    if m.lower() in MASK_PRESETS:
        return MASK_PRESETS[m.lower()], f"using mask preset '{m}' = {MASK_PRESETS[m.lower()]}"
    if "?" in m:                          # looks like a raw hashcat mask
        return m, f"using mask {m}"
    return None, f"refused: '{m}' is not a known mask preset and has no ?-classes"


# ---- the "help me pick" smarts ------------------------------------------ #

@dataclass(frozen=True)
class Suggestion:
    attack: int
    use_rules: bool
    rules: str
    reason: str


def suggest(recipe: Recipe) -> Suggestion:
    """Recommend an attack + rules choice for a hash type, with the reason.

    The core heuristic is cost: FAST hashes (raw digests, NTLM) can afford a
    rule set that multiplies the wordlist many-fold; SLOW KDFs (bcrypt,
    sha512crypt, 7-Zip, VeraCrypt) cannot — there a straight run of a small,
    targeted list is the only sane choice.
    """
    if recipe.slow:
        return Suggestion(
            attack=0, use_rules=False, rules="",
            reason=(f"{recipe.name} is a slow KDF — straight wordlist only, a small "
                    "targeted list; rules/brute-force would take far too long"))
    # fast hash
    rules = recipe.rules or "best64"
    return Suggestion(
        attack=0, use_rules=True, rules=rules,
        reason=(f"{recipe.name} is fast — straight wordlist + '{rules}' rules gives "
                "big coverage for little extra time"))


# ---- plan: assemble the exact hashcat argv ------------------------------ #

@dataclass(frozen=True)
class CrackPlan:
    argv: List[str]
    recipe: Recipe
    attack: int
    wordlist: Optional[str]
    rules: Optional[str]
    mask: Optional[str]
    explanation: str
    problems: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.problems and bool(self.argv)


def plan(recipe: Recipe, hash_file: str, *,
         attack: Optional[int] = None,
         wordlist: Optional[str] = None,
         rules: Optional[str] = None,
         mask: Optional[str] = None,
         binary: str = "hashcat",
         outfile: Optional[str] = None,
         extra: Sequence[str] = (),
         rule_exists=None) -> CrackPlan:
    """Build the hashcat command for `recipe` over `hash_file`, glass-box.

    Fail closed: a plan with problems has an empty argv and a reason, rather
    than a command that would error at runtime. `rule_exists` is injectable for
    tests. The caller (the gated runner) decides whether it's *authorized* to
    run; this only decides what a correct run *is*.
    """
    problems: List[str] = []
    expl: List[str] = []
    atk = recipe.attack if attack is None else attack
    if atk not in ATTACKS:
        problems.append(f"unknown attack mode {atk} (choose {sorted(ATTACKS)})")
        atk = recipe.attack

    hp = Path(hash_file)
    if not hash_file:
        problems.append("no hash file given")
    elif not hp.is_absolute():
        problems.append(f"hash file path must be absolute: {hash_file}")

    argv: List[str] = [binary, "-m", str(recipe.mode), "-a", str(atk)]
    argv += [str(hash_file)]
    expl.append(f"hashcat mode {recipe.mode} ({recipe.label}), "
                f"attack {atk} — {ATTACKS.get(atk, '?')}")

    rule_path = None
    if atk in (0, 1):                     # straight / combinator: need wordlist(s)
        if not wordlist:
            problems.append("attack needs a --wordlist")
        else:
            argv.append(str(wordlist))
            expl.append(f"wordlist {wordlist}")
        if atk == 0 and rules:
            rule_path, why = resolve_rules(rules, exists=rule_exists)
            expl.append(why)
            if rule_path:
                argv += ["-r", rule_path]
            # a missing rule file is a soft problem: we drop rules, not the run
            elif rules:
                expl.append("(continuing without rules)")
    elif atk == 3:                        # mask / brute-force
        resolved, why = resolve_mask(mask or "")
        expl.append(why)
        if not resolved:
            problems.append("mask attack needs a valid --mask")
        else:
            argv.append(resolved)
    elif atk in (6, 7):                   # hybrid: wordlist + mask (order by mode)
        resolved, why = resolve_mask(mask or "")
        if not wordlist or not resolved:
            problems.append("hybrid attack needs BOTH --wordlist and --mask")
        else:
            argv += ([str(wordlist), resolved] if atk == 6 else [resolved, str(wordlist)])
            expl.append(f"hybrid {wordlist} + {resolved}")

    if outfile:
        argv += ["--outfile", str(outfile), "--outfile-format", "2"]
    argv += ["--potfile-disable", "--quiet"]
    argv += list(extra)

    if problems:
        return CrackPlan(argv=[], recipe=recipe, attack=atk, wordlist=wordlist,
                         rules=rule_path, mask=mask,
                         explanation="refused: " + "; ".join(problems),
                         problems=tuple(problems))
    return CrackPlan(argv=argv, recipe=recipe, attack=atk, wordlist=wordlist,
                     rules=rule_path, mask=mask,
                     explanation="will run " + "; ".join(expl))
