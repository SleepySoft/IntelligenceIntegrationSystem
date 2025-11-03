import re
import json
import requests
import xml.etree.ElementTree as ET
from collections import deque
from usp.tree import sitemap_from_str
from urllib.parse import urlparse, urljoin
from typing import Set, List, Dict, Any, Optional, Deque

# Required: pip install ultimate-sitemap-parser requests

class SitemapDiscoverer:
    """
    A general-purpose Sitemap Discoverer, refactored for two-stage operation.

    This class is designed to run in two distinct stages, allowing for
    user selection of "channels" (leaf sitemaps) before processing.

    --------------------------------------------------------------------------
    HOW TO USE (Two-Stage Process):
    --------------------------------------------------------------------------

    1.  Instantiate the class:
        discoverer = SitemapDiscoverer(verbose=True)

    2.  STAGE 1: Discover "Channels" (Leaf Sitemaps)
        This recursively explores sitemap indexes (e.g., sitemap_index.xml)
        to find all the final, "leaf" sitemap files that contain the
        actual page URLs (e.g., sitemap-posts-2023.xml).

        all_channels = discoverer.discover_channels("https://www.example.com")

        # This returns a list of URLs, e.g.:
        # [
        #   "https://www.example.com/sitemap-posts.xml",
        #   "https://www.example.com/sitemap-pages.xml",
        #   "https://www.example.com/sitemap-news.xml"
        # ]

    3.  STAGE 2: Process Only Selected Channels
        The user can now review the list from Stage 1 and select only
        the channels they are interested in.

        # Example: User is only interested in "news" and "posts"
        selected_channels = [
           "https://www.example.com/sitemap-posts.xml",
           "https://www.example.com/sitemap-news.xml"
        ]

        # The class then fetches and parses *only* these selected files.
        results = discoverer.process_selected_channels(selected_channels)

        # `results` will be a dictionary containing *only* the URLs
        # from the selected channels.
        # {
        #   'status': 'success',
        #   'total_urls_found': 1234,
        #   'selected_channels_processed': [...],
        #   'article_urls': [...]
        # }

    --------------------------------------------------------------------------

    This class automatically attempts to find sitemaps from robots.txt,
    falling back to default paths. It uses a queue for recursive processing
    of sitemap indexes. It prioritizes the 'ultimate-sitemap-parser' library
    and automatically falls back to manual 'xml.etree.ElementTree' parsing
    for maximum compatibility and robustness.
    """

    # --- 1. Configuration ---

    # Must simulate a browser, or many sites will return 403 Forbidden
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    # XML namespace for standard sitemaps
    NAMESPACES = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

    def __init__(self, verbose: bool = True):
        """
        Initializes the discoverer.
        :param verbose: Whether to print detailed log messages.
        """
        self.verbose = verbose

        # --- State Properties ---

        # (For Stage 2) Stores final article URLs from selected channels
        self.all_article_urls: Set[str] = set()

        # (For Stage 1) Stores discovered "channels" (leaf sitemap URLs)
        self.leaf_sitemaps: Set[str] = set()

        # Internal queue for processing sitemap indexes
        self.to_process_queue: Deque[str] = deque()

        # Set to avoid re-processing the same sitemap URL
        self.processed_sitemaps: Set[str] = set()

        self.log_messages: List[str] = []  # For GUI logging

    # --- 2. Private Helper Methods ---

    def _log(self, message: str, indent: int = 0):
        """Unified logging function."""
        log_msg = f"{' ' * (indent * 4)}{message}"
        self.log_messages.append(log_msg)
        if self.verbose:
            print(log_msg)

    def _get_content(self, url: str) -> Optional[bytes]:
        """
        General-purpose content fetching function.
        - Returns bytes (for XML parsing)
        - Returns None (on failure)
        """
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=10)
            response.raise_for_status()  # Ensure request was successful (e.g., 200 OK)
            return response.content
        except requests.exceptions.RequestException as e:
            self._log(f"[Request Error] Failed to fetch {url}: {e}", 1)
            return None

    def _discover_sitemap_entry_points(self, homepage_url: str) -> List[str]:
        """
        Step 1 (Internal): Automatically discover sitemap entry points.
        This mimics the behavior of search engine crawlers.
        """
        self._log(f"Auto-discovering sitemap entry points for {homepage_url}...")

        try:
            parsed_home = urlparse(homepage_url)
            base_url = f"{parsed_home.scheme}://{parsed_home.netloc}"
        except Exception as e:
            self._log(f"[Error] Could not parse homepage URL: {e}")
            return []

        # Path 1: Check robots.txt (Preferred)
        robots_url = urljoin(base_url, '/robots.txt')
        self._log(f"Checking robots.txt: {robots_url}", 1)
        robots_content = self._get_content(robots_url)

        sitemap_urls = []
        if robots_content:
            try:
                # Find all "Sitemap: ..." directives
                sitemap_urls = re.findall(
                    r"^Sitemap:\s*(.+)$",
                    robots_content.decode('utf-8', errors='ignore'),
                    re.IGNORECASE | re.MULTILINE
                )
                sitemap_urls = [url.strip() for url in sitemap_urls]

                if sitemap_urls:
                    self._log(f"Found {len(sitemap_urls)} sitemap(s) in robots.txt: {sitemap_urls}", 1)
                    return sitemap_urls

            except Exception as e:
                self._log(f"Error parsing robots.txt: {e}", 1)

        # Path 2: Guess default paths (Fallback)
        self._log("No sitemaps found in robots.txt. Guessing default paths...", 1)
        return [
            urljoin(base_url, '/sitemap_index.xml'),
            urljoin(base_url, '/sitemap.xml')
        ]

    def _parse_sitemap_xml(self, xml_content: bytes, sitemap_url: str) -> Dict[str, List[str]]:
        """
        Parses Sitemap XML content.
        This is a core step, implementing the "USP library first, manual fallback" logic.

        :param xml_content: The fetched XML binary content
        :param sitemap_url: The URL of the current sitemap (for logging)
        :return: A dictionary containing 'pages' (list of page URLs) and 'sub_sitemaps' (list of sub-sitemap URLs)
        """
        pages: List[str] = []
        sub_sitemaps: List[str] = []

        try:
            # -----------------
            # Scheme A: Try 'ultimate-sitemap-parser' (robust, automatic)
            # -----------------
            self._log("    Trying to parse with [ultimate-sitemap-parser]...", 1)
            # Use 'ignore' for encoding errors, as some sitemaps are malformed
            parsed_sitemap = sitemap_from_str(xml_content.decode('utf-8', errors='ignore'))

            # 1. Extract pages (<urlset>)
            for page in parsed_sitemap.all_pages():
                pages.append(page.url)

            # 2. Extract sub-sitemaps (<sitemapindex>)
            for sub_sitemap in parsed_sitemap.all_sub_sitemaps():
                sub_sitemaps.append(sub_sitemap.url)

            self._log(f"    [USP Success] Found {len(pages)} pages and {len(sub_sitemaps)} sub-sitemaps.", 1)

        except Exception as e:
            # -----------------
            # Scheme B: USP library failed (e.g., people.com.cn index), execute manual fallback
            # -----------------
            self._log(f"    [USP Failed] Library parsing error: {e}", 1)
            self._log("    --> Initiating [Manual ElementTree] fallback...", 1)

            try:
                root = ET.fromstring(xml_content)

                # 1. Manually parse index files (<sitemap>)
                index_nodes = root.findall('ns:sitemap', self.NAMESPACES)
                if index_nodes:
                    for node in index_nodes:
                        loc = node.find('ns:loc', self.NAMESPACES)
                        if loc is not None and loc.text:
                            sub_sitemaps.append(loc.text)  # Add sub-sitemap
                    self._log(f"    [Manual Fallback] Found {len(sub_sitemaps)} sub-sitemaps.", 1)

                # 2. Manually parse page files (<url>)
                url_nodes = root.findall('ns:url', self.NAMESPACES)
                if url_nodes:
                    for node in url_nodes:
                        loc = node.find('ns:loc', self.NAMESPACES)
                        if loc is not None and loc.text:
                            pages.append(loc.text)  # Add page URL
                    self._log(f"    [Manual Fallback] Found {len(pages)} pages.", 1)

                if not index_nodes and not url_nodes:
                    self._log("    [Manual Fallback] Failed: No <sitemap> or <url> tags found in XML.", 1)

            except ET.ParseError as xml_e:
                self._log(f"    [Manual Fallback] Failed: Could not parse XML. Error: {xml_e}", 1)

        return {'pages': pages, 'sub_sitemaps': sub_sitemaps}

    # --- 3. Public-Facing Methods (Two-Stage API) ---

    def discover_channels(self, homepage_url: str) -> List[str]:
        """
        STAGE 1: Discover all "channels" (leaf sitemaps containing articles).

        This recursively explores all sitemap indexes to find sitemap files
        that directly contain <url> tags (pages).
        It returns a list of these sitemap file URLs, which you can then
        present to a user for selection.

        :param homepage_url: The homepage URL of the site to analyze.
        :return: A list of "leaf" sitemap URLs (the "channels").
        """
        self._log(f"--- STAGE 1: Discovering Channels for {homepage_url} ---")

        # --- 0. Reset state ---
        self.leaf_sitemaps.clear()
        self.to_process_queue.clear()
        self.processed_sitemaps.clear()

        # --- 1. Find entry points ---
        initial_sitemaps = self._discover_sitemap_entry_points(homepage_url)
        if not initial_sitemaps:
            self._log("Could not find any sitemap entry points.")
            return []

        self.to_process_queue.extend(initial_sitemaps)

        # --- 2. Loop through sitemap queue (finding channels) ---
        while self.to_process_queue:
            sitemap_url = self.to_process_queue.popleft()

            if sitemap_url in self.processed_sitemaps:
                continue
            self.processed_sitemaps.add(sitemap_url)

            self._log(f"\n--- Analyzing index: {sitemap_url} ---")

            xml_content = self._get_content(sitemap_url)
            if not xml_content:
                self._log("  Failed to fetch, skipping.", 1)
                continue

            # --- 3. Parse ---
            parse_result = self._parse_sitemap_xml(xml_content, sitemap_url)

            # If it has sub-sitemaps (it's an index file), add them back to the queue
            if parse_result['sub_sitemaps']:
                self._log(f"  > Found {len(parse_result['sub_sitemaps'])} sub-indexes. Adding to queue.", 2)
                self.to_process_queue.extend(parse_result['sub_sitemaps'])

            # If it has pages (it's a leaf file/channel), we store THIS sitemap_url
            # We do *not* save the pages themselves yet. That's for Stage 2.
            if parse_result['pages']:
                self._log(f"  > Found {len(parse_result['pages'])} pages. Marking {sitemap_url} as a 'Channel'.", 2)
                self.leaf_sitemaps.add(sitemap_url)

        self._log(f"\n==========================================")
        self._log(f"Stage 1 Complete: Discovered {len(self.leaf_sitemaps)} total channels.")
        return list(self.leaf_sitemaps)

    def process_selected_channels(self, channel_urls: List[str]) -> Dict[str, Any]:
        """
        STAGE 2: Process only the selected channels.

        This fetches and parses only the sitemap URLs (channels) that
        were selected by the user from the Stage 1 discovery.
        It returns only the article URLs found within those selected channels.

        :param channel_urls: A list of sitemap URLs to process (from discover_channels()).
        :return: A dictionary containing the final processing results.
        """
        self._log(f"\n--- STAGE 2: Processing {len(channel_urls)} Selected Channel(s) ---")

        # --- 0. Reset state ---
        self.all_article_urls.clear()

        # Use a queue in case a selected "channel" is somehow another index
        process_queue: Deque[str] = deque(channel_urls)
        processed_set: Set[str] = set()  # Tracks processing for this function call

        while process_queue:
            sitemap_url = process_queue.popleft()

            if sitemap_url in processed_set:
                continue
            processed_set.add(sitemap_url)

            self._log(f"Processing selected channel: {sitemap_url}", 1)
            xml_content = self._get_content(sitemap_url)
            if not xml_content:
                self._log("  > Fetch failed, skipping.", 2)
                continue

            parse_result = self._parse_sitemap_xml(xml_content, sitemap_url)

            # Add the discovered page URLs to the final set
            if parse_result['pages']:
                self._log(f"  > Found and added {len(parse_result['pages'])} page URLs.", 2)
                self.all_article_urls.update(parse_result['pages'])

            # [Robustness] If a selected "channel" was *also* an index,
            # we automatically process its children.
            if parse_result['sub_sitemaps']:
                self._log(f"  > Warning: Selected channel {sitemap_url} is a sub-index.", 2)
                self._log(f"  > Automatically adding its children to the queue...", 2)
                process_queue.extend(parse_result['sub_sitemaps'])

        self._log(f"\n==========================================")
        self._log(f"Stage 2 Complete: Found {len(self.all_article_urls)} unique URLs from selected channels.")

        return {
            'status': 'success' if self.all_article_urls else 'failure',
            'total_urls_found': len(self.all_article_urls),
            'selected_channels_processed': list(processed_set),
            'article_urls': list(self.all_article_urls)
        }

    def get_articles_for_channel(self, channel_url: str) -> List[str]:
        """
        Helper for Stage 2 (Lazy Loading): Gets pages for ONE specific channel.
        """
        self._log(f"--- STAGE 2: Fetching articles for {channel_url} ---")
        xml_content = self._get_content(channel_url)
        if not xml_content:
            return []

        parse_result = self._parse_sitemap_xml(xml_content, channel_url)
        self._log(f"  > Found {len(parse_result['pages'])} articles.")
        return parse_result['pages']

    def get_xml_content_str(self, url: str) -> str:
        """Helper to get raw XML as a string for display."""
        self.log_messages.clear()
        self._log(f"Fetching XML content for: {url}")
        content = self._get_content(url)
        if content:
            try:
                # Try to decode as UTF-8, fallback to ignoring errors
                return content.decode('utf-8', errors='ignore')
            except Exception as e:
                self._log(f"Error decoding XML: {e}")
                return f"Error decoding XML: {e}"
        return f"Failed to fetch content from {url}"


# ----------------------------------------------------------------------------------------------------------------------

if __name__ == "__main__":

    # Instantiate our discoverer
    # verbose=True will print detailed logs, good for debugging
    # verbose=False will run silently
    discoverer = SitemapDiscoverer(verbose=True)

    # Add all websites you want to try here
    websites_to_analyze = [
        "http://www.people.com.cn",  # (We know this one requires the fallback parser)
        "https://www.cnblogs.com",  # (Standard sitemap.xml)
        "https://www.theverge.com",  # (Standard sitemap_index.xml)
        "https://www.xinhuanet.com",  # (Xinhua)
        "http://www.gov.cn",  # (Chinese Gov)
        # "https://www.bbc.com"      # (You can add more...)
    ]

    # Store the final results from all websites
    all_final_results = []

    for site in websites_to_analyze:
        print(f"\n\n{'=' * 25} Analyzing: {site} {'=' * 25}")

        # --- STAGE 1: Discover all available channels ---
        print(f"\n--- STAGE 1: Discovering channels for {site} ---")
        try:
            all_channels = discoverer.discover_channels(site)
        except Exception as e:
            print(f"An error occurred during channel discovery for {site}: {e}")
            continue

        if not all_channels:
            print(f"No channels (leaf sitemaps) found for {site}. Skipping.")
            continue

        print(f"\nDiscovered {len(all_channels)} channels:")
        for i, channel_url in enumerate(all_channels[:5]):  # Print first 5
            print(f"  [{i}] {channel_url}")
        if len(all_channels) > 5:
            print(f"  ... and {len(all_channels) - 5} more.")

        # --- STAGE 2: Select channels and process them ---
        print(f"\n--- STAGE 2: Selecting and processing channels for {site} ---")

        # --- This is where you implement your selection logic ---
        #
        # Example: Automatically select channels based on name.
        # Let's try to find "news" channels, or "cpc" (for people.com.cn)
        #
        selected_channels = [
            ch for ch in all_channels
            if "news" in ch or "cpc" in ch or "finance" in ch
        ]

        # If our filter didn't find anything, just process the first channel
        # to get *some* data.
        if not selected_channels and all_channels:
            print("No specific channels found, processing the first channel as a sample.")
            selected_channels = [all_channels[0]]
        else:
            print(
                f"Filtered selection: Found {len(selected_channels)} channels matching criteria (e.g., 'news', 'cpc', 'finance').")
            for ch in selected_channels[:3]:  # Print selection sample
                print(f"  -> Selected: {ch}")
            if len(selected_channels) > 3:
                print(f"  ... and {len(selected_channels) - 3} more.")
        # --- End of selection logic ---

        # Now, process *only* the channels we selected
        try:
            result = discoverer.process_selected_channels(selected_channels)
            all_final_results.append({'site': site, 'results': result})

            # Print a brief summary of the results
            print(f"\n--- Analysis Summary for {site} (Selected Channels) ---")
            print(f"Status: {result['status']}")
            print(f"Processed {len(result['selected_channels_processed'])} selected channel(s).")
            print(f"Total URLs found in selection: {result['total_urls_found']}")

            # Show a sample of 5 URLs
            if result['article_urls']:
                print("--- Sample of 5 URLs ---")
                sample = result['article_urls'][:5]
                for url in sample:
                    print(url)
            print(f"{'=' * 60}\n\n")

        except Exception as e:
            print(f"An error occurred during channel processing for {site}: {e}")

    # At the end, `all_final_results` contains the detailed results
    # from the *selected* channels for all processed websites.
    try:
        with open('sitemap_analysis_results.json', 'w', encoding='utf-8') as f:
            json.dump(all_final_results, f, indent=4, ensure_ascii=False)
        print("All results saved to sitemap_analysis_results.json")
    except Exception as e:
        print(f"Error saving results to JSON: {e}")

