"""Read-only, credential-free GitHub/PyPI/npm evidence collector. No project code runs."""
import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / 'docs' / 'evidence'
REPOS = [
    'davidteather/TikTok-Api', 'Russell-Newton/TikTokPy', 'dfreelon/pyktok',
    'wkaisertexas/tiktok-uploader', 'Evil0ctal/Douyin_TikTok_Download_API',
    'drawrowfly/tiktok-scraper', 'tiktok/tiktok-research-api-wrapper',
    'tiktok/tiktok-business-api-sdk', 'adinagit/tiktok-research-api-python',
]

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'LeadHunter-SourceReview/1.0'})
    with urllib.request.urlopen(req, timeout=40) as response:
        return response.read()

def json_get(url):
    return json.loads(get(url))

def repo(name):
    base = f'https://api.github.com/repos/{name}'
    try:
        meta = json_get(base)
        sha = json_get(base + '/commits?per_page=1')[0]['sha']
        tree = json_get(base + f'/git/trees/{sha}?recursive=1')['tree']
        issues = json_get(base + '/issues?state=open&per_page=5&sort=updated')
        paths = [x['path'] for x in tree if x['type'] == 'blob']
        target = ROOT / name.replace('/', '__')
        target.mkdir(parents=True, exist_ok=True)
        (target / 'tree.json').write_text(json.dumps(paths, indent=2), encoding='utf-8')
        # Small source samples; extra paths can be retrieved at the same pinned SHA later.
        candidates = [p for p in paths if p.lower().endswith(('.py', '.ts', '.js'))
                      and any(k in p.lower() for k in ('search', 'comment', 'api.py', 'client.py', 'upload.py', 'pyktok.py', 'test'))]
        candidates = sorted(candidates, key=lambda p: (('test' in p.lower()), len(p)))[:6]
        if not (meta.get('license') or {}).get('spdx_id'):
            candidates = []  # Retain metadata/links, not an unlicensed source distribution.
        licenses = [p for p in paths if p.lower() in ('license', 'license.md', 'license.txt')]
        samples = []
        for path in licenses + candidates:
            url = f'https://raw.githubusercontent.com/{name}/{sha}/{path}'
            try:
                data = get(url)
                dest = target / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                samples.append({'path': path, 'url': url, 'sha256': hashlib.sha256(data).hexdigest()})
            except Exception as exc:
                samples.append({'path': path, 'error': type(exc).__name__})
        return {'repo': name, 'url': meta['html_url'], 'stars': meta['stargazers_count'],
                'open_issues_and_prs': meta['open_issues_count'], 'pushed_at': meta['pushed_at'],
                'updated_at': meta['updated_at'], 'archived': meta['archived'],
                'license': (meta.get('license') or {}).get('spdx_id'), 'sha': sha,
                'recent_open_items': [{'title': i['title'], 'url': i['html_url'],
                                       'updated_at': i['updated_at'], 'is_pr': 'pull_request' in i} for i in issues],
                'samples': samples}
    except Exception as exc:
        return {'repo': name, 'error': str(exc)}

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        repos = list(pool.map(repo, REPOS))
    registries = []
    for name in ['TikTokApi', 'tiktokapipy', 'pyktok', 'tiktok-uploader', 'TikTokResearchApi']:
        try:
            data = json_get(f'https://pypi.org/pypi/{name}/json')
            info = data['info']
            registries.append({'registry': 'PyPI', 'name': name, 'version': info['version'],
                               'requires_python': info['requires_python'], 'project_urls': info['project_urls'],
                               'uploaded_at': [x['upload_time_iso_8601'] for x in data['urls']]})
        except Exception as exc:
            registries.append({'name': name, 'error': str(exc)})
    try:
        data = json_get('https://registry.npmjs.org/tiktok-scraper')
        latest = data['dist-tags']['latest']
        registries.append({'registry': 'npm', 'name': 'tiktok-scraper', 'version': latest,
                           'published_at': data['time'][latest], 'repository': data['versions'][latest].get('repository')})
    except Exception as exc:
        registries.append({'registry': 'npm', 'error': str(exc)})
    result = {'captured_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'repos': repos, 'registries': registries}
    (ROOT / 'snapshot.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for entry in repos:
        print(json.dumps({k: v for k, v in entry.items() if k != 'samples'}, ensure_ascii=False))
    print(json.dumps(registries, ensure_ascii=False))

if __name__ == '__main__':
    main()
