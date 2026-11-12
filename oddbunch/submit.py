"""Fill (and optionally submit) the substitution form in a visible browser.

The form is a two-page POWR form inside the ``powr-form-builder`` element (an open
shadow root, which Playwright reaches directly):

  page 1  Full Name, Email Address, Your Region, Your Subscription (<region>),
          Item to Remove (<box> - <region>)                              -> Next
  page 2  Box to choose from? (<region>), Item to Add (<box> - <region>) -> Submit

Field labels and option texts come from ``form.FormPlan`` (read from POWR's config),
so the browser only selects values that are known to exist.

Guardrails: this never solves, bypasses or outsources a CAPTCHA and uses no stealth
or fingerprint-masking plugins. If a challenge appears it hands the window to you.
Default mode only fills the form; you click Submit yourself.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from .form import EMAIL_LABEL, NAME_LABEL

SUBS_URL = "https://www.oddbunch.ca/pages/substitutions"
CONFIRM_RE = re.compile(r"thank|received|submitted|success", re.I)


class SubmitError(Exception):
    pass


@dataclass
class FormFill:
    name: str
    email: str
    region_label: str
    region: str
    subscription_label: str
    subscription: str
    remove_label: str
    remove_option: str
    box_label: str
    box: str
    add_label: str
    add_option: str
    extra: dict = field(default_factory=dict)    # label -> value, if the form grows new text fields


def fill_for(plan, name: str, email: str, remove_option: str, choice, extra: dict | None = None) -> FormFill:
    """The FormFill for one swap, with every label taken from the form's own config."""
    return FormFill(
        name=name, email=email,
        region_label=plan.region["label"], region=plan.region_value,
        subscription_label=plan.subscription_field["label"], subscription=plan.subscription,
        remove_label=plan.remove_field["label"], remove_option=remove_option,
        box_label=plan.box_field["label"], box=choice.box,
        add_label=choice.label, add_option=choice.option,
        extra=dict(extra or {}),
    )


def _open(profile_dir: Path, url: str, headless: bool):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=headless)
    ctx.set_default_timeout(10000)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    return pw, ctx, page


def _wait_for_close(page) -> None:
    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass


def _field(page, label: str, timeout_ms: int = 10000):
    loc = page.locator("powr-form-builder").get_by_label(label, exact=False).first
    loc.wait_for(state="visible", timeout=timeout_ms)
    return loc


def _select(page, label: str, option: str) -> None:
    _field(page, label).select_option(label=option)


def _challenge_visible(page) -> bool:
    for frame in page.frames:
        url = frame.url or ""
        if ("recaptcha/api2/bframe" in url or "recaptcha/enterprise/bframe" in url
                or ("hcaptcha" in url and "challenge" in url)):
            try:
                el = frame.frame_element()
                if el.is_visible() and (el.bounding_box() or {}).get("height", 0) > 50:
                    return True
            except Exception:
                continue
    return False


def _fill_all(page, fill: FormFill, timeout_s: int) -> None:
    """Fill both pages. Raises SubmitError naming the step that failed."""
    step = "loading the form"
    try:
        _field(page, EMAIL_LABEL, timeout_s * 1000)
        step = NAME_LABEL
        _field(page, NAME_LABEL).fill(fill.name)
        step = EMAIL_LABEL
        _field(page, EMAIL_LABEL).fill(fill.email)
        step = fill.region_label
        _select(page, fill.region_label, fill.region)
        step = fill.subscription_label
        _select(page, fill.subscription_label, fill.subscription)
        step = fill.remove_label
        _select(page, fill.remove_label, fill.remove_option)
        for label, value in fill.extra.items():
            step = label
            _field(page, label).fill(value)
        step = "Next"
        page.locator("powr-form-builder").get_by_role("button", name="Next").click()
        step = fill.box_label
        _select(page, fill.box_label, fill.box)
        step = fill.add_label
        _select(page, fill.add_label, fill.add_option)
    except SubmitError:
        raise
    except Exception as exc:
        raise SubmitError(f"could not complete '{step}': {str(exc).splitlines()[0]}") from exc


def run(fill: FormFill, profile_dir: Path, shots_dir: Path, submit: bool = False, *,
        url: str = SUBS_URL, headless: bool = False, wait: bool = True, timeout_s: int = 45,
        quiet: bool = False) -> dict:
    """Fill the form. With submit=False (default) it stops for you to review and click Submit."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    pw, ctx, page = _open(profile_dir, url, headless)
    result = {"result": "", "screenshot": None, "error": None}
    say = (lambda *_: None) if quiet else print
    try:
        try:
            _fill_all(page, fill, timeout_s)
        except SubmitError as exc:
            result["error"] = str(exc)
            say(f"Stopped: {exc}. Finish the form by hand, then close the browser window.")
            if wait:
                _wait_for_close(page)
            result["result"] = "incomplete, handed over"
            return result

        if not submit:
            say("Form filled. Check it, click Submit yourself, then close the browser window.")
            if wait:
                _wait_for_close(page)
            result["result"] = "prefilled"
            return result

        page.locator("powr-form-builder").get_by_role("button", name="Submit").click()
        page.wait_for_timeout(3000)
        if _challenge_visible(page):
            say("A CAPTCHA challenge appeared. Complete it by hand, then close the browser window.")
            if wait:
                _wait_for_close(page)
            result["result"] = "handed over (captcha)"
            return result
        try:
            page.locator("powr-form-builder").get_by_text(CONFIRM_RE).first.wait_for(timeout=20000)
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
