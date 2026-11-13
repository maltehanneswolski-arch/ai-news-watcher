"""Breaking AI news watcher: reads public RSS feeds every 10 minutes and raises an alarm in Slack.

Usage: python watcher.py [--dry-run] [--state state.json]

No AI model is involved. An alarm needs one of two signals:
  1. Several independent outlets: the same AI story from MIN_SOURCES or more different websites
     (feeds of one site, like NVIDIA's blog and newsroom, count once), published within
     WINDOW_MINUTES (titles grouped by their shared key words).
  2. An authority acts: an EU or national institution's feed publishes an AI item with a rule word
     (ruling, fine, ban, investigation, proposal, ...).
Newsletters and analysis blogs are read but do not count as independent outlets.

Each story alarms once (a story already alarmed in the last day is skipped), at most MAX_PER_DAY a day.
The alarm goes to the Slack channel in SLACK_CHANNEL with the bot token in SLACK_BOT_TOKEN; without
them, or with --dry-run, it is printed only. State (links seen, alarms sent) is kept in state.json,
which the workflow keeps on the "state" branch. Standard library only.
"""

from __future__ import annotations

import argparse
import urllib.parse
import email.utils
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USER_AGENT = "Mozilla/5.0 (compatible; AINewsScanner/1.0; +https://github.com/maltehanneswolski-arch)"
WINDOW_MINUTES = 180   # stories spread over hours; the watcher matters most overnight
MIN_SOURCES = 3
MAX_PER_DAY = 6
COUNTING = {"newsroom", "lab", "eu-institution", "institution", "eu-ai-policy"}
AUTHORITIES = {"eu-institution", "institution"}
AI_TERMS = re.compile(
    r"\bAI\b|\bA\.I\.|artificial intelligence|machine learning|\bLLMs?\b|language model|generative|chatbot|\bagents?\b|"
    r"\bagentic\b|deep learning|foundation model|OpenAI|Anthropic|DeepMind|Gemini|\bClaude\b|ChatGPT|Copilot|Mistral|"
    r"Llama|Hugging Face|Nvidia|\bGPUs?\b|semiconductor|\bchips?\b|data cent(?:re|er)s?|AI Act|\bAGI\b|\bGrok\b|\bxAI\b|"
    r"\bKI\b|Künstliche Intelligenz|intelligence artificielle|\bIA\b",
    re.IGNORECASE,
)
RULE_WORDS = re.compile(
    r"\b(ruling|fine[sd]?|ban(s|ned)?|investigat\w*|probe|decision|sanction\w*|export control\w*|enforcement|infringement)\b",
    re.IGNORECASE,
)
# For the authority rule a bare "agents" (travel agents) is not AI.
STRICT_AI = re.compile(AI_TERMS.pattern.replace(r"\bagents?\b|", ""), re.IGNORECASE)
# Outlets that count when they come through Google News (the wires and major newsrooms); any other
# outlet there is read but does not count.
GNEWS_OUTLETS = set("""reuters.com apnews.com bloomberg.com ft.com wsj.com nytimes.com washingtonpost.com economist.com
    politico.com politico.eu axios.com semafor.com theinformation.com cnbc.com cnn.com nbcnews.com cbsnews.com npr.org
    bbc.co.uk bbc.com theguardian.com theverge.com techcrunch.com wired.com arstechnica.com euractiv.com euronews.com
    dw.com france24.com lemonde.fr handelsblatt.com spiegel.de faz.net sueddeutsche.de heise.de nikkei.com scmp.com
    afp.com aljazeera.com""".split())
STOP = set("the a an and or of to in on for with by from at as is are was were be its it this that their his her into over after "
           "about than new says said will can could may more most how why what who amid via has have had not but just now "
           "report reports reportedly".split())


def words_of(title: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]+|\d+", title.lower()):
        if w in STOP or (len(w) < 3 and not w.isdigit()):
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


# ---- feeds ----------------------------------------------------------------

def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node: ET.Element, name: str) -> str:
    for c in node:
        if local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def child_link(node: ET.Element) -> str:
    for c in node:
        if local(c.tag) == "link":
            href = c.get("href")
            if href and c.get("rel", "alternate") == "alternate":
                return href.strip()
            if (c.text or "").strip():
                return c.text.strip()
    return ""


def parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        d = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fix_text(s: str) -> str:
    """Undo UTF-8 read as Windows-1252 ("â€™" for "’"), which some feeds send."""
    if "â€" in s or "Ã" in s:
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def read_feed(feed: dict) -> list[dict]:
    request = urllib.request.Request(feed["url"], headers={
        "User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.5",
        "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=15) as response:
        root = ET.fromstring(response.read(3_000_000))
    items = []
    for node in root.iter():
        if local(node.tag) not in ("item", "entry"):
            continue
        title = fix_text(re.sub(r"\s+", " ", child_text(node, "title")))
        link = child_link(node)
        when = parse_date(child_text(node, "pubdate") or child_text(node, "published") or child_text(node, "updated") or child_text(node, "date"))
        if not (title and link and when):
            continue
        item = {"feed": feed["name"], "group": feed.get("group", ""), "title": title, "link": link,
                "published": when.astimezone(timezone.utc)}
        src = next((c for c in node if local(c.tag) == "source" and c.get("url")), None)
        if feed.get("kind") == "gnews" and src is not None:
            # Google News: one item per outlet; the outlet is the <source>, the title ends in " - Outlet".
            name = (src.text or "").strip()
            item["title"] = re.sub(r"\s+-\s+" + re.escape(name) + r"$", "", title) if name else title
            item["feed"] = f"{name} (via Google News)"
            item["site"] = site(src.get("url"))
            item["group"] = "newsroom" if item["site"] in GNEWS_OUTLETS else "aggregated"
        items.append(item)
    return items


def read_all(feeds: list[dict]) -> tuple[list[dict], list[str]]:
    items, failed = [], []

    def one(feed):
        try:
            return read_feed(feed)
        except Exception as error:  # a broken feed must not stop the others
            failed.append(f"{feed['name']}: {type(error).__name__}")
            return []

    with ThreadPoolExecutor(16) as pool:
        for got in pool.map(one, feeds):
            items.extend(got)
    return items, failed


# ---- signals ----------------------------------------------------------------

def clusters(items: list[dict], df: dict[str, int], rare: float) -> list[list[dict]]:
    """Group titles that tell one story: three or more shared key words, 40% of the shorter title's,
    one of them rare across the day's titles."""
    groups: list[tuple[list[dict], set[str]]] = []
    for item in sorted(items, key=lambda x: x["published"]):
        bag = words_of(item["title"])
        for members, gbag in groups:
            shared = bag & gbag
            if len(shared) >= 3 and len(shared) >= .4 * min(len(bag), len(gbag)) and any(df.get(w, 0) <= rare for w in shared):
                members.append(item)
                gbag |= bag
                break
        else:
            groups.append(([item], set(bag)))
    return [m for m, _ in groups]


def same_story(a: set[str], b: set[str]) -> bool:
    shared = a & b
    return len(shared) >= 3 and len(shared) >= .4 * min(len(a), len(b))


def site(link: str) -> str:
    """The website a link belongs to: blogs.nvidia.com and nvidianews.nvidia.com are both nvidia.com."""
    parts = (urllib.parse.urlsplit(link).hostname or "").lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "ac", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def outlets_of(members: list[dict]) -> dict[str, str]:
    """One feed name per website, counting only outlets (not newsletters or analysis blogs)."""
    out: dict[str, str] = {}
    for m in members:
        if m["group"] in COUNTING:
            out.setdefault(m.get("site") or site(m["link"]), m["feed"])
    return out


# ---- slack ------------------------------------------------------------------

def post(text: str, blocks: list[dict]) -> bool:
    token, channel = os.environ.get("SLACK_BOT_TOKEN", ""), os.environ.get("SLACK_CHANNEL", "")
    if not token or not channel:
        return False
    body = json.dumps({"channel": channel, "text": text, "blocks": blocks, "unfurl_links": False, "unfurl_media": False}).encode()
    request = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(request, timeout=20) as response:
        reply = json.loads(response.read())
    if not reply.get("ok"):
        raise SystemExit(f"Slack refused the alarm: {reply.get('error')}")
    return True


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- run ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", default=str(ROOT / "state.json"))
    parser.add_argument("--window", type=int, default=WINDOW_MINUTES)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    feeds = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))["feeds"]
    state_path = Path(args.state)
    raw = state_path.read_text(encoding="utf-8").strip() if state_path.exists() else ""
    state = json.loads(raw) if raw else {"alarms": []}
    week = now - timedelta(days=7)
    state["alarms"] = [a for a in state.get("alarms", []) if datetime.fromisoformat(a["at"]) > week]
    state["near"] = [a for a in state.get("near", []) if datetime.fromisoformat(a["at"]) > week]

    items, failed = read_all(feeds)
    # The Google News searches overlap: keep one copy of each outlet's headline.
    seen: set[tuple[str, str]] = set()
    items = [x for x in items if not ((x.get("site") or x["link"]), x["title"].lower()) in seen
             and not seen.add(((x.get("site") or x["link"]), x["title"].lower()))]
    day = [x for x in items if x["published"] > now - timedelta(hours=24)]
    df: dict[str, int] = {}
    for x in day:
        for w in words_of(x["title"]):
            df[w] = df.get(w, 0) + 1
    rare = max(5, .01 * len(day))
    recent = [x for x in day if x["published"] > now - timedelta(minutes=args.window) and x["published"] <= now + timedelta(minutes=5)]
    recent_ai = [x for x in recent if AI_TERMS.search(x["title"])]

    candidates = []
    for members in clusters(recent_ai, df, rare):
        n = len(outlets_of(members))
        if n >= MIN_SOURCES:
            candidates.append(("sources", members))
        elif n == MIN_SOURCES - 1:  # near miss: one outlet short, kept a week to see how close it gets
            bag = set().union(*(words_of(m["title"]) for m in members))
            if not any(same_story(bag, set(a["words"])) for a in state["near"] + state["alarms"]):
                state["near"].append({"at": now.isoformat(), "title": members[0]["title"], "words": sorted(bag),
                                      "outlets": sorted(outlets_of(members).values())})
    for x in recent_ai:
        if x["group"] in AUTHORITIES and RULE_WORDS.search(x["title"]) and STRICT_AI.search(x["title"]):
            candidates.append(("authority", [x]))

    today = [a for a in state["alarms"] if datetime.fromisoformat(a["at"]) > now - timedelta(hours=24)]
    sent = []
    for kind, members in sorted(candidates, key=lambda c: -len(outlets_of(c[1]))):
        bag = set().union(*(words_of(m["title"]) for m in members))
        if any(same_story(bag, set(a["words"])) for a in today):
            continue
        if len(today) >= MAX_PER_DAY:
            print(f"Daily limit reached; not sent: {members[0]['title']}")
            continue
        lead = next((m for m in members if m["group"] in COUNTING), members[0])
        members = [lead] + [m for m in members if m is not lead]
        entry = {"id": f"c{int(now.timestamp())}{len(sent)}", "at": now.isoformat(), "kind": kind, "title": lead["title"],
                 "words": sorted(bag), "outlets": sorted(outlets_of(members).values()),
                 "articles": [{"feed": m["feed"], "title": m["title"], "link": m["link"]} for m in members][:8]}
        print("TO JUDGE " + lead["title"])
        today.append(entry)
        if not args.dry_run:
            state["alarms"].append(entry)
            state.setdefault("pending", []).append(entry)
        sent.append(entry)

    state["pending"] = [p for p in state.get("pending", []) if datetime.fromisoformat(p["at"]) > now - timedelta(hours=6)]
    if sent and not args.dry_run:
        (ROOT / "judge.flag").write_text(" ".join(e["id"] for e in sent), encoding="utf-8")
    state["last_run"] = {"at": now.isoformat(), "feeds": len(feeds), "failed": failed, "items_24h": len(day),
                         "ai_items_window": len(recent_ai), "candidates": len(candidates), "alarms": len(sent)}
    if not args.dry_run:
        state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Read {len(feeds) - len(failed)}/{len(feeds)} feeds, {len(day)} items in 24 h, {len(recent_ai)} AI items in the last "
          f"{args.window} min, {len(candidates)} candidate(s), {len(sent)} alarm(s).")
    if failed:
        print("Failed: " + "; ".join(failed))


if __name__ == "__main__":
    main()
