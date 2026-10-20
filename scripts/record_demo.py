"""Record the slothd demo: dashboard live, plus a real "you start working" pause.

Run with the interpreter that has Playwright installed:
    python3 scripts/record_demo.py
Writes demo/slothd-demo.webm (converted to mp4/gif afterwards).
"""

import os
import shutil
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "demo", "raw")
URL = "http://127.0.0.1:8011/"
CHROME = os.path.expanduser(
    "~/.hermes/tools/chromium-1208/chrome-linux/chrome"
)

TITLE_CSS = """
#cap {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 99999;
  background: rgba(8,10,13,.93); border-top: 1px solid #2a333d;
  color: #e7ebf0; font: 500 19px/1.4 ui-sans-serif,system-ui,sans-serif;
  padding: 16px 26px; letter-spacing: .2px; white-space: pre;
}
#cap b { color: #9ad17a; font-weight: 600; }
#cap em { color: #8a96a3; font-style: normal; }
#card{position:fixed;inset:0;z-index:99998;background:#0f1216;display:flex;
  flex-direction:column;align-items:center;justify-content:center;gap:14px;
  transition:opacity .6s ease}
#card h1{font:650 64px/1 ui-sans-serif,system-ui,sans-serif;color:#e7ebf0;margin:0}
#card p{color:#8a96a3;font:400 20px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0;text-align:center}
#card .sloth{font-size:64px}
"""


def cap(page, text):
    page.evaluate(
        """(t) => {
            let c = document.getElementById('cap');
            if (!c) { c = document.createElement('div'); c.id='cap'; document.body.appendChild(c); }
            c.innerHTML = t;
        }""",
        text,
    )


def burn(seconds):
    """Pin both cores, the way a build or a video call would."""
    procs = [subprocess.Popen([sys.executable, "-c",
                               "while True: pass"]) for _ in range(2)]
    time.sleep(seconds)
    for p in procs:
        p.kill()


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=OUT,
            record_video_size={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle")
        page.add_style_tag(content=TITLE_CSS)
        page.evaluate(
            """() => {
                const c = document.createElement('div'); c.id = 'card';
                c.innerHTML = '<div class="sloth">🦥</div><h1>slothd</h1>' +
                  '<p>a chore daemon that only works while you are not using your machine</p>';
                document.body.appendChild(c);
            }"""
        )
        page.wait_for_timeout(3200)
        page.evaluate("() => { const c=document.getElementById('card'); c.style.opacity=0; setTimeout(()=>c.remove(), 700); }")
        page.wait_for_timeout(900)

        cap(page, "a folder nobody wants to open · <b>16 files</b> · receipts, logs, notes, screenshots")
        page.wait_for_timeout(4200)

        cap(page, "one local open-weight model reads each file · <b>gemma3:4b</b> on this machine · 0 bytes uploaded")
        page.wait_for_timeout(4200)

        cap(page, "it only works when <b>you</b> leave 70% of the machine free · its own inference is discounted")
        page.wait_for_timeout(4200)

        cap(page, "<em>now you start working…</em>")
        burn(9)
        page.wait_for_timeout(4000)
        cap(page, "it sees you and <b>drops the file mid-inference</b> · nothing half-filed, nothing lost")
        page.wait_for_timeout(4200)

        cap(page, "<em>you walk away again…</em>")
        page.wait_for_timeout(5000)
        cap(page, "you stop, it resumes the same file from the start · ledger, not memory")
        page.wait_for_timeout(4500)

        cap(page, "your reward · <b>two lines</b>, not a dashboard to read")
        page.wait_for_timeout(4600)

        # scroll to the ledger and show the decisions
        page.evaluate(
            """() => { const t = document.querySelector('table').getBoundingClientRect().top;
                       window.scrollTo({top: window.scrollY + t - 90, behavior:'smooth'}); }"""
        )
        cap(page, "every move is reversible · <b>undo</b> puts it all back")
        page.wait_for_timeout(4600)

        cap(page, "<em>slow and steady. that is the whole product.</em>")
        page.wait_for_timeout(3200)

        ctx.close()
        browser.close()
    print("recorded into", OUT)


if __name__ == "__main__":
    main()
