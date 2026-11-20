"""Read-only probe for the wylkyj grading page. Never clicks submit.

Usage: python tools/web_probe.py [--fill 3]
"""
import argparse
import os
import re
import sys

from playwright.sync_api import sync_playwright

IMG = ".imgSection img"
INPUT = "#inputOne"
SUBMIT = "button.btnSubmit"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_out")


def fingerprint(src):
    m = re.search(r"/([^/?]+)\?", src or "")
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill", help="fill this score into the box (NOT submitted), read back, then clear")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "wylkyj" in pg.url:
                    page = pg
        if page is None:
            print("FAIL: no wylkyj page found in Edge")
            return 1
        ctx = page.context
        print("page frames:", len(page.frames))

        img = page.locator(IMG)
        print("img count:", img.count(), "| input count:", page.locator(INPUT).count(),
              "| submit count:", page.locator(SUBMIT).count())
        img.first.wait_for(state="visible", timeout=10000)
        page.wait_for_function(
            "s => { const i = document.querySelector(s); return i && i.complete && i.naturalWidth > 0 }",
            arg=IMG, timeout=10000)
        src = img.first.get_attribute("src")
        if not src:
            print("FAIL: img has no src")
            return 1
        print("fingerprint:", fingerprint(src))
        print("natural size:", page.evaluate(
            "s => { const i = document.querySelector(s); return [i.naturalWidth, i.naturalHeight] }", IMG))

        print("displayed size:", page.evaluate(
            "s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.width, r.height] }", IMG))

        img.first.screenshot(path=os.path.join(OUT, "paper_element.png"))
        print("placeholder:", page.locator(INPUT).get_attribute("placeholder"))

        if args.fill is not None:
            box = page.locator(INPUT)
            box.fill(args.fill)
            back = box.input_value()
            print("filled:", args.fill, "read back:", back, "OK" if back == args.fill else "MISMATCH")
            input("Look at the page, press Enter here to clear the box (nothing was submitted)...")
            box.fill("")
        print("saved to", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
