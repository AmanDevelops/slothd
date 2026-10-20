"""Compare two local models on the same files, using the ledger as the baseline.

    python3 scripts/compare_models.py [other_model] [--var var]

Reads every filed file back from `sorted/`, re-classifies it with the other
model, and reports agreement and wall-clock time. Exists so the "you can swap
the brain" claim in the write-up is a measurement rather than a vibe.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slothd import clerk  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    other = args[0] if args else "gemma3:1b"
    var = os.path.join(HERE, "var")
    for i, a in enumerate(sys.argv):
        if a == "--var":
            var = sys.argv[i + 1]

    ledger = [json.loads(line) for line in open(os.path.join(var, "ledger.jsonl")) if line.strip()]
    rows = []
    t0 = time.time()
    for e in ledger:
        if not e.get("dst") or not os.path.exists(e["dst"]):
            continue
        text = clerk.read_text(e["dst"])
        start = time.time()
        verdict, meta = clerk.classify(text, e["name"], model=other)
        took = time.time() - start
        rows.append({"name": e["name"], "baseline": e["category"],
                     "other": verdict["category"], "agree": e["category"] == verdict["category"],
                     "seconds": round(took, 1), "source": meta.get("source")})
        print(f"{e['name'][:38]:38} {e['category']:10} vs {verdict['category']:10} "
              f"{'ok' if rows[-1]['agree'] else 'DIFF':4} {took:5.1f}s")

    agree = sum(1 for r in rows if r["agree"])
    out = {
        "baseline_model": ledger[0].get("model") if ledger else "?",
        "other_model": other,
        "files": len(rows),
        "agree": agree,
        "disagree": len(rows) - agree,
        "baseline_seconds": round(sum(e.get("seconds", 0) for e in ledger), 1),
        "other_seconds": round(sum(r["seconds"] for r in rows), 1),
        "wall_seconds": round(time.time() - t0, 1),
        "rows": rows,
    }
    path = os.path.join(var, f"bench-{other.replace(':', '-')}.json")
    json.dump(out, open(path, "w"), indent=1)
    print(f"\n{agree}/{len(rows)} agree · baseline "
          f"{out['baseline_seconds']}s vs {out['other_model']} {out['other_seconds']}s "
          f"· wrote {path}")


if __name__ == "__main__":
    main()
