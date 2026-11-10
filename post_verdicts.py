"""Post the candidates the Haiku judge called important to Slack: the title, linked, and its 1-2 sentence summary.

Usage: python post_verdicts.py state.json verdict.json [verdict.json ...]
Each verdict file is one new verdict ({id, important, summary}) from the push that started this run, so a
verdict is posted once. The articles behind an alarm stay in state.json; a 👀 reaction shows them.
"""

import json
import sys

from watcher import esc, post


def main() -> None:
    state = json.load(open(sys.argv[1], encoding="utf-8"))
    cands = {c["id"]: c for c in state.get("alarms", []) + state.get("pending", [])}
    for path in sys.argv[2:]:
        v = json.load(open(path, encoding="utf-8"))
        cand = cands.get(v.get("id"))
        if not cand or v.get("important") is not True:
            print("not posted:", v.get("id"), (cand or {}).get("title", "")[:90])
            continue
        link = (cand.get("articles") or [{}])[0].get("link", "")
        title = esc(cand["title"])
        head = f"*Breaking* · <{link}|{title}>" if link else f"*Breaking* · {title}"
        summary = esc(str(v.get("summary", "")).strip()[:300])
        text = head + (f"\n{summary}" if summary else "")
        blocks = [{"type": "section", "block_id": f"brk-{cand['id']}", "text": {"type": "mrkdwn", "text": text}}]
        posted = post(f"Breaking: {cand['title']}"[:900], blocks)
        print(("POSTED " if posted else "WOULD POST ") + cand["title"][:90])


if __name__ == "__main__":
    main()
