"""Fill the substitution form in a visible browser.

Status: the form has not yet been seen live (it only renders Monday 2pm to Tuesday
11:55pm ET). Field detection is heuristic until the labels are confirmed; run
``oddbunch inspect-form`` on the first open Monday and record the labels under
``form:`` in prefs.yaml.

Guardrails: this never solves, bypasses or outsources a CAPTCHA, and uses no
stealth or fingerprint-masking plugins. If a challenge appears it hands the window
to you. Default mode only fills the form; you click Submit yourself.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from .evaluate import normalise

SUBS_URL = "https://www.oddbunch.ca/pages/substitutions"

EMAIL_HINTS = [r"e-?mail"]
REMOVE_HINTS = [r"remove", r"swap(ped)? out", r"don.?t want", r"take out", r"item to (swap|replace)"]
REPLACE_HINTS = [r"replace(ment| with)", r"instead", r"substitut(e|ion)\b.*(with|item)", r"would (you )?like", r"swap (it )?for"]


class SubmitError(Exception):
    pass


@dataclass
class FormFill:
    email: str
    remove: str
    candidates: list[str]
    labels: dict = field(default_factory=dict)   # email_label / remove_label / replace_label
    extra: dict = field(default_factory=dict)    # label -> value, for any other fields


DESCRIBE_JS = """
() => Array.from(document.querySelectorAll('input, select, textarea')).filter(e => e.type !== 'hidden').map(e => {
  let label = '';
  if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) label = l.innerText; }
  if (!label && e.closest('label')) label = e.closest('label').innerText;
  if (!label) label = e.getAttribute('aria-label') || e.placeholder || '';
  return {tag: e.tagName.toLowerCase(), type: e.type || '', name: e.name || '', label: label.trim(),
          options: e.tagName === 'SELECT' ? Array.from(e.options).map(o => o.text.trim()).filter(Boolean) : []};
})
"""


def _find_form_frame(page, timeout_s: int):
    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout_s)
    while dt.datetime.now() < deadline:
        for frame in page.frames:
            try:
                if frame.locator("input[type=email], input[name*=mail i]").count():
                    return frame
            except Exception:
                continue
        page.wait_for_timeout(1000)
    return None


def _locate(frame, label: str | None, hints: list[str]):
    patterns = [re.compile(re.escape(label), re.I)] if label else [re.compile(h, re.I) for h in hints]
    for pat in patterns:
        for getter in (frame.get_by_label, frame.get_by_placeholder):
            loc = getter(pat)
            if loc.count():
                return loc.first
    return None


def _set(loc, value: str) -> str | None:
    """Fill a text field or choose a matching <select> option. Returns the value used."""
    tag = loc.evaluate("e => e.tagName.toLowerCase()")
    if tag == "select":
        options = [o.strip() for o in loc.locator("option").all_inner_texts()]
        for opt in options:
            if normalise(opt) == normalise(value):
                loc.select_option(label=opt)
                return opt
        return None
    loc.fill(value)
    return value


def _options(loc) -> list[str] | None:
    if loc.evaluate("e => e.tagName.toLowerCase()") != "select":
        return None
    return [o.strip() for o in loc.locator("option").all_inner_texts() if o.strip()]


def _challenge_visible(page) -> bool:
    for frame in page.frames:
        if "recaptcha/api2/bframe" in (frame.url or "") or "recaptcha/enterprise/bframe" in (frame.url or ""):
            try:
                el = frame.frame_element()
                if el.is_visible() and (el.bounding_box() or {}).get("height", 0) > 50:
                    return True
            except Exception:
                continue
    return False


def _open(profile_dir: Path):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(SUBS_URL, wait_until="domcontentloaded")
    return pw, ctx, page


def _wait_for_close(page) -> None:
    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass


def inspect_form(profile_dir: Path, timeout_s: int = 45) -> list[dict]:
    """Open the form and return every visible field with its label and options."""
    pw, ctx, page = _open(profile_dir)
    try:
        frame = _find_form_frame(page, timeout_s)
        if frame is None:
            raise SubmitError("substitution form not found; the window may be closed")
        return frame.evaluate(DESCRIBE_JS)
    finally:
        ctx.close()
        pw.stop()


def run(fill: FormFill, profile_dir: Path, shots_dir: Path, submit: bool = False, timeout_s: int = 45) -> dict:
    profile_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    pw, ctx, page = _open(profile_dir)
    result = {"replace": None, "result": "", "screenshot": None, "missing": []}
    try:
        frame = _find_form_frame(page, timeout_s)
        if frame is None:
            raise SubmitError("substitution form not found; the window may be closed")

        email_el = _locate(frame, fill.labels.get("email_label"), EMAIL_HINTS) or \
            frame.locator("input[type=email], input[name*=mail i]").first
        email_el.fill(fill.email)

        remove_el = _locate(frame, fill.labels.get("remove_label"), REMOVE_HINTS)
        if remove_el is None or _set(remove_el, fill.remove) is None:
            result["missing"].append("item to remove")

        replace_el = _locate(frame, fill.labels.get("replace_label"), REPLACE_HINTS)
        if replace_el is not None:
            for cand in fill.candidates:
                used = _set(replace_el, cand)
                if used:
                    result["replace"] = used
                    break
        if result["replace"] is None:
            result["missing"].append("replacement")
            if replace_el is not None and (opts := _options(replace_el)):
                result["form_options"] = opts

        for label, value in fill.extra.items():
            loc = _locate(frame, label, [])
            if loc is None or _set(loc, value) is None:
                result["missing"].append(label)

        if not submit or result["missing"]:
            if result["missing"]:
                print("Could not fill: " + ", ".join(result["missing"]) + ". Complete these by hand.")
            print("Form prefilled. Check it, click Submit yourself, then close the browser window.")
            _wait_for_close(page)
            result["result"] = "prefilled"
            return result

        frame.locator("button[type=submit], input[type=submit], button:has-text('Submit')").first.click()
        page.wait_for_timeout(3000)
        if _challenge_visible(page):
            print("A CAPTCHA challenge appeared. Complete it by hand, then close the browser window.")
            _wait_for_close(page)
            result["result"] = "handed over (captcha)"
            return result
        try:
            page.get_by_text(re.compile(r"thank|received|submitted|success", re.I)).first.wait_for(timeout=20000)
            result["result"] = "submitted"
        except Exception:
            result["result"] = "submitted, no confirmation seen"
        shot = shots_dir / f"{stamp}_substitution.png"
        page.screenshot(path=str(shot), full_page=True)
        result["screenshot"] = str(shot)
        return result
    finally:
        try:
            ctx.close()
        finally:
            pw.stop()
