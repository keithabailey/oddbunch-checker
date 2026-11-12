"""Fetch and parse the public weekly menu (no login, no browser).

The product page ships every region's list in the raw HTML as
``div.p2m__list[data-region-list=...]``; the non-default regions are simply
``hidden``. The card ``[data-p2m-card]`` carries ISO dates for the week.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import requests
from bs4 import BeautifulSoup

MENU_URL = "https://www.oddbunch.ca/products/{box}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
REGIONS = {"west": "BC & AB", "east": "ON & QC", "prairie": "SK & MB"}


class MenuError(Exception):
    """The page did not contain a parseable menu."""


@dataclass
class Item:
    name: str
    qty: str
    origin: str


@dataclass
class Menu:
    region: str
    week_label: str
    week_from: dt.date
    week_to: dt.date
    items: list[Item] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [i.name for i in self.items]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["week_from"] = self.week_from.isoformat()
        d["week_to"] = self.week_to.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Menu":
        return cls(
            region=d["region"],
            week_label=d["week_label"],
            week_from=dt.date.fromisoformat(d["week_from"]),
            week_to=dt.date.fromisoformat(d["week_to"]),
            items=[Item(**i) for i in d["items"]],
        )


def fetch_html(box: str = "large-box", timeout: int = 30) -> str:
    resp = requests.get(
        MENU_URL.format(box=box), headers={"User-Agent": USER_AGENT}, timeout=timeout
    )
    resp.raise_for_status()
    return resp.text


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse(html: str, region: str = "west") -> Menu:
    if region not in REGIONS:
        raise MenuError(f"unknown region {region!r}; expected one of {sorted(REGIONS)}")
    soup = BeautifulSoup(html, "html.parser")

    card = soup.select_one("[data-p2m-card]")
    if card is None or not card.get("data-from") or not card.get("data-to"):
        raise MenuError("menu card with week dates not found; the page layout may have changed")
    week_from = dt.date.fromisoformat(card["data-from"])
    week_to = dt.date.fromisoformat(card["data-to"])

    label_el = soup.select_one("[data-p2m-week]")
    week_label = _text(label_el) or f"{week_from:%b %d} - {week_to:%b %d}".upper()

    lst = soup.select_one(f'.p2m__list[data-region-list="{region}"]')
    if lst is None:
        raise MenuError(f"no item list for region {region!r}")

    items = []
    for row in lst.select(".p2m__row"):
        name = _text(row.select_one(".p2m__name"))
        if not name:
            continue
        qty = _text(row.select_one(".p2m__qty"))
        origin_el = row.select_one(".p2m__origin")
        origin = _text(origin_el)
        if origin.lower().startswith("grown in"):
            origin = origin[len("grown in"):].strip()
        items.append(Item(name=name, qty=qty, origin=origin))
    if not items:
        raise MenuError(f"item list for region {region!r} is empty")

    return Menu(region=region, week_label=week_label, week_from=week_from, week_to=week_to, items=items)


def fetch_menu(box: str = "large-box", region: str = "west") -> Menu:
    return parse(fetch_html(box), region)


def is_current(menu: Menu, today: dt.date) -> bool:
    """True once the page shows the upcoming delivery week rather than last week's."""
    return menu.week_from >= today
