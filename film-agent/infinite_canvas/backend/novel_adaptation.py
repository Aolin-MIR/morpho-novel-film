"""Deterministic, provenance-first novel adaptation intake for Morpho.

The importer deliberately produces an editable *source packet*, not a second
script generator.  Its useful ideas are adapted from the author's NEST project:
safe EPUB ingestion, paragraph offsets, and every suggested story unit pointing
back to a short original-text span.  No model call is made at this stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import posixpath
import re
import zipfile
import hashlib
from itertools import chain
from xml.etree import ElementTree


MAX_SOURCE_CHARS = 5_000_000
SOURCE_CHUNK_CHARS = 8_000
MAX_PACKET_SCENES = 24
_CHAPTER_RE = re.compile(r"^[ \t]*(?:第\s*[一二三四五六七八九十百千万0-9]+\s*[章节回卷]|chapter\s+\d+|prologue|序章|楔子)[^\n]*", re.I | re.M)
_NAME_RE = re.compile(r"(?:^|[，。；：、\s])([\u4e00-\u9fff]{2,4})(?:说|道|问|答|看见|走进|来到|转身|决定|发现|听见)")
_LATIN_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,20})\s+(?:said|asked|walked|found|decided|saw)\b")
_FRONT_MATTER = ("目录", "contents", "copyright", "版权", "献词", "dedication", "前言", "preface")
_BACK_MATTER = ("作者的话", "后记", "acknowledg", "about the author", "附录", "appendix")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag.lower() in {"script", "style"}:
            self.skip += 1
        if tag.lower() in {"p", "div", "section", "article", "br", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.skip:
            self.skip -= 1
        if tag.lower() in {"p", "div", "section", "article", "br", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[\t \f\v]+", " ", text)).strip()


def _epub_text(raw: bytes) -> tuple[str, dict[str, str]]:
    """Read EPUB spine in reading order using only standard-library modules."""
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        names = archive.namelist()
        def read_item(name, limit=MAX_SOURCE_CHARS * 4):
            info = archive.getinfo(name)
            if info.file_size > limit:
                raise ValueError('EPUB 解压后的文本过大，请按卷导入；原文不会被截断')
            with archive.open(name) as handle:
                data = handle.read(limit + 1)
            if len(data) > limit:
                raise ValueError('EPUB 解压后的文本过大，请按卷导入')
            return data
        try:
            container = ElementTree.fromstring(read_item("META-INF/container.xml", 2_000_000))
            opf = next((node.attrib.get("full-path") for node in container.iter() if node.tag.endswith("rootfile")), None)
        except Exception:
            opf = next((name for name in names if name.lower().endswith(".opf")), None)
        if not opf:
            raise ValueError("EPUB 缺少 OPF 目录")
        root = ElementTree.fromstring(read_item(opf, 2_000_000))
        manifest = {node.attrib.get("id"): node.attrib.get("href") for node in root.iter() if node.tag.endswith("item")}
        spine = [node.attrib.get("idref") for node in root.iter() if node.tag.endswith("itemref")]
        metadata: dict[str, str] = {}
        for node in root.iter():
            key = node.tag.rsplit("}", 1)[-1].lower()
            if key in {"title", "creator", "language"} and node.text and key not in metadata:
                metadata[key] = node.text.strip()
        base = posixpath.dirname(opf)
        chapters: list[str] = []
        text_bytes = 0
        for item_id in spine:
            href = manifest.get(item_id)
            if not href:
                continue
            item_path = posixpath.normpath(posixpath.join(base, href))
            if item_path not in names:
                continue
            parser = _TextExtractor()
            item = read_item(item_path)
            text_bytes += len(item)
            if text_bytes > MAX_SOURCE_CHARS * 4:
                raise ValueError('EPUB 全文过大，请按卷导入；原文不会被截断')
            try:
                parser.feed(item.decode('utf-8-sig'))
            except UnicodeDecodeError as exc:
                raise ValueError('EPUB 文本编码无法完整读取，请转换为 UTF-8 EPUB 或 TXT') from exc
            chapter = _clean("".join(parser.parts))
            if chapter:
                chapters.append(chapter)
        if not chapters:
            raise ValueError("EPUB 未找到可读取的正文")
    return "\n\n".join(chapters), metadata


def _filter_matter(chapters: list[str]) -> str:
    # Only trim at document edges, never remove a possibly narrative middle chapter.
    start, end = 0, len(chapters)
    while start < end - 1 and any(marker in chapters[start][:500].lower() for marker in _FRONT_MATTER):
        start += 1
    while end - 1 > start and any(marker in chapters[end - 1][:500].lower() for marker in _BACK_MATTER):
        end -= 1
    return "\n\n".join(chapters[start:end])


def read_novel(raw: bytes, filename: str) -> tuple[str, dict[str, str]]:
    suffix = Path(filename or "novel.txt").suffix.lower()
    if suffix == ".epub":
        text, metadata = _epub_text(raw)
    elif suffix in {".txt", ".md", ".markdown"}:
        metadata = {}
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = raw.decode('gb18030')
                metadata['encoding'] = 'gb18030'
            except UnicodeDecodeError as exc:
                raise ValueError('文本编码无法完整读取，请转换为 UTF-8') from exc
    else:
        raise ValueError("只支持 TXT、Markdown 或 EPUB 小说文件")
    text = _clean(text)
    if not text:
        raise ValueError("小说内容为空")
    if len(text) > MAX_SOURCE_CHARS:
        raise ValueError(f"原著超过 {MAX_SOURCE_CHARS:,} 字符，请按卷导入；系统不会截断原文")
    return text, metadata


def _chapter_spans(text: str) -> list[tuple[str, int, int]]:
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        # Deterministic reading chunks give long unheaded novels a stable intake shape.
        return [("正文", 0, len(text))]
    spans: list[tuple[str, int, int]] = []
    if matches[0].start() > 0:
        spans.append(("开篇", 0, matches[0].start()))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        spans.append((_clean(match.group())[:80], match.start(), end))
    return spans


def source_chunks(text: str) -> list[dict[str, Any]]:
    """Cover every source character with bounded, chapter-aware reading chunks."""
    chunks = []
    for label, start, end in _chapter_spans(text):
        while start < end:
            stop = min(start + SOURCE_CHUNK_CHARS, end)
            if stop < end:
                paragraph = text.rfind("\n", start + SOURCE_CHUNK_CHARS // 2, stop)
                if paragraph > start:
                    stop = paragraph + 1
            value = text[start:stop]
            chunks.append({"id": f"source_{len(chunks) + 1:05d}", "label": label,
                           "start_char": start, "end_char": stop, "text": value,
                           "sha256": hashlib.sha256(value.encode()).hexdigest()})
            start = stop
    return chunks


def _excerpt(text: str, start: int, end: int, limit: int = 360) -> str:
    value = _clean(text[start:end])
    return value if len(value) <= limit else value[:limit].rsplit("。", 1)[0].strip() + "。"


def _evidence(text: str, index: int, label: str, start: int, end: int) -> dict[str, Any]:
    return {"id": f"evidence_{index:03d}", "label": label, "start_char": start, "end_char": end, "excerpt": _excerpt(text, start, end)}


def build_adaptation_packet(text: str, title: str = "") -> dict[str, Any]:
    """Return source-grounded, editable screenplay seeds without calling an LLM."""
    text = _clean(text)
    if not text or len(text) > MAX_SOURCE_CHARS:
        raise ValueError(f"请提供不超过 {MAX_SOURCE_CHARS:,} 字符的原著")
    spans = _chapter_spans(text)
    chunks = source_chunks(text)
    evidence = [_evidence(text, i + 1, label, start, end) for i, (label, start, end) in enumerate(spans[:MAX_PACKET_SCENES])]
    names: list[str] = []
    for match in chain(_NAME_RE.finditer(text), _LATIN_NAME_RE.finditer(text)):
        name = match.group(1)
        if name not in names and name not in {"我们", "他们", "自己", "这个", "那里"}:
            names.append(name)
        if len(names) >= 8:
            break
    characters = [
        {
            "id": f"novel_character_{i + 1}", "name": name,
            "character_type": "protagonist" if i == 0 else "helper",
            "background": "来自小说原文；请在工作台补足欲望、代价与人物弧光。",
            "source_evidence_ids": [item["id"] for item in evidence[:2]],
        }
        for i, name in enumerate(names)
    ]
    scenes, events, connections = [], [], []
    for index, item in enumerate(evidence):
        scene_id, event_id = f"novel_scene_{index + 1}", f"novel_event_{index + 1}"
        scenes.append({"id": scene_id, "title": item["label"], "description": item["excerpt"], "act": "act_one", "source_evidence_ids": [item["id"]]})
        events.append({"id": event_id, "title": f"{item['label']}的行动变化", "description": item["excerpt"], "act": "act_one", "beat": "setup", "conflict_type": "man_vs_man", "source_evidence_ids": [item["id"]]})
        connections.append({"id": f"novel_link_{index + 1}", "source_id": scene_id, "target_id": event_id, "relationship_type": "contains", "semantic_family": "structural", "description": "由原文证据导入"})
    if characters and events:
        for index, event in enumerate(events[:len(characters)]):
            connections.append({"id": f"novel_participates_{index + 1}", "source_id": characters[index]["id"], "target_id": event["id"], "relationship_type": "participates_in", "semantic_family": "structural", "description": "待编剧确认的原文行动归属"})
    premise = _excerpt(text, 0, min(len(text), 900), 700)
    packet_title = title.strip() or "小说改编项目"
    return {
        "schema_version": "novel-adaptation/v1", "title": packet_title,
        "source_summary": {"character_count": len(text), "chapter_count": len(spans),
                           "chunk_count": len(chunks), "chunk_chars": SOURCE_CHUNK_CHARS,
                           "sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "analysis": "full_source_chunked_intake", "truncated": False},
        "source_chunks": chunks,
        "evidence_spans": evidence, "characters": characters, "scenes": scenes, "events": events, "connections": connections,
        "worldview": {"id": "novel_world", "name": packet_title, "premise": premise},
        "themes": [{"id": "novel_theme_source", "title": "从原著中提炼主题（待编剧确认）", "description": "导入阶段不臆造主题；结构阶段结合原文证据生成。"}],
        "adaptation_guardrails": ["所有改编建议都应可回溯至证据片段。", "导入内容是候选结构，不等于已判定的戏剧事实。", "结构生成应先解决主角欲望、因果链与单集推进，再扩写剧本。"],
    }


def packet_to_workspace(packet: dict[str, Any]) -> dict[str, Any]:
    """Create the exact existing workspace contract consumed by Morpho Planner."""
    title = str(packet.get("title") or "小说改编项目")
    evidence = packet.get("evidence_spans") if isinstance(packet.get("evidence_spans"), list) else []
    return {
        "worlds": [packet.get("worldview") or {"id": "novel_world", "name": title, "premise": ""}],
        "worldview": packet.get("worldview") or {"id": "novel_world", "name": title, "premise": ""},
        "themes": packet.get("themes") or [], "characters": packet.get("characters") or [],
        "arc_milestones": [], "scenes": packet.get("scenes") or [], "events": packet.get("events") or [],
        "plot_devices": [], "dialogues": [], "connections": packet.get("connections") or [],
        "workspace_meta": {"worldview_text": (packet.get("worldview") or {}).get("premise", ""), "themes_text": "从原著中提炼主题（待编剧确认）", "novel_adaptation": {"schema_version": packet.get("schema_version"), "title": title, "source_summary": packet.get("source_summary") or {}, "evidence_spans": evidence, "adaptation_guardrails": packet.get("adaptation_guardrails") or [], "source_preparation": {k: (packet.get("source_preparation") or {}).get(k) for k in ("document_id", "revision", "settings")}}},
    }
