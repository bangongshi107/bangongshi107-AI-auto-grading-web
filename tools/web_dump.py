"""Read-only dump of the grading page state (never clicks). Usage: python tools/web_dump.py"""
import json

from playwright.sync_api import sync_playwright

JS = """() => {
 const q = s => [...document.querySelectorAll(s)];
 const info = e => ({tag:e.tagName, cls:String(e.className).slice(0,80), id:e.id, text:(e.innerText||'').trim().slice(0,30), disabled:!!e.disabled, vis:e.offsetParent!==null});
 return {
  imgs: q('.imgSection img').map(i => ({file:i.src.split('?')[0].split('/').pop(), complete:i.complete, nw:i.naturalWidth, vis:i.offsetParent!==null})),
  imgSectionHTML: (document.querySelector('.imgSection')||{outerHTML:'NONE'}).outerHTML.replace(/(Signature|security-token|OSSAccessKeyId)=[^&"]*/g,'$1=X').slice(0,1500),
  input: q('#inputOne').map(info),
  submit: q('button.btnSubmit').map(info),
  buttons: q('button').map(info).filter(b => b.vis)
 };
}"""

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    for c in b.contexts:
        for pg in c.pages:
            if "wylkyj" in pg.url:
                print(json.dumps(pg.evaluate(JS), ensure_ascii=False, indent=1))
