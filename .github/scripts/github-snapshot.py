"""Write site/data/github.json: the public GitHub picture the homepage shows.

Everything comes from what any visitor can see, so private activity never leaks:
- the contribution calendar from github.com/users/<login>/contributions (the
  same HTML the public profile renders);
- public, non-fork repositories and their stars from the REST API;
- pull requests opened on other people's repositories, from the search API.

It runs every few hours in the profile repository (YiyuZh/YiyuZh,
.github/workflows/github-data.yml, next to a copy of this file) and publishes the
result to that repository's "data" branch, which the Portal API reads through
api.github.com. Run it locally to refresh the bundled fallback before a deploy:
    python scripts/github-snapshot.py [--login YiyuZh] [--calendar-html saved.html] [--out path]
With GITHUB_TOKEN set (the Actions token), REST calls are authenticated so a
shared runner does not hit the anonymous rate limit; they only read public data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'site' / 'data' / 'github.json'
UA = {'User-Agent': 'zenithy-portal-snapshot', 'Accept': 'application/vnd.github+json'}


def get(url: str) -> bytes:
    """GET, authenticated for api.github.com when GITHUB_TOKEN is set. Falls back to
    curl, which follows the system proxy setup more reliably on Windows than urllib."""
    request_headers = dict(UA)
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if token and url.startswith('https://api.github.com/'):
        request_headers['Authorization'] = f'Bearer {token}'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=request_headers), timeout=90) as response:
            return response.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        headers = [arg for key, value in request_headers.items() for arg in ('-H', f'{key}: {value}')]
        return subprocess.run(['curl', '-sS', '--fail', '--max-time', '120', *headers, url],
                              check=True, capture_output=True).stdout


def calendar(page: str) -> dict:
    """Days, levels and counts from the public contributions fragment."""
    levels = {m['id']: (m['date'], int(m['level'])) for m in re.finditer(
        r'data-date="(?P<date>\d{4}-\d{2}-\d{2})"\s+id="(?P<id>contribution-day-component-[\d-]+)"\s+data-level="(?P<level>\d)"', page)}
    counts = {}
    for m in re.finditer(r'<tool-tip[^>]*\sfor="(?P<id>contribution-day-component-[\d-]+)"[^>]*>(?P<text>[^<]*)<', page):
        text = html.unescape(m['text']).strip()
        number = re.match(r'(\d[\d,]*) contributions?', text)
        counts[m['id']] = int(number.group(1).replace(',', '')) if number else 0
    if len(levels) < 300:
        raise SystemExit(f'calendar markup changed: only {len(levels)} days found')
    days = sorted(({'d': date, 'c': counts.get(key, 0), 'l': level} for key, (date, level) in levels.items()), key=lambda day: day['d'])
    longest = run = 0
    for day in days:
        run = run + 1 if day['c'] else 0
        longest = max(longest, run)
    return {
        'start': days[0]['d'],
        'end': days[-1]['d'],
        'total': sum(day['c'] for day in days),
        'activeDays': sum(1 for day in days if day['c']),
        'longestStreak': longest,
        'days': days,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--login', default='YiyuZh')
    parser.add_argument('--calendar-html', help='a saved copy of github.com/users/<login>/contributions')
    parser.add_argument('--out', type=pathlib.Path, default=OUT, help='where to write the JSON')
    args = parser.parse_args()
    login = args.login
    out = args.out

    page = (pathlib.Path(args.calendar_html).read_text(encoding='utf-8') if args.calendar_html
            else get(f'https://github.com/users/{login}/contributions').decode('utf-8'))
    repos = [repo for repo in json.loads(get(f'https://api.github.com/users/{login}/repos?per_page=100&type=owner'))
             if not repo['fork'] and not repo['private'] and repo['name'].lower() != login.lower()]
    query = f'https://api.github.com/search/issues?q=type:pr+author:{login}+-user:{login}&per_page=100'
    search = json.loads(get(query))
    items = list(search['items'])
    page_number = 2
    # The search API pages by 100 and stops at 1000 results.
    while len(items) < min(search['total_count'], 1000) and page_number <= 10:
        batch = json.loads(get(f'{query}&page={page_number}'))['items']
        if not batch:
            break
        items += batch
        page_number += 1

    upstream: dict[str, dict] = {}
    for item in items:
        name = item['repository_url'].split('/repos/', 1)[1]
        entry = upstream.setdefault(name, {'repo': name, 'url': f'https://github.com/{name}', 'prs': 0, 'merged': 0,
                                           'open': 0, 'pulls': []})
        entry['prs'] += 1
        if (item.get('pull_request') or {}).get('merged_at'):
            entry['merged'] += 1
            state = 'merged'
        elif item['state'] == 'open':
            entry['open'] += 1
            state = 'open'
        else:
            state = 'closed'
        # Each pull request, so the About page can list them by state.
        entry['pulls'].append({'number': item['number'], 'title': item['title'], 'state': state, 'url': item['html_url']})
    for entry in upstream.values():
        entry['pulls'].sort(key=lambda pull: -pull['number'])
        detail = json.loads(get(f'https://api.github.com/repos/{entry["repo"]}'))
        entry['stars'] = detail['stargazers_count']
        entry['description'] = detail.get('description') or ''
        entry['pullsUrl'] = f'https://github.com/{entry["repo"]}/pulls?q=is%3Apr+author%3A{login}'

    snapshot = {
        'login': login,
        'url': f'https://github.com/{login}',
        'updatedAt': dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'stars': sum(repo['stargazers_count'] for repo in repos),
        'publicRepos': len(repos),
        'pullRequests': search['total_count'],
        'openSource': sorted(upstream.values(), key=lambda entry: -entry['stars']),
        'repos': [{'name': repo['name'], 'url': repo['html_url'], 'stars': repo['stargazers_count'],
                   'language': repo.get('language') or '', 'description': repo.get('description') or ''}
                  for repo in sorted(sorted(repos, key=lambda repo: repo['pushed_at'], reverse=True),
                                     key=lambda repo: -repo['stargazers_count'])[:6]],
        'calendar': calendar(page),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8', newline='\n')
    c = snapshot['calendar']
    print(f"{out}: {c['total']} contributions, {c['activeDays']} active days, longest {c['longestStreak']}, "
          f"{snapshot['stars']} stars over {snapshot['publicRepos']} repos, {snapshot['pullRequests']} PRs upstream")


if __name__ == '__main__':
    main()
