"""
Convert source HTML to readable plain text.

`re.sub(r'<[^>]+>', '', html)` deletes tags without replacing them, so
`</p><p>` closes up and the last word of one block welds onto the first word of
the next ("TL;DRHow can we study...", "EnvironmentsEnvironments are..."). The
result is one unbroken wall of text that no downstream renderer can recover,
because the structure is gone before it is ever stored.

This keeps block boundaries as blank lines, turns list items into `- ` bullets
and headings into `## `, and preserves `<code>` as backticks — the markdown the
renderers on both ends already understand.
"""

import re
from html import unescape

try:
    from bs4 import BeautifulSoup, NavigableString, Tag

    _HAVE_BS4 = True
except ImportError:  # pragma: no cover - bs4 is a declared dependency
    _HAVE_BS4 = False

# Elements whose content is markup or chrome, never prose.
_DROP = ('script', 'style', 'noscript', 'template', 'svg', 'iframe', 'form')

_BLOCK = (
    'p', 'div', 'section', 'article', 'header', 'footer', 'blockquote',
    'pre', 'table', 'tr', 'ul', 'ol', 'dl', 'figure', 'figcaption', 'br', 'hr',
)

_HEADINGS = {'h1': '#', 'h2': '##', 'h3': '###', 'h4': '####', 'h5': '#####', 'h6': '######'}


def html_to_text(html: str, max_length: int | None = None) -> str:
    """
    Flatten HTML to markdown-ish plain text, preserving block structure.

    max_length truncates on a word boundary and appends an ellipsis, so a cut
    never lands mid-word or mid-entity.
    """
    if not html:
        return ''

    text = _with_bs4(html) if _HAVE_BS4 else _with_regex(html)

    # Joining inline children with a space leaves a gap before punctuation that
    # followed a tag ("`end_task` : end the session").
    text = re.sub(r'[ \t]+([,.;:!?\)])', r'\1', text)

    # Collapse runs of blank lines, and trim trailing space on each line.
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    if max_length and len(text) > max_length:
        cut = text[:max_length]
        # Prefer the last paragraph break, then the last space.
        for boundary in ('\n\n', ' '):
            idx = cut.rfind(boundary)
            if idx > max_length * 0.6:
                cut = cut[:idx]
                break
        text = cut.rstrip() + '...'

    return text


def _with_bs4(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')

    for tag in soup(_DROP):
        tag.decompose()

    # Inline code becomes backticks so it survives as markdown rather than
    # dissolving into the surrounding sentence.
    for tag in soup.find_all(('code', 'tt')):
        if tag.find_parent('pre'):
            continue  # fenced below, don't double-mark
        tag.replace_with(NavigableString(f'`{tag.get_text()}`'))

    for tag in soup.find_all('pre'):
        tag.replace_with(NavigableString(f'\n\n```\n{tag.get_text().strip()}\n```\n\n'))

    for name, hashes in _HEADINGS.items():
        for tag in soup.find_all(name):
            tag.replace_with(NavigableString(f'\n\n{hashes} {tag.get_text(" ", strip=True)}\n\n'))

    for tag in soup.find_all('li'):
        tag.replace_with(NavigableString(f'\n- {tag.get_text(" ", strip=True)}'))

    # Everything else that is a block gets a hard break around it.
    for tag in soup.find_all(_BLOCK):
        if isinstance(tag, Tag):
            tag.insert_before(NavigableString('\n\n'))
            tag.insert_after(NavigableString('\n\n'))

    return soup.get_text()


def _with_regex(html: str) -> str:
    """Fallback when bs4 is unavailable: same intent, coarser."""
    text = re.sub(r'(?is)<(script|style|noscript)\b.*?</\1>', ' ', html)
    text = re.sub(r'(?i)<li\b[^>]*>', '\n- ', text)
    text = re.sub(r'(?i)<(h[1-6])\b[^>]*>', '\n\n## ', text)
    text = re.sub(r'(?i)<(br|hr)\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|section|article|blockquote|pre|tr|ul|ol|h[1-6])\s*>', '\n\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    return unescape(text)
