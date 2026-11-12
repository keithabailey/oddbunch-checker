"""Matching and proposal logic.

Preferences are two independent ranked lists:
  remove        items to swap out, priority order; the first one in the box gets the free swap
  replace_with  ranked replacements; the first one the form offers that week is used
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

PREFIXES = {"fresh", "local", "organic"}


def _singular(word: str) -> str:
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("oes", "ches", "shes", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalise(name: str, aliases: dict[str, str] | None = None) -> str:
    """Case-insensitive key: drops Fresh/Local/Organic prefixes and a plural on the last word.

    Matching on the key is exact, so "Kale" does not match "Kale Slaw".
    """
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    while len(words) > 1 and words[0] in PREFIXES:
        words.pop(0)
    if words:
        words[-1] = _singular(words[-1])
    key = " ".join(words)
    if aliases:
        amap = {normalise(k): normalise(v) for k, v in aliases.items()}
        key = amap.get(key, key)
    return key


@dataclass
class Swap:
    remove: str
    candidates: list[str]  # ranked; submission uses the first one the form accepts
    paid: bool = False


@dataclass
class Proposal:
    flagged: list[str] = field(default_factory=list)
    free: Swap | None = None
    paid: list[Swap] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        return cls(
            flagged=d.get("flagged", []),
            free=Swap(**d["free"]) if d.get("free") else None,
            paid=[Swap(**s) for s in d.get("paid", [])],
        )


def rank_candidates(
    replace_with: list[str],
    available: list[str] | None = None,
    exclude: list[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Ranked replacements, filtered to what the form offers when that is known.

    Returns the form's own spelling for each match so it can be selected verbatim.
    """
    excl = {normalise(x, aliases) for x in (exclude or [])}
    avail = None if available is None else {normalise(a, aliases): a for a in available}
    out, seen = [], set()
    for want in replace_with:
        key = normalise(want, aliases)
        if key in excl or key in seen:
            continue
        if avail is None:
            out.append(want)
        elif key in avail:
            out.append(avail[key])
        else:
            continue
        seen.add(key)
    return out


def find_in(name: str, names: list[str], aliases: dict[str, str] | None = None) -> str | None:
    key = normalise(name, aliases)
    for n in names:
        if normalise(n, aliases) == key:
            return n
    return None


def evaluate(
    box_names: list[str],
    remove: list[str],
    replace_with: list[str],
    aliases: dict[str, str] | None = None,
    available: list[str] | None = None,
) -> Proposal:
    flagged: list[str] = []
    for unwanted in remove:
        hit = find_in(unwanted, box_names, aliases)
        if hit and hit not in flagged:
            flagged.append(hit)
    if not flagged:
        return Proposal()
    candidates = rank_candidates(replace_with, available, exclude=flagged, aliases=aliases)
    return Proposal(
        flagged=flagged,
        free=Swap(remove=flagged[0], candidates=candidates, paid=False),
        paid=[Swap(remove=f, candidates=candidates, paid=True) for f in flagged[1:]],
    )
