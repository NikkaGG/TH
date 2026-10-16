"""Pin additional implementation/test samples used in the review; never execute them."""
import hashlib
import json
from research_snapshot import ROOT, get

selections = {
    'davidteather/TikTok-Api': ['TikTokApi/tiktok.py', 'tests/test_search.py', 'tests/test_comments.py'],
    'tiktok/tiktok-business-api-sdk': ['python_sdk/business_api_client/api/comments_api.py',
                                      'python_sdk/business_api_client/models/comment_post_body.py',
                                      'yml_files/comment_post.yml'],
    'Evil0ctal/Douyin_TikTok_Download_API': ['src/dtk/platforms/tiktok/adapter.py',
                                          'src/dtk/platforms/tiktok/endpoints.py',
                                          'src/dtk/platforms/tiktok/parser.py',
                                          'tests/replay/test_tiktok_parser.py',
                                          'tests/contract/test_live_endpoints.py'],
    'drawrowfly/tiktok-scraper': ['src/core/TikTok.ts', 'src/core/TikTok.test.ts', 'package.json', 'LICENSE'],
    'adinagit/tiktok-research-api-python': ['tiktok-video-hash-data.py'],
}
snapshot = json.loads((ROOT / 'snapshot.json').read_text(encoding='utf-8'))
for repo in snapshot['repos']:
    paths = json.loads((ROOT / repo['repo'].replace('/', '__') / 'tree.json').read_text())
    for path in selections.get(repo['repo'], []):
        if path not in paths:
            print('Not present:', repo['repo'], path)
            continue
        url = f"https://raw.githubusercontent.com/{repo['repo']}/{repo['sha']}/{path}"
        data = get(url)
        dest = ROOT / repo['repo'].replace('/', '__') / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        can_store = bool(repo.get('license')) or path.endswith('.json')
        if can_store:
            dest.write_bytes(data)
        elif dest.exists():
            dest.unlink()  # Only this explicitly named research sample created by this tool.
        repo['samples'] = [sample for sample in repo['samples'] if sample['path'] != path]
        repo['samples'].append({'path': path, 'url': url, 'sha256': hashlib.sha256(data).hexdigest(),
                                'stored_locally': can_store})
        print(repo['repo'], path, len(data))
(ROOT / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
