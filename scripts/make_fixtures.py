"""Write the messy demo inbox: 16 files, the kind nobody opens on purpose.

    python3 scripts/make_fixtures.py [target_dir]

All content is synthetic. Nothing here came off a real machine.
"""

import os
import sys

FILES = {
    "Untitled document (3).txt": (
        "random thoughts about the side project\n"
        "- maybe add a cache\n- ask someone about vector db\n"
        "- the deploy broke again on tuesday\n"
    ),
    "receipt-zomato-4491.txt": (
        "Zomato order #4491\nDate: 12 Oct 2026\nPaneer bowl 289.00\n"
        "Delivery 34.00\nTotal paid: 323.00 INR\nPayment: UPI\n"
    ),
    "mom bday.txt": (
        "mom's birthday 14 nov\nbook the cake from the place near sector 18\n"
        "she wants the chocolate one, not butterscotch\nremind dad two days before\n"
    ),
    "traceback.log": (
        'Traceback (most recent call last):\n  File "server.py", line 88, in <module>\n'
        "    main()\n  File \"server.py\", line 61, in main\n    srv.serve_forever()\n"
        "OSError: [Errno 98] Address already in use\n"
    ),
    "train.py": (
        "import torch\n\n\ndef train(model, loader, opt):\n    model.train()\n"
        "    for x, y in loader:\n        opt.zero_grad()\n"
        "        loss = torch.nn.functional.cross_entropy(model(x), y)\n"
        "        loss.backward()\n        opt.step()\n    return loss.item()\n"
    ),
    "rt campaign notes FINAL final v2.md": (
        "# RT campaign notes\n- budget 12k, spent 9.4k\n"
        "- CTR dropped after we changed the headline on the 9th\n"
        "- retarget the people who clicked but did not sign up\n"
        "- next review with Priya on friday 4pm\n"
    ),
    "copy of copy of budget october.csv": (
        "item,planned,actual\nads,8000,9400\ncontractor,15000,15000\ntools,1200,1180\n"
    ),
    "flight-ticket-delhi-bom.txt": (
        "IndiGo 6E 2043\nDEL -> BOM, 3 November 2026, 07:45\n"
        "PNR QX7T2M, seat 14A, web check-in opens 48h prior\n"
    ),
    "invoice-akshat-domain-renewal.txt": (
        "INVOICE\nFrom: Akshat Verma (freelance)\n"
        "For: domain renewal + DNS migration, amanpal.tech\n"
        "Amount: 899.00 INR\nDue: 30 October 2026\nGST included\n"
    ),
    "meeting-notes-standup.md": (
        "Standup 16 Oct\n- blocked on the payment webhook, waiting on sandbox keys\n"
        "- shipped the export fix\n- need to write tests for the retry path before friday demo\n"
    ),
    "Screenshot 2026-10-11 at 3.04.12 PM.png": "PNG\0\0binaryplaceholder\0\0",
    "tmpasdf.txt": "asdf\ntest\ntest123\n",
    "todo old.txt": (
        "call the bank about the KYC thing\nrenew the domain\nfix the export bug\n"
        "someday: read the tabpfn paper properly\n"
    ),
    "nn training experiment notes.md": (
        "# training run 7\nmodel: 4b q4, lr 2e-5, 1 epoch\nloss 1.42 -> 1.09\n"
        "eval acc 71.3% on 240 rows\n"
        "note: the 1b model was nearly as good and 4x faster, use it for classification\n"
    ),
    "grocery list.txt": "milk\neggs\npaneer\ncoffee\nonions\n",
    "bank statement sept.txt": (
        "Statement Sep 2026, A/c xx4412\nOpening 84,120.00\nSalary credit 1,20,000.00\n"
        "Rent debit 22,000.00\nClosing 1,71,890.00\n"
    ),
}


def main(target=None):
    target = target or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "inbox")
    os.makedirs(target, exist_ok=True)
    for name, body in FILES.items():
        with open(os.path.join(target, name), "w") as f:
            f.write(body)
    print(f"wrote {len(FILES)} files to {target}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
