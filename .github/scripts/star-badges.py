#!/usr/bin/env python3
"""Pre-render the README's "stars" badges into the data branch.

A live shields.io badge (img.shields.io/github/stars/...) asks GitHub for the
count on every README view, through GitHub's image proxy. When shields answers
slowly the proxy gives up and the README shows "Error Fetching Resource".

This runs in the six-hourly data workflow after github-snapshot.py. For every
open-source repository in github.json it asks shields.io once for the static
badge with the same look (label "stars", flat-square, GitHub logo, #2858d0,
the count shortened as shields does) and writes it to
<out>/stars-<owner>-<repo>.svg. If shields cannot be reached, the copy already
on the data branch is kept, so a published badge never disappears.

Usage: star-badges.py --data dist/github.json --out dist/badges --owner YiyuZh
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

COLOR = "2858d0"
PREFIXES = "kMGTPEZY"
USER_AGENT = "zenithy-profile-badges"


def metric(n):
    """The count as shields writes it: 359, 1.5k, 31k."""
    n = int(n)
    for i in range(len(PREFIXES), 0, -1):
        limit = 1000 ** i
        if abs(n) >= limit:
            scaled = abs(n) / limit
            if scaled < 10:
                one = f"{scaled:.1f}"
                if not one.endswith("0"):
                    return ("-" if n < 0 else "") + one + PREFIXES[i - 1]
            rounded = round(scaled)
            text = f"{rounded}{PREFIXES[i - 1]}" if rounded < 1000 else f"1{PREFIXES[i]}"
            return ("-" if n < 0 else "") + text
    return str(n)


def badge_name(repo):
    return "stars-" + re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-") + ".svg"


def fetch(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status == 200:
                    return response.read()
        except Exception as error:  # noqa: BLE001 - any failure means "try again, then fall back"
            print(f"  {url}: {error}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return None


def is_badge(body, message):
    text = (body or b"").decode("utf-8", "replace")
    return text.lstrip().startswith("<svg") and f">{message}<" in text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    data = json.load(open(args.data, encoding="utf-8"))
    missing = 0
    for item in data.get("openSource") or []:
        repo, stars = item.get("repo"), item.get("stars")
        if not repo or stars is None:
            continue
        name = badge_name(repo)
        message = metric(stars)
        url = ("https://img.shields.io/badge/stars-" + urllib.parse.quote(message.replace("-", "--"))
               + f"-{COLOR}?style=flat-square&logo=github")
        body = fetch(url)
        source = "shields"
        if not is_badge(body, message):
            previous = f"https://raw.githubusercontent.com/{args.owner}/{args.owner}/data/badges/{name}"
            body = fetch(previous, tries=2)
            source = "previous copy"
            if not (body or b"").lstrip().startswith(b"<svg"):
                print(f"{repo}: no badge (shields and the previous copy both failed)", file=sys.stderr)
                missing += 1
                continue
        with open(os.path.join(args.out, name), "wb") as handle:
            handle.write(body)
        print(f"{repo}: {stars} -> {message} ({source}) -> {name}")
    if missing:
        print(f"{missing} badge(s) missing; github.json is published anyway", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
