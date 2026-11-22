"""Markdown mínimo e seguro para a central de ajuda e as páginas de produto (RF31).

Só o que os textos em `docs/ajuda` usam: títulos `#`, parágrafos, listas (`-` e `1.`),
**negrito**, `código` e [links](https://...). Todo o texto é escapado antes; links só
http(s) ou relativos. Sem dependência externa.
"""

from __future__ import annotations

import html
import re
from typing import Final

_BOLD: Final = re.compile(r"\*\*(.+?)\*\*")
_CODE: Final = re.compile(r"`([^`]+)`")
_LINK: Final = re.compile(r"\[([^\]]+)\]\(((?:https?://|/)[^)\s]*)\)")
_ORDERED: Final = re.compile(r"^\d+\.\s+")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', out)


def to_html(source: str) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    rows: list[list[str]] = []
    list_tag = ""

    def flush() -> None:
        nonlocal list_tag
        if rows:
            head, *body = rows
            cells = "".join(f"<th>{_inline(c)}</th>" for c in head)
            lines = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body
            )
            blocks.append(
                f'<div class="tabela"><table><thead><tr>{cells}</tr></thead>'
                f"<tbody>{lines}</tbody></table></div>"
            )
            rows.clear()
        if paragraph:
            blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()
        if items:
            blocks.append(
                f"<{list_tag}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{list_tag}>"
            )
            items.clear()

    for raw in source.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("|"):
            if not rows:
                flush()
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not all(set(c) <= {"-", ":"} for c in cells):  # linha separadora do cabeçalho
                rows.append(cells)
        elif stripped.startswith("#"):
            flush()
            level = min(len(stripped) - len(stripped.lstrip("#")), 3)
            blocks.append(f"<h{level}>{_inline(stripped.lstrip('#').strip())}</h{level}>")
        elif stripped.startswith(("- ", "* ")) or _ORDERED.match(stripped):
            tag = "ol" if _ORDERED.match(stripped) else "ul"
            if paragraph or (items and tag != list_tag):
                flush()
            list_tag = tag
            items.append(_inline(_ORDERED.sub("", stripped[2:] if tag == "ul" else stripped)))
        elif items and raw.startswith("  "):
            items[-1] += " " + _inline(stripped)  # continuação do item
        else:
            if items:
                flush()
            paragraph.append(stripped)
    flush()
    return "\n".join(blocks)


def title_of(source: str) -> str:
    first = next((line for line in source.splitlines() if line.startswith("# ")), "")
    return first.removeprefix("# ").strip() or "Ajuda"
