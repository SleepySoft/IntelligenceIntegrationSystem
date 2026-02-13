# IntelligenceCrawler/CrawlPipeline.py
# -*- coding: utf-8 -*-

"""
Project-agnostic crawling pipeline (orchestrator).

This pipeline composes Discoverer + Fetcher + Extractor and exposes:
- Sink (event aggregator) for monitoring/statistics/persistence/caching.
- Policy for rate limiting / dedup / round lifecycle.
- ErrorPolicy for error classification (transient/permanent) without external exceptions.

Design principles:
- No dependency on project-specific governance modules.
- No dependency on ProcessCotrolException (removed).
- No grouping_key_for_channel: group_key is provided by caller via resolver/map.
- Backward compatibility for article_filter/content_handler/exception_handler.
- Two modes: "batch" (3-stage) and "streaming" (discover-then-extract).

Typical usage pattern:
    pipeline = build_pipeline(name, config, log_callback, crawler_governor=None)
    drive_pipeline(pipeline, config)
"""

from __future__ import annotations

import datetime
import traceback
from dataclasses import dataclass
from typing import (
    Protocol, Callable, Optional, List, Tuple, Dict, Any, Iterable, Set
)
from urllib.parse import urlparse
from collections import defaultdict

from IntelligenceCrawler.Discoverer import IDiscoverer, discoverer_factory
from IntelligenceCrawler.Extractor import IExtractor, ExtractionResult, extractor_factory
from IntelligenceCrawler.Fetcher import Fetcher, fetcher_factory


# ============================== Data Models ==============================

@dataclass(frozen=True)
class Channel:
    """Represents a discovered channel (URL + externally provided group key)."""
    url: str
    group_key: str


@dataclass(frozen=True)
class Article:
    """Represents a discovered article with provenance."""
    url: str
    channel_url: str
    group_key: str
    discovered_at: datetime.datetime


# ============================== Interfaces ==============================

class PipelineSink(Protocol):
    """
    Aggregates all pipeline events for monitoring, metrics, persistence, caching, etc.
    Implementations can forward events to governance/DB/logging systems.
    """

    # Run lifecycle
    def on_run_start(self, run_id: str, name: str, at: datetime.datetime) -> None: ...
    def on_run_end(self, run_id: str, name: str, at: datetime.datetime, stats: Dict[str, Any]) -> None: ...

    # Discovery
    def on_channel_discovered(self, channel: Channel) -> None: ...
    def on_article_discovered(self, article: Article) -> None: ...

    # Fetch/Extract
    def on_fetch_start(self, url: str, group_key: str) -> None: ...
    def on_fetch_skip(self, url: str, reason: str) -> None: ...
    def on_fetch_success(self, url: str, bytes_len: int) -> None: ...
    def on_extract_success(self, url: str, result: ExtractionResult) -> None: ...

    # Errors
    def on_error(self, url: str, error: Exception, transient: bool, context: Dict[str, Any]) -> None: ...
    def on_dead_letter(self, url: str, error: Exception, context: Dict[str, Any]) -> None: ...


class CrawlPolicy(Protocol):
    """
    Encapsulates rate limiting & dedup decisions (group_key is passed in from outside).
    """

    def should_crawl(self, article_url: str) -> bool: ...
    def allow_now(self, group_key: str) -> bool: ...
    def notify_round_start(self, group_key: str, total: int) -> None: ...
    def notify_round_finish(self, group_key: str) -> None: ...


class ErrorPolicy(Protocol):
    """
    Classifies exceptions and decides whether they are transient (retryable).
    """

    def classify(self, exc: Exception) -> Tuple[bool, str]:
        """
        Returns (transient, code).
        transient=True   -> retryable
        transient=False  -> permanent failure
        code: a string used by downstream (e.g., "timeout", "http_404", "extract_error").
        """
        ...


# ============================== Default Implementations ==============================

class NoopSink:
    """No-op sink: safe default for quick start or testing."""
    def on_run_start(self, run_id: str, name: str, at: datetime.datetime) -> None: ...
    def on_run_end(self, run_id: str, name: str, at: datetime.datetime, stats: Dict[str, Any]) -> None: ...
    def on_channel_discovered(self, channel: Channel) -> None: ...
    def on_article_discovered(self, article: Article) -> None: ...
    def on_fetch_start(self, url: str, group_key: str) -> None: ...
    def on_fetch_skip(self, url: str, reason: str) -> None: ...
    def on_fetch_success(self, url: str, bytes_len: int) -> None: ...
    def on_extract_success(self, url: str, result: ExtractionResult) -> None: ...
    def on_error(self, url: str, error: Exception, transient: bool, context: Dict[str, Any]) -> None: ...
    def on_dead_letter(self, url: str, error: Exception, context: Dict[str, Any]) -> None: ...


class SimpleCrawlPolicy:
    """
    Minimal policy:
    - always allow crawling
    - no rate limiting
    - emits no-op round notifications
    """
    def should_crawl(self, article_url: str) -> bool:
        return True

    def allow_now(self, group_key: str) -> bool:
        return True

    def notify_round_start(self, group_key: str, total: int) -> None:
        ...

    def notify_round_finish(self, group_key: str) -> None:
        ...


class DefaultErrorPolicy:
    """
    Naive error policy:
    - Treats all errors as transient by default (retryable).
    - Customize this to map network/HTTP/parse errors as needed.
    """
    def classify(self, exc: Exception) -> Tuple[bool, str]:
        return True, exc.__class__.__name__


# ============================== Pipeline Core ==============================

class CrawlPipeline:
    """
    Stateful orchestrator that composes Discoverer + Fetcher + Extractor.
    It delegates monitoring/rate-limiting/error decisions to Sink/Policy interfaces.

    IMPORTANT:
    - No ProcessCotrolException usage.
    - No grouping_key_for_channel: group_key must be provided externally via resolver or map.
    """

    def __init__(self,
                 name: str,
                 d_fetcher: Fetcher,
                 discoverer: IDiscoverer,
                 e_fetcher: Fetcher,
                 extractor: IExtractor,
                 log_callback: Callable[..., None] = print,
                 sink: Optional[PipelineSink] = None,
                 policy: Optional[CrawlPolicy] = None,
                 error_policy: Optional[ErrorPolicy] = None,
                 channel_group_resolver: Optional[Callable[[str], str]] = None):
        """
        Args:
            name: Pipeline name.
            d_fetcher: Fetcher for discovery.
            discoverer: Channel/article discoverer.
            e_fetcher: Fetcher for content download.
            extractor: Content extractor.
            log_callback: Logging function.
            sink: Event aggregator implementation.
            policy: Rate-limiting/dedup strategy (group_key is provided externally).
            error_policy: Error classification strategy.
            channel_group_resolver: Callable that returns group_key for a given channel URL.
                                   If not provided, a stable fallback will be used (netloc[/first-segment]).
        """
        self.name = name
        self.d_fetcher = d_fetcher
        self.discoverer = discoverer
        self.e_fetcher = e_fetcher
        self.extractor = extractor
        self.log = log_callback

        self.sink: PipelineSink = sink or NoopSink()
        self.policy: CrawlPolicy = policy or SimpleCrawlPolicy()
        self.error_policy: ErrorPolicy = error_policy or DefaultErrorPolicy()
        self.channel_group_resolver = channel_group_resolver

        # Internal state
        self.channels: List[Channel] = []
        self.articles: List[Article] = []
        self.results: List[Tuple[str, ExtractionResult]] = []  # (article_url, result)

    # ---------------- Lifecycle ----------------

    def shutdown(self) -> None:
        """Gracefully close both fetchers."""
        self.log("--- Shutting down fetchers ---")
        try:
            if self.d_fetcher:
                self.d_fetcher.close()
        except Exception as e:
            self.log(f"[Error] Failed to close discovery fetcher: {e}")

        try:
            if self.e_fetcher and self.e_fetcher is not self.d_fetcher:
                self.e_fetcher.close()
        except Exception as e:
            self.log(f"[Error] Failed to close extraction fetcher: {e}")

    # ---------------- Stage 1: Channel Discovery ----------------

    def discover_channels(self,
                          entry_points: List[str],
                          start_date: Optional[datetime.datetime] = None,
                          end_date: Optional[datetime.datetime] = None,
                          fetcher_kwargs: Optional[Dict[str, Any]] = None) -> List[Channel]:
        """
        Discover channels from entry points and populate self.channels.

        Notes:
        - group_key is assigned by channel_group_resolver if provided;
          otherwise, we use a stable fallback (netloc[/first path segment]).
        """
        if fetcher_kwargs is None:
            fetcher_kwargs = {}

        self.channels.clear()
        self.sink.on_run_start(run_id=self._run_id(), name=self.name, at=datetime.datetime.now())
        self.log(f"--- 1. Discover Channels from {len(entry_points)} entry point(s) ---")

        discovered: List[Channel] = []
        for ep in entry_points:
            self.log(f"Scanning entry point: {ep}")
            try:
                urls: Iterable[str] = self.discoverer.discover_channels(
                    entry_point=ep,
                    start_date=start_date,
                    end_date=end_date,
                    fetcher_kwargs=fetcher_kwargs
                )
                for ch_url in urls:
                    group_key = self._resolve_group_key(ch_url)
                    ch = Channel(url=ch_url, group_key=group_key)
                    discovered.append(ch)
                    self.sink.on_channel_discovered(ch)
            except Exception as e:
                self.log(f"[Error] Failed to discover from {ep}: {e}\n{traceback.format_exc()}")

        # Deduplicate while preserving order
        seen: Set[str] = set()
        unique: List[Channel] = []
        for ch in discovered:
            if ch.url not in seen:
                seen.add(ch.url)
                unique.append(ch)

        self.channels = unique
        self.log(f"Found {len(self.channels)} unique channels in total.")
        return self.channels

    # ---------------- Stage 2: Article Discovery ----------------

    def discover_articles(self,
                          channel_filter: Optional[Callable[[str], bool]] = None,
                          fetcher_kwargs: Optional[Dict[str, Any]] = None) -> List[Article]:
        """
        Discover articles from channels and populate self.articles.

        channel_filter(channel_url) -> bool:
            Return False to skip a channel (e.g., user-selected whitelist).
        """
        if fetcher_kwargs is None:
            fetcher_kwargs = {}

        self.articles.clear()
        self.log(f"--- 2. Discover Articles from {len(self.channels)} channel(s) ---")

        seen_articles: Set[str] = set()
        grouped_channels: Dict[str, List[str]] = defaultdict(list)  # group_key -> list[channel_url]

        # Pre-group channels for round notifications
        for ch in self.channels:
            if channel_filter and not channel_filter(ch.url):
                self.sink.on_fetch_skip(ch.url, "channel-filtered")
                continue
            grouped_channels[ch.group_key].append(ch.url)

        for group_key, ch_urls in grouped_channels.items():
            for ch_url in ch_urls:
                try:
                    article_urls: Iterable[str] = self.discoverer.get_articles_for_channel(ch_url, fetcher_kwargs)
                    temp_list = list(article_urls)
                    self.policy.notify_round_start(group_key, len(temp_list))

                    added = 0
                    for art_url in temp_list:
                        if art_url in seen_articles:
                            self.sink.on_fetch_skip(art_url, "dedup")
                            continue
                        seen_articles.add(art_url)

                        art = Article(
                            url=art_url,
                            channel_url=ch_url,
                            group_key=group_key,
                            discovered_at=datetime.datetime.now()
                        )
                        self.articles.append(art)
                        self.sink.on_article_discovered(art)
                        added += 1

                    self.log(f"[{group_key}] Found {added} new article(s) in channel: {ch_url}")
                except Exception as e:
                    self.log(f"[Error] Failed to process channel {ch_url}: {e}\n{traceback.format_exc()}")
                finally:
                    self.policy.notify_round_finish(group_key)

        self.log(f"Discovered {len(self.articles)} unique articles.")
        return self.articles

    # ---------------- Stage 3: Extraction ----------------

    def extract_articles(self,
                         article_filter: Optional[Callable[[str, str], bool]] = None,
                         content_handler: Optional[Callable[[str, ExtractionResult], None]] = None,
                         exception_handler: Optional[Callable[[str, Exception], None]] = None,
                         fetcher_kwargs: Optional[Dict[str, Any]] = None,
                         extractor_kwargs: Optional[Dict[str, Any]] = None) -> List[Tuple[str, ExtractionResult]]:
        """
        Extract content from discovered articles.

        Backward compatibility:
            - article_filter(url, group_key) returning False will skip the article.
            - content_handler(url, result) is invoked after successful extraction.
            - exception_handler(url, exc) is invoked on any exception.
        """
        if fetcher_kwargs is None:
            fetcher_kwargs = {}
        if extractor_kwargs is None:
            extractor_kwargs = {}

        self.results.clear()
        self.log(f"--- 3. Fetch & Extract {len(self.articles)} article(s) ---")

        grouped_articles: Dict[str, List[Article]] = defaultdict(list)
        for art in self.articles:
            grouped_articles[art.group_key].append(art)

        for group_key, articles in grouped_articles.items():
            self.policy.notify_round_start(group_key, len(articles))
            for art in articles:
                url = art.url

                # External/project filter (e.g., cache hit handling outside the pipeline)
                if article_filter and not article_filter(url, group_key):
                    self.sink.on_fetch_skip(url, "filtered")
                    continue

                # Policy-level dedup/permission
                if not self.policy.should_crawl(url):
                    self.sink.on_fetch_skip(url, "should-not-crawl")
                    continue

                # Rate limiting
                if not self.policy.allow_now(group_key):
                    self.sink.on_fetch_skip(url, "rate-limited")
                    continue

                self.sink.on_fetch_start(url, group_key)
                self.log(f"Processing: {url}")

                try:
                    content = self.e_fetcher.get_content(url, **fetcher_kwargs)
                    if not content:
                        self.sink.on_fetch_skip(url, "empty-content")
                        continue

                    self.sink.on_fetch_success(url, len(content))
                    result = self.extractor.extract(content, url, **extractor_kwargs)

                    self.sink.on_extract_success(url, result)
                    self.results.append((url, result))

                    if content_handler:
                        content_handler(url, result)

                except Exception as e:
                    # Legacy callback for compatibility
                    if exception_handler:
                        exception_handler(url, e)

                    transient, code = self.error_policy.classify(e)
                    self.log(f"[Error] Extraction failed for {url}: {e}")
                    self.sink.on_error(url, e, transient, {"code": code})

                    if not transient:
                        self.sink.on_dead_letter(url, e, {"code": code})

            self.policy.notify_round_finish(group_key)

        self.log(f"Extracted {len(self.results)} article(s) successfully.")
        self.sink.on_run_end(
            run_id=self._run_id(),
            name=self.name,
            at=datetime.datetime.now(),
            stats={
                "channels": len(self.channels),
                "articles": len(self.articles),
                "extracted": len(self.results)
            }
        )
        return self.results

    # ---------------- Streaming Mode (optional) ----------------

    def stream_discover_and_extract(self,
                                    entry_points: List[str],
                                    channel_filter: Optional[Callable[[str], bool]] = None,
                                    article_filter: Optional[Callable[[str, str], bool]] = None,
                                    d_fetcher_kwargs: Optional[Dict[str, Any]] = None,
                                    e_fetcher_kwargs: Optional[Dict[str, Any]] = None,
                                    extractor_kwargs: Optional[Dict[str, Any]] = None,
                                    start_date: Optional[datetime.datetime] = None,
                                    end_date: Optional[datetime.datetime] = None,
                                    content_handler: Optional[Callable[[str, ExtractionResult], None]] = None,
                                    exception_handler: Optional[Callable[[str, Exception], None]] = None
                                    ) -> List[Tuple[str, ExtractionResult]]:
        """
        Streaming mode: discover channels -> for each channel discover articles -> extract immediately.

        Good for freshness and lower memory footprint on large crawls.
        """
        if d_fetcher_kwargs is None:
            d_fetcher_kwargs = {}
        if e_fetcher_kwargs is None:
            e_fetcher_kwargs = {}
        if extractor_kwargs is None:
            extractor_kwargs = {}

        self.channels.clear()
        self.articles.clear()
        self.results.clear()

        self.sink.on_run_start(run_id=self._run_id(), name=self.name, at=datetime.datetime.now())
        self.log(f"=== STREAMING: discover -> extract ===")

        # 1) Discover channels
        channels = self.discover_channels(entry_points, start_date, end_date, d_fetcher_kwargs)

        # 2) For each channel, discover & extract on the fly
        for ch in channels:
            if channel_filter and not channel_filter(ch.url):
                self.sink.on_fetch_skip(ch.url, "channel-filtered")
                continue

            try:
                article_urls: Iterable[str] = self.discoverer.get_articles_for_channel(ch.url, d_fetcher_kwargs)
                temp_list = list(article_urls)
                self.policy.notify_round_start(ch.group_key, len(temp_list))

                for art_url in temp_list:
                    art = Article(
                        url=art_url,
                        channel_url=ch.url,
                        group_key=ch.group_key,
                        discovered_at=datetime.datetime.now()
                    )
                    self.articles.append(art)
                    self.sink.on_article_discovered(art)

                    # Inline extraction
                    if article_filter and not article_filter(art.url, art.group_key):
                        self.sink.on_fetch_skip(art.url, "filtered")
                        continue
                    if not self.policy.should_crawl(art.url):
                        self.sink.on_fetch_skip(art.url, "should-not-crawl")
                        continue
                    if not self.policy.allow_now(art.group_key):
                        self.sink.on_fetch_skip(art.url, "rate-limited")
                        continue

                    self.sink.on_fetch_start(art.url, art.group_key)
                    try:
                        content = self.e_fetcher.get_content(art.url, **e_fetcher_kwargs)
                        if not content:
                            self.sink.on_fetch_skip(art.url, "empty-content")
                            continue

                        self.sink.on_fetch_success(art.url, len(content))
                        result = self.extractor.extract(content, art.url, **extractor_kwargs)
                        self.sink.on_extract_success(art.url, result)
                        self.results.append((art.url, result))
                        if content_handler:
                            content_handler(art.url, result)

                    except Exception as e:
                        if exception_handler:
                            exception_handler(art.url, e)
                        transient, code = self.error_policy.classify(e)
                        self.log(f"[Error] Extraction failed for {art.url}: {e}")
                        self.sink.on_error(art.url, e, transient, {"code": code})
                        if not transient:
                            self.sink.on_dead_letter(art.url, e, {"code": code})

            except Exception as e:
                self.log(f"[Error] Failed to process channel {ch.url}: {e}\n{traceback.format_exc()}")
            finally:
                self.policy.notify_round_finish(ch.group_key)

        self.log(f"[STREAMING] Extracted {len(self.results)} article(s).")
        self.sink.on_run_end(
            run_id=self._run_id(),
            name=self.name,
            at=datetime.datetime.now(),
            stats={
                "channels": len(self.channels),
                "articles": len(self.articles),
                "extracted": len(self.results)
            }
        )
        return self.results

    # ---------------- Helpers ----------------

    def _run_id(self) -> str:
        """A simple run_id. Replace with UUID if you need multiple runs per process."""
        today = datetime.datetime.now().strftime("%Y%m%d")
        return f"{self.name}-{today}"

    def _resolve_group_key(self, channel_url: str) -> str:
        """
        Resolve group_key from external resolver if provided; otherwise fall back to a stable netloc[/first-seg].
        This fallback is only for convenience; callers are encouraged to pass a resolver to avoid ambiguities.
        """
        if self.channel_group_resolver:
            try:
                g = self.channel_group_resolver(channel_url)
                if g:
                    return g
            except Exception:
                # Fallback when resolver fails
                pass

        parsed = urlparse(channel_url)
        netloc = parsed.netloc or "unknown"
        path = (parsed.path or "/").strip("/")
        first_seg = path.split("/", 1)[0] if path else ""
        return f"{netloc}/{first_seg}" if first_seg else netloc


# ============================== Compatibility Layer ==============================

def build_pipeline(
        name: str,
        config: dict,
        log_callback: Callable[..., None],
        crawler_governor=None  # kept for backward compatibility; NOT used inside pipeline
) -> CrawlPipeline:
    """
    Build pipeline with factory methods. Compatible with previous config structure.

    Extra (optional) config keys:
        - 'sink': PipelineSink implementation
        - 'policy': CrawlPolicy implementation
        - 'error_policy': ErrorPolicy implementation
        - 'channel_group_resolver': Callable[[str], str]
        - 'channel_group_map': Dict[str, str]  (will be wrapped as a resolver)
    """
    d_fetcher_name = config.get('d_fetcher_name', 'N/A')
    d_fetcher_init_param = config.get('d_fetcher_init_param', {})
    d_fetcher = fetcher_factory(d_fetcher_name, d_fetcher_init_param)

    e_fetcher_name = config.get('e_fetcher_name', 'N/A')
    e_fetcher_init_param = config.get('e_fetcher_init_param', {})
    e_fetcher = fetcher_factory(e_fetcher_name, e_fetcher_init_param)

    discoverer_name = config.get('discoverer_name', 'N/A')
    discoverer_init_param = config.get('discoverer_init_param', {})
    discoverer = discoverer_factory(discoverer_name, {'fetcher': d_fetcher, **discoverer_init_param})

    extractor_name = config.get('extractor_name', 'N/A')
    extractor_init_param = config.get('extractor_init_param', {})
    extractor = extractor_factory(extractor_name, extractor_init_param)

    sink: Optional[PipelineSink] = config.get('sink', None)
    policy: Optional[CrawlPolicy] = config.get('policy', None)
    error_policy: Optional[ErrorPolicy] = config.get('error_policy', None)

    channel_group_resolver: Optional[Callable[[str], str]] = config.get('channel_group_resolver', None)
    channel_group_map: Optional[Dict[str, str]] = config.get('channel_group_map', None)
    if channel_group_resolver is None and channel_group_map:
        # Wrap map into a resolver; fallback to default if not found in map.
        def _resolver_from_map(u: str) -> str:
            return channel_group_map.get(u, "")
        channel_group_resolver = _resolver_from_map

    pipeline = CrawlPipeline(
        name=name,
        d_fetcher=d_fetcher,
        discoverer=discoverer,
        e_fetcher=e_fetcher,
        extractor=extractor,
        log_callback=log_callback,
        sink=sink,
        policy=policy,
        error_policy=error_policy,
        channel_group_resolver=channel_group_resolver
    )
    return pipeline


def drive_pipeline(pipeline: CrawlPipeline, config: dict) -> None:
    """
    Drive the pipeline in either 'batch' (default) or 'streaming' mode.

    Backward-compatible hooks (optional):
        - 'channel_filter': Callable[[str], bool]
        - 'article_filter': Callable[[str, str], bool]
        - 'content_handler': Callable[[str, ExtractionResult], None]
        - 'exception_handler': Callable[[str, Exception], None]
    """
    entry_points: List[str] = config.get('entry_points', [])
    start_date, end_date = config.get('period_filter', (None, None))
    d_fetcher_kwargs = config.get('d_fetcher_kwargs', {})
    e_fetcher_kwargs = config.get('e_fetcher_kwargs', {})
    extractor_kwargs = config.get('extractor_kwargs', {})

    channel_filter = config.get('channel_filter', None)        # Callable[[str], bool]
    article_filter = config.get('article_filter', None)        # Callable[[str, str], bool]
    content_handler = config.get('content_handler', None)      # Callable[[str, ExtractionResult], None]
    exception_handler = config.get('exception_handler', None)  # Callable[[str, Exception], None]

    mode = (config.get('mode') or 'batch').lower()

    try:
        if mode == 'streaming':
            pipeline.stream_discover_and_extract(
                entry_points=entry_points,
                channel_filter=channel_filter,
                article_filter=article_filter,
                d_fetcher_kwargs=d_fetcher_kwargs,
                e_fetcher_kwargs=e_fetcher_kwargs,
                extractor_kwargs=extractor_kwargs,
                start_date=start_date,
                end_date=end_date,
                content_handler=content_handler,
                exception_handler=exception_handler
            )
        else:
            # Batch (3-stage)
            pipeline.discover_channels(
                entry_points=entry_points,
                start_date=start_date,
                end_date=end_date,
                fetcher_kwargs=d_fetcher_kwargs
            )
            pipeline.discover_articles(
                channel_filter=channel_filter,
                fetcher_kwargs=d_fetcher_kwargs
            )
            pipeline.extract_articles(
                article_filter=article_filter,
                content_handler=content_handler,
                exception_handler=exception_handler,
                fetcher_kwargs=e_fetcher_kwargs,
                extractor_kwargs=extractor_kwargs
            )
    finally:
        pipeline.shutdown()


def run_pipeline(
        config: dict,
        log_callback: Callable[..., None] = print,
        name: str = 'ic',
        crawler_governor=None  # kept for backward compatibility; NOT used inside pipeline
) -> None:
    pipeline = build_pipeline(name, config, log_callback, crawler_governor)
    drive_pipeline(pipeline, config)
