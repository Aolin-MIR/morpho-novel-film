from __future__ import annotations

import html
import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

try:
    from ddgs import DDGS

    DDGS_AVAILABLE = True
except Exception:
    try:
        from duckduckgo_search import DDGS

        DDGS_AVAILABLE = True
    except Exception:
        DDGS = None  # type: ignore[assignment]
        DDGS_AVAILABLE = False


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SEARCH_TIMEOUT = httpx.Timeout(18.0, connect=8.0, read=18.0)
FETCH_TIMEOUT = httpx.Timeout(14.0, connect=6.0, read=14.0)
_CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 1800
COMPANY_SEARCH_CACHE_TTL_SECONDS = 21600
LIVE_WEB_QUERY_BUDGET = 3
LIVE_WEB_FETCH_BUDGET = 3
BLOCKED_DOMAINS = {
    "zhidao.baidu.com",
    "zhihu.com",
    "duckduckgo.com",
    "reddit.com",
    "facebook.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "weibo.com",
    "tiktok.com",
    "douyin.com",
    "quora.com",
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", _normalize_text(value))
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _parse_json_loose(text: str) -> Any | None:
    raw = _normalize_text(text)
    if not raw:
        return None
    raw = re.sub(r"^```json\s*", "", raw, flags=re.I)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return None
    return None


def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expires_at, value = item
    if expires_at < time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int = CACHE_TTL_SECONDS) -> None:
    _CACHE[key] = (time.time() + ttl, value)


def _normalize_query_key(value: Any) -> str:
    text = re.sub(r"\s+", " ", _normalize_text(value)).strip().lower()
    return text


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = _normalize_text(os.getenv(name))
    if not raw:
        return default
    try:
        return max(low, min(high, int(raw)))
    except Exception:
        return default


def _trim_live_queries(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    max_queries = _int_env("LIVE_WEB_QUERY_BUDGET", LIVE_WEB_QUERY_BUDGET, 1, 8)
    deduped = []
    seen = set()
    for item in queries or []:
        if not isinstance(item, dict):
            continue
        live_query = _normalize_query_key(item.get("live_query") or item.get("query"))
        if not live_query or live_query in seen:
            continue
        seen.add(live_query)
        deduped.append({**item, "live_query": live_query, "query": _normalize_text(item.get("query") or item.get("live_query"))})
        if len(deduped) >= max_queries:
            break
    return deduped


def _http_client(timeout: httpx.Timeout) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        },
        follow_redirects=True,
    )


def _qwen_api_key() -> str:
    return (
        _normalize_text(os.getenv("DASHSCOPE_API_KEY"))
        or _normalize_text(os.getenv("QWEN_API_KEY"))
        or _normalize_text(os.getenv("TONGYI_API_KEY"))
    )


def _qwen_workspace_id() -> str:
    return (
        _normalize_text(os.getenv("DASHSCOPE_WORKSPACE_ID"))
        or _normalize_text(os.getenv("QWEN_WORKSPACE_ID"))
        or _normalize_text(os.getenv("BAILIAN_WORKSPACE_ID"))
    )


def _qwen_responses_base_url() -> str:
    return (
        _normalize_text(os.getenv("QWEN_RESPONSES_BASE_URL"))
        or _normalize_text(os.getenv("DASHSCOPE_RESPONSES_BASE_URL"))
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )


def _qwen_web_search_model() -> str:
    return (
        _normalize_text(os.getenv("QWEN_WEB_SEARCH_MODEL"))
        or _normalize_text(os.getenv("LLM_MODEL"))
        or "qwen3.5-plus"
    )


def _qwen_tools_available() -> bool:
    # DashScope responses web tools require workspaceid for this integration path.
    return bool(_qwen_api_key() and _qwen_workspace_id())


def _qwen_search_timeout() -> httpx.Timeout:
    raw = _normalize_text(os.getenv("QWEN_WEB_SEARCH_TIMEOUT_SECONDS")) or "90"
    try:
        seconds = max(30.0, min(300.0, float(raw)))
    except Exception:
        seconds = 90.0
    return httpx.Timeout(seconds, connect=min(20.0, seconds), read=seconds)


def _extract_response_output_text(payload: dict[str, Any]) -> str:
    text = _normalize_text(payload.get("output_text"))
    if text:
        return text
    output = payload.get("output")
    if not isinstance(output, list) and isinstance(payload.get("response"), dict):
        output = payload.get("response", {}).get("output")
    if not isinstance(output, list):
        return ""
    chunks = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                chunks.append(part.get("text"))
            elif isinstance(part.get("text"), dict):
                chunks.append(_normalize_text(part.get("text", {}).get("value")))
            elif isinstance(part.get("content"), str):
                chunks.append(part.get("content"))
    return "\n".join(chunk for chunk in chunks if _normalize_text(chunk)).strip()


def _strip_tags(value: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg|iframe).*?>.*?</\\1>", " ", value or "")
    text = re.sub(r"(?is)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|h1|h2|h3|article|section|main)>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    host = host.lower().strip()
    return host[4:] if host.startswith("www.") else host


def _is_blocked_domain(url: str) -> bool:
    domain = _domain_of(url)
    if not domain:
        return True
    if domain in BLOCKED_DOMAINS:
        return True
    if domain.startswith("bbs.") or "forum" in domain:
        return True
    return False


def _extract_meta(html_text: str, attr_name: str, attr_value: str) -> str:
    patterns = [
        rf'<meta[^>]+{attr_name}="{re.escape(attr_value)}"[^>]+content="([^"]+)"',
        rf"<meta[^>]+{attr_name}='{re.escape(attr_value)}'[^>]+content='([^']+)'",
        rf'<meta[^>]+content="([^"]+)"[^>]+{attr_name}="{re.escape(attr_value)}"',
        rf"<meta[^>]+content='([^']+)'[^>]+{attr_name}='{re.escape(attr_value)}'",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def _extract_title(html_text: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
    if not match:
        return ""
    return _strip_tags(match.group(1))


def _extract_published_at(html_text: str) -> str:
    for attr_name, attr_value in [
        ("property", "article:published_time"),
        ("name", "pubdate"),
        ("name", "publishdate"),
        ("itemprop", "datePublished"),
    ]:
        value = _extract_meta(html_text, attr_name, attr_value)
        if value:
            return value
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html_text)
    if match:
        return match.group(1).strip()
    return ""


def _extract_main_text(html_text: str) -> str:
    candidates = []
    for pattern in [
        r"(?is)<article[^>]*>(.*?)</article>",
        r"(?is)<main[^>]*>(.*?)</main>",
        r"(?is)<body[^>]*>(.*?)</body>",
    ]:
        match = re.search(pattern, html_text)
        if match:
            candidates.append(match.group(1))
    if not candidates:
        candidates = [html_text]
    segments = []
    seen = set()
    for block in candidates:
        for pattern in [
            r"(?is)<p[^>]*>(.*?)</p>",
            r"(?is)<li[^>]*>(.*?)</li>",
            r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>",
        ]:
            for match in re.finditer(pattern, block):
                text = _strip_tags(match.group(1))
                if len(text) < 35:
                    continue
                marker = text[:180]
                if marker in seen:
                    continue
                seen.add(marker)
                segments.append(text)
        if len(segments) >= 8:
            break
    return _compact_text(" ".join(segments[:8]), 900)


def _fetch_page_extract(url: str) -> dict[str, Any]:
    if not url:
        return {}
    cache_key = f"page:{url}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {"title": "", "content": "", "published_at": "", "url": url, "domain": _domain_of(url)}
    try:
        with _http_client(FETCH_TIMEOUT) as client:
            response = client.get(url)
            response.raise_for_status()
            html_text = response.text
        title = _extract_title(html_text)
        description = (
            _extract_meta(html_text, "name", "description")
            or _extract_meta(html_text, "property", "og:description")
        )
        content = _extract_main_text(html_text)
        published_at = _extract_published_at(html_text)
        result.update(
            {
                "title": title,
                "content": content or description,
                "published_at": published_at,
            }
        )
    except Exception:
        pass
    _cache_set(cache_key, result)
    return result


def _decode_duckduckgo_url(url: str) -> str:
    text = html.unescape(url or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0]).strip()
    return text


def _parse_duckduckgo_results(html_text: str, max_results: int) -> list[dict[str, Any]]:
    results = []
    for match in re.finditer(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", html_text, re.I | re.S):
        url = _decode_duckduckgo_url(match.group(1))
        title = _strip_tags(match.group(2))
        if not url or not title or _is_blocked_domain(url):
            continue
        window = html_text[match.end() : match.end() + 1200]
        snippet_match = re.search(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", window, re.I | re.S)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": url, "snippet": _compact_text(snippet, 260)})
        if len(results) >= max_results:
            break
    return results


def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, Any]]:
    normalized_query = _normalize_query_key(query)
    cache_key = f"ddg:{normalized_query}:{max_results}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    if DDGS_AVAILABLE and DDGS is not None:
        try:
            results = []
            with DDGS() as client:
                for item in client.text(
                    keywords=query,
                    region="wt-wt",
                    safesearch="moderate",
                    backend="auto",
                    max_results=max_results,
                ):
                    if not isinstance(item, dict):
                        continue
                    url = _normalize_text(item.get("href") or item.get("url"))
                    if not url or _is_blocked_domain(url):
                        continue
                    results.append(
                        {
                            "title": _normalize_text(item.get("title")) or url,
                            "url": url,
                            "snippet": _compact_text(item.get("body") or item.get("snippet"), 260),
                        }
                    )
                    if len(results) >= max_results:
                        break
            if results:
                _cache_set(cache_key, results)
                return results
        except Exception:
            pass
    errors = []
    for _ in range(2):
        try:
            with _http_client(SEARCH_TIMEOUT) as client:
                response = client.get("https://lite.duckduckgo.com/lite/", params={"q": normalized_query})
                response.raise_for_status()
            results = _parse_duckduckgo_results(response.text, max_results)
            if results:
                _cache_set(cache_key, results)
                return results
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        _cache_set(cache_key, [])
    return []


def _search_bing_rss(query: str, max_results: int) -> list[dict[str, Any]]:
    normalized_query = _normalize_query_key(query)
    cache_key = f"bing:{normalized_query}:{max_results}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        with _http_client(SEARCH_TIMEOUT) as client:
            response = client.get("https://www.bing.com/search", params={"q": normalized_query, "format": "rss"})
            response.raise_for_status()
        root = ET.fromstring(response.text)
        results = []
        for item in root.findall("./channel/item"):
            title = _normalize_text(item.findtext("title"))
            url = _normalize_text(item.findtext("link"))
            snippet = _normalize_text(item.findtext("description"))
            if not title or not url or _is_blocked_domain(url):
                continue
            results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        _cache_set(cache_key, results)
        return results
    except Exception:
        _cache_set(cache_key, [])
        return []


def _run_qwen_responses_request(request_data: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {_qwen_api_key()}",
        "Content-Type": "application/json",
    }
    url = _qwen_responses_base_url().rstrip("/") + "/responses"
    workspace_id = _qwen_workspace_id()
    client = httpx.Client(
        timeout=_qwen_search_timeout(),
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        },
        follow_redirects=True,
    )
    try:
        response = client.post(
            url,
            headers=headers,
            json=request_data,
            params={"workspaceid": workspace_id} if workspace_id else None,
        )
        response.raise_for_status()
        return response.json()
    finally:
        client.close()


def _run_company_search_queries(
    queries: list[dict[str, Any]], max_results_per_query: int,
) -> list[dict[str, Any]]:
    """Use the same Arsenal comprehensive-search endpoint as Wiki paratext."""
    trimmed_queries = _trim_live_queries(queries)
    query_texts = [
        _normalize_text(item.get("live_query") or item.get("query"))
        for item in trimmed_queries
        if _normalize_text(item.get("live_query") or item.get("query"))
    ]
    cache_key = (
        "arsenal:"
        + hashlib.sha256(json.dumps(query_texts, ensure_ascii=False).encode("utf-8")).hexdigest()
        + f":{max_results_per_query}"
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    if not query_texts:
        return []

    url = os.getenv("ARSENAL_URL", "http://38.98.112.91:8888/comprehensive/search")
    app_id = os.getenv("ARSENAL_APP_ID", "readify-online")
    auth = os.getenv("ARSENAL_AUTH", "aime-deepsearch")
    channels = [item for item in os.getenv("ARSENAL_CHANNELS", "google").split(",") if item]
    payload = {
        "query_list": query_texts,
        "channels": channels,
        "app_id": app_id,
        "user_id": "morphor-generation",
        "qid": "morphor-" + hashlib.sha256("\n".join(query_texts).encode("utf-8")).hexdigest()[:12],
        "size": max(6, min(20, max_results_per_query * len(query_texts))),
        "need_content": True,
        "content_site": False,
        "resort": True,
        # Use the company-side cache in addition to the worker-local TTL cache.
        "cache": "true",
        "debug": "false",
    }
    with httpx.Client(
        timeout=SEARCH_TIMEOUT, trust_env=False, follow_redirects=False,
        headers={"X-Arsenal-Auth": auth, "Content-Type": "application/json"},
    ) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        body = response.json()
    if int(body.get("status_code", -1)) != 0:
        raise RuntimeError(f"Arsenal search failed: {body.get('status_msg') or body.get('status_code')}")

    content_by_uid = {
        str(item.get("uid") or ""): _normalize_text(item.get("content"))
        for item in body.get("content_data") or []
        if isinstance(item, dict)
    }
    grouped = [
        {
            "query_id": _normalize_text(item.get("id")),
            "query": _normalize_text(item.get("query")),
            "live_query": _normalize_text(item.get("live_query") or item.get("query")),
            "results": [],
        }
        for item in trimmed_queries
    ]
    seen_urls = set()
    for row in body.get("data") or []:
        if not isinstance(row, dict):
            continue
        url_value = _normalize_text(row.get("url"))
        title = _normalize_text(row.get("title"))
        if not url_value or not title or url_value in seen_urls or _is_blocked_domain(url_value):
            continue
        trace = row.get("trace_info") or {}
        traceability = row.get("traceability_type")
        if traceability is None:
            traceability = trace.get("traceability_type")
        if traceability == 1:
            continue
        uid = str(row.get("uid") or "")
        summary = _normalize_text(row.get("summary"))
        content = content_by_uid.get(uid, "")
        text_blob = f"{title} {summary} {content}".lower()
        best_index = 0
        best_overlap = -1
        for index, item in enumerate(grouped):
            terms = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", item["live_query"].lower()))
            overlap = sum(1 for term in terms if term in text_blob)
            if overlap > best_overlap:
                best_index, best_overlap = index, overlap
        target = grouped[best_index]
        if len(target["results"]) >= max_results_per_query:
            continue
        target["results"].append(
            {
                "title": title,
                "url": url_value,
                "snippet": _compact_text(summary or content, 220),
                "content": _compact_text(content or summary, 900),
                "published_at": _normalize_text(row.get("publish_time")),
                "rank": len(target["results"]) + 1,
                "source": _domain_of(url_value),
            }
        )
        seen_urls.add(url_value)
    ttl = _int_env(
        "COMPANY_SEARCH_CACHE_TTL_SECONDS", COMPANY_SEARCH_CACHE_TTL_SECONDS, 300, 86400,
    )
    _cache_set(cache_key, grouped, ttl=ttl)
    return grouped


def _run_qwen_web_search_queries(queries: list[dict[str, Any]], max_results_per_query: int) -> list[dict[str, Any]]:
    trimmed_queries = _trim_live_queries(queries)
    cache_key = f"qwen:{json.dumps(trimmed_queries, ensure_ascii=False, sort_keys=True)}:{max_results_per_query}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    brief_queries = []
    for item in trimmed_queries:
        brief_queries.append(
            {
                "query_id": _normalize_text(item.get("id")),
                "intent": _normalize_text(item.get("intent")),
                "query": _normalize_text(item.get("query")),
                "live_query": _normalize_text(item.get("live_query") or item.get("query")),
            }
        )

    prompt = (
        "You are a web research assistant for dramatic writing.\n"
        "Use web_search and web_extractor to collect high-signal sources for narrative generation.\n"
        "Prefer: plot summaries, episode recaps, synopses, structure analyses, character arc discussions, dialogue craft articles, and reputable industry reports.\n"
        "Avoid: forums, generic Q&A sites, piracy/streaming pages, cast-only pages, shopping pages, and social posts.\n"
        f"For each query, return at most {max_results_per_query} results.\n"
        "Return strict JSON only in this exact shape:\n"
        '{"results":[{"query_id":"...","query":"...","items":[{"title":"...","url":"...","snippet":"...","content":"...","published_at":""}]}]}\n'
        f"QUERIES={json.dumps(brief_queries, ensure_ascii=False)}"
    )

    request_variants = [
        {
            "model": _qwen_web_search_model(),
            "input": prompt,
            "tools": [{"type": "web_search"}, {"type": "web_extractor"}],
            "enable_thinking": True,
        },
        {
            "model": _qwen_web_search_model(),
            "input": prompt,
            "tools": [{"type": "web_search"}],
            "enable_thinking": True,
        },
    ]

    last_error: Exception | None = None
    payload = None
    for request_data in request_variants:
        try:
            payload = _run_qwen_responses_request(request_data)
            break
        except Exception as exc:
            last_error = exc
            continue
    if payload is None:
        raise last_error or RuntimeError("Qwen web search request failed")

    output_text = _extract_response_output_text(payload)
    parsed = _parse_json_loose(output_text)
    if not isinstance(parsed, dict):
        raise ValueError("Qwen web search did not return parseable JSON")

    grouped = []
    for group in parsed.get("results") or []:
        if not isinstance(group, dict):
            continue
        items = []
        for index, item in enumerate(group.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            url_value = _normalize_text(item.get("url"))
            if not url_value or _is_blocked_domain(url_value):
                continue
            items.append(
                {
                    "title": _normalize_text(item.get("title")) or url_value,
                    "url": url_value,
                    "snippet": _compact_text(item.get("snippet") or item.get("content"), 220),
                    "content": _compact_text(item.get("content") or item.get("snippet"), 900),
                    "published_at": _normalize_text(item.get("published_at")),
                    "rank": index,
                    "query_id": _normalize_text(group.get("query_id")),
                    "query": _normalize_text(group.get("query")),
                    "source": _domain_of(url_value),
                }
            )
            if len(items) >= max_results_per_query:
                break
        grouped.append(
            {
                "query_id": _normalize_text(group.get("query_id")),
                "query": _normalize_text(group.get("query")),
                "results": items,
            }
        )
    _cache_set(cache_key, grouped)
    return grouped


def run_live_web_search_queries(
    queries: list[dict[str, Any]],
    max_results_per_query: int = 3,
) -> list[dict[str, Any]]:
    trimmed_queries = _trim_live_queries(queries)
    fetch_budget = _int_env("LIVE_WEB_FETCH_BUDGET", LIVE_WEB_FETCH_BUDGET, 0, 8)
    fetch_count = 0
    grouped = []
    for query in trimmed_queries:
        query_text = _normalize_text(query.get("query"))
        live_query = _normalize_query_key(query.get("live_query")) or _normalize_query_key(query_text)
        query_id = _normalize_text(query.get("id"))
        if not live_query:
            continue
        results = _search_duckduckgo(live_query, max_results_per_query)
        if not results:
            results = _search_bing_rss(live_query, max_results_per_query)
        enriched = []
        for index, item in enumerate(results[:max_results_per_query], start=1):
            url = _normalize_text(item.get("url"))
            snippet = _normalize_text(item.get("snippet"))
            should_fetch = bool(url and len(snippet) < 80 and fetch_count < fetch_budget)
            fetched = _fetch_page_extract(url) if should_fetch else {}
            if should_fetch:
                fetch_count += 1
            title = _normalize_text(fetched.get("title") or item.get("title"))
            content = _normalize_text(fetched.get("content") or snippet)
            enriched.append(
                {
                    "title": title or item.get("title") or url,
                    "url": url,
                    "snippet": _compact_text(snippet or content, 220),
                    "content": content,
                    "published_at": _normalize_text(fetched.get("published_at")),
                    "rank": index,
                    "query_id": query_id,
                    "query": query_text,
                    "live_query": live_query,
                    "source": _domain_of(url),
                }
            )
        grouped.append({"query_id": query_id, "query": query_text, "live_query": live_query, "results": enriched})
    return grouped


def run_live_web_research(
    queries: list[dict[str, Any]],
    max_results_per_query: int = 3,
) -> dict[str, Any]:
    trimmed_queries = _trim_live_queries(queries)
    cache_key = f"live_web:{json.dumps(trimmed_queries, ensure_ascii=False, sort_keys=True)}:{max_results_per_query}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    if not trimmed_queries:
        return {"provider": "none", "results": []}
    try:
        result = {
            "provider": "company_arsenal",
            "results": _run_company_search_queries(
                trimmed_queries, max_results_per_query=max_results_per_query,
            ),
        }
    except Exception as exc:
        # Company retrieval is the only permitted live provider for Morphor.
        # Fail open to local knowledge instead of falling back to public search.
        result = {"provider": "company_arsenal_unavailable", "results": [], "error": str(exc)[:300]}
    if result["provider"] == "company_arsenal":
        _cache_set(cache_key, result, ttl=CACHE_TTL_SECONDS)
    return result


def live_web_search_enabled(payload: dict | None) -> bool:
    request_payload = payload or {}
    if "live_web_search" in request_payload:
        return bool(request_payload.get("live_web_search"))
    if "enable_live_web_search" in request_payload:
        return bool(request_payload.get("enable_live_web_search"))
    env_value = str(os.getenv("DEFAULT_LIVE_WEB_SEARCH", "true")).strip().lower()
    return env_value in {"1", "true", "yes", "on"}
