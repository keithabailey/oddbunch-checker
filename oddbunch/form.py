"""Read the substitution form's configuration without a browser.

The form at /pages/substitutions is a POWR Form Builder form. POWR serves the whole
form, including every conditional field and its options, as server-rendered HTML
with the field definitions embedded as JSON. Reading it is a single GET; nothing is
filled in or submitted.

Field chain (resolved by condition links, not label text):
  Your Region -> Your Subscription (<region>) -> Item to Remove (<box> - <region>)   [page 1]
  Your Region -> Box to choose from? (<region>) -> Item to Add (<box> - <region>)    [page 2]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

from .evaluate import normalise
from .menu import USER_AGENT

FORM_UNIQUE_LABEL = "1476dcbf_1758738507"
SSR_URL = "https://view.powr.io/api/ssr/shadow?unique_label={label}&storefront=shopify"
REGION_OPTIONS = {"west": "BC / AB", "east": "ON / QC", "prairie": "SK / MB"}
KEEP_KEYS = ("idx", "type", "label", "options", "required", "conditionalElement", "conditionalValue")


class FormError(Exception):
    pass


def parse_components(html: str) -> list[dict]:
    dec = json.JSONDecoder()
    out, seen = [], set()
    for m in re.finditer(r'\{"idx":"', html):
        try:
            obj, _ = dec.raw_decode(html, m.start())
        except ValueError:
            continue
        if obj.get("idx") in seen:
            continue
        seen.add(obj["idx"])
        out.append({k: obj.get(k) for k in KEEP_KEYS})
    if not out:
        raise FormError("no form fields found in the POWR response; the form may have changed")
    return out


def fetch_components(label: str = FORM_UNIQUE_LABEL, timeout: int = 30) -> list[dict]:
    resp = requests.get(SSR_URL.format(label=label), headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return parse_components(resp.text)


def item_name(option: str) -> str:
    """'Fresh Gooseberries - 1 clamshell' -> 'Fresh Gooseberries'."""
    return option.rsplit(" - ", 1)[0].strip() if " - " in option else option.strip()


def subscription_for(box_slug: str) -> str:
    """'large-box' -> 'Large Box' (as the form spells it)."""
    return box_slug.replace("-", " ").title()


@dataclass
class Choice:
    box: str
    option: str
    label: str           # the Item to Add field's label

    @property
    def name(self) -> str:
        return item_name(self.option)


class FormPlan:
    """The fields that apply to one region and subscription."""

    def __init__(self, comps: list[dict], region_value: str, subscription: str):
        self.comps = comps
        self.region_value = region_value
        self.subscription = subscription
        region = self._one(lambda c: c["type"] == "select" and not c.get("conditionalElement")
                           and c["label"].lower().startswith("your region"), "Your Region")
        if region_value not in region["options"]:
            raise FormError(f"region {region_value!r} not offered; form has {region['options']}")
        self.region = region
        self.subscription_field = self._child(region, region_value, "your subscription")
        if subscription not in self.subscription_field["options"]:
            raise FormError(f"subscription {subscription!r} not offered; form has "
                            f"{self.subscription_field['options']}")
        self.remove_field = self._child(self.subscription_field, subscription, "item to remove")
        self.box_field = self._child(region, region_value, "box to choose")
        self.add_fields = {}
        for box in self.box_field["options"]:
            try:
                self.add_fields[box] = self._child(self.box_field, box, "item to add")
            except FormError:
                continue

    def _one(self, pred, what: str) -> dict:
        hits = [c for c in self.comps if pred(c)]
        if len(hits) != 1:
            raise FormError(f"expected one {what!r} field, found {len(hits)}")
        return hits[0]

    def _child(self, parent: dict, value: str, prefix: str) -> dict:
        return self._one(lambda c: c.get("conditionalElement") == parent["idx"]
                         and c.get("conditionalValue") == value
                         and c["label"].lower().startswith(prefix), f"{prefix} ({value})")

    @property
    def remove_options(self) -> list[str]:
        return [o for o in self.remove_field["options"] if o and o.strip()]

    def remove_option(self, name: str, aliases: dict | None = None) -> str | None:
        key = normalise(name, aliases)
        return next((o for o in self.remove_options if normalise(item_name(o), aliases) == key), None)

    def box_order(self, preferred: list[str] | None = None) -> list[str]:
        order = [self.subscription, *(preferred or []), *self.add_fields]
        seen, out = set(), []
        for b in order:
            if b in self.add_fields and b not in seen:
                seen.add(b)
                out.append(b)
        return out

    def add_options(self, box: str) -> list[str]:
        return [o for o in self.add_fields[box]["options"]
                if o and o.strip() and normalise(o) != "nothing"]

    def find_replacement(self, candidates: list[str], preferred_boxes: list[str] | None = None,
                         aliases: dict | None = None, exclude: list[str] | None = None,
                         only_boxes: list[str] | None = None) -> Choice | None:
        """First candidate (in preference order) offered by any box list (own box first)."""
        excl = {normalise(x, aliases) for x in (exclude or [])}
        boxes = [b for b in (only_boxes or []) if b in self.add_fields] or self.box_order(preferred_boxes)
        for cand in candidates:
            key = normalise(cand, aliases)
            if key in excl:
                continue
            for box in boxes:
                for opt in self.add_options(box):
                    if normalise(item_name(opt), aliases) == key:
                        return Choice(box=box, option=opt, label=self.add_fields[box]["label"])
        return None

    def summary(self) -> dict:
        return {box: [item_name(o) for o in self.add_options(box)] for box in self.add_fields}


def plan_for(comps: list[dict], region: str, box_slug: str, subscription: str | None = None) -> FormPlan:
    if region not in REGION_OPTIONS:
        raise FormError(f"unknown region {region!r}")
    return FormPlan(comps, REGION_OPTIONS[region], subscription or subscription_for(box_slug))
