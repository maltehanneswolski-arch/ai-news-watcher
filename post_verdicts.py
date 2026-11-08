"""Post the candidates the Haiku judge called important (verdicts.json) to Slack, with its reason.

Usage: python post_verdicts.py state.json verdicts.json
"""

import json
import sys

from watcher import esc, post


def main() -> None:
    state = json.load(open(sys.argv[1], encoding="utf-8"))
    verdicts = {v["id"]: v for v in json.load(open(sys.argv[2], encoding="utf-8")) if isinstance(v, dict) and "id" in v}
    for cand in state.get("pending", []):
        v = verdicts.get(cand["id"])
        if not v or v.get("important") is not True:
            print("not posted:", cand["title"][:90])
            continue
        blocks = cand["blocks"]
        line = str(v.get("line", "")).strip()[:200]
        if line:
            blocks = blocks[:1] + [{"type": "section", "text": {"type": "mrkdwn", "text": esc(line)}}] + blocks[1:]
        posted = post(cand["text"], blocks)
        print(("POSTED " if posted else "WOULD POST ") + cand["title"][:90])


if __name__ == "__main__":
    main()
