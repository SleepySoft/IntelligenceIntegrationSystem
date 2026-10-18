# -*- coding: utf-8 -*-
"""按 SKILL §6 验收清单验证 crawler_config：发现数 + 抽 2 篇/频道 验证正文。

用法: iis_env_python _validate_new_configs.py <name1> [name2 ...]
"""
import importlib.util
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
sys.path.insert(0, str(ROOT))

from IntelligenceCrawler.CrawlPipeline import build_pipeline

PROXY = 'socks5://127.0.0.1:10808'
MAX_PER_CHANNEL = 2


def load_config(name: str) -> dict:
    path = ROOT / 'CrawlTasks' / 'news' / f'crawler_config_{name}.py'
    spec = importlib.util.spec_from_file_location(f'crawler_config_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.CRAWLER_CONFIG)


def validate(name: str) -> bool:
    print(f'\n{"=" * 70}\n验证配置: {name}\n{"=" * 70}')
    config = load_config(name)
    config['d_fetcher_init_param']['proxy'] = PROXY
    config['e_fetcher_init_param']['proxy'] = PROXY
    config['d_fetcher_init_param']['log_callback'] = lambda *a: None
    config['e_fetcher_init_param']['log_callback'] = lambda *a: None
    config['extractor_init_param']['verbose'] = False

    pipeline = build_pipeline(name, config, lambda *a: None, None)

    entry_points = config['entry_points']
    if isinstance(entry_points, dict):
        channel_tables = {v: f'{name}/{k}' for k, v in entry_points.items()}
        entry_list = list(entry_points.values())
    else:
        channel_tables = {}
        entry_list = entry_points

    # 1. 发现频道
    pipeline.discover_channels(entry_point=entry_list, start_date=None, end_date=None,
                               fetcher_kwargs=config.get('d_fetcher_kwargs', {}))
    print(f'频道: {len(pipeline.channels)} 个')

    # 2. 发现文章
    articles = pipeline.discover_articles(channel_tables=channel_tables,
                                          fetcher_kwargs=config.get('d_fetcher_kwargs', {}))
    per_channel = Counter(group for _, group in articles)
    url_group = dict(articles)
    ok_discovery = True
    for group, count in sorted(per_channel.items()):
        mark = 'OK ' if count >= 5 else 'LOW'
        if count < 5:
            ok_discovery = False
        print(f'  [{mark}] {group}: {count} 篇文章链接')

    # 3. 每频道抽 2 篇验证正文
    taken = Counter()
    allowed = set()
    for url, group in articles:
        if taken[group] < MAX_PER_CHANNEL:
            taken[group] += 1
            allowed.add(url)

    results = []
    def handler(url, group, result):
        text_len = len(result.markdown_content or '')
        results.append({
            'group': group, 'url': url, 'success': result.success,
            'len': text_len, 'title': (result.metadata or {}).get('title'),
            'date': (result.metadata or {}).get('date'),
        })
        return True

    # 与生产一致：配置自带的 article_filter 与限量过滤组合生效
    config_filter = config.get('article_filter')
    pipeline.extract_articles(
        article_filter=lambda url: url in allowed and (config_filter(url) if config_filter else True),
        content_handler=handler,
        exception_handler=lambda url, e: True,
        fetcher_kwargs=config.get('e_fetcher_kwargs', {}),
        extractor_kwargs=config.get('extractor_kwargs', {}),
    )

    ok_extract = True
    for r in results:
        good = r['success'] and r['len'] >= 1000 and r['title']
        if not good:
            ok_extract = False
        mark = 'OK ' if good else 'BAD'
        print(f"  [{mark}] {r['group']:28s} len={r['len']:6d} date={str(r['date'])[:16]:16s} "
              f"{str(r['title'])[:55]}")

    verdict = ok_discovery and ok_extract and bool(results)
    print(f'>>> {name}: {"PASS" if verdict else "FAIL"}')
    return verdict


if __name__ == '__main__':
    names = sys.argv[1:] or ['guardian', 'euronews', 'moscowtimes', 'timesofindia', 'africanews']
    summary = {n: validate(n) for n in names}
    print('\n===== 汇总 =====')
    for n, ok in summary.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
