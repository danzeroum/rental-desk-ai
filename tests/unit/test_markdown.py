from app.web.markdown import title_of, to_html


def test_basic_blocks() -> None:
    html = to_html(
        "# Título\n\nUm **texto** com `código` e [link](https://exemplo.com).\n\n"
        "- um\n- dois\n  continua\n\n1. primeiro\n2. segundo\n"
    )
    assert "<h1>Título</h1>" in html
    assert (
        '<p>Um <strong>texto</strong> com <code>código</code> e <a href="https://exemplo.com">link</a>.</p>'
        in html
    )
    assert "<ul><li>um</li><li>dois continua</li></ul>" in html
    assert "<ol><li>primeiro</li><li>segundo</li></ol>" in html


def test_escapes_html_and_unsafe_links() -> None:
    html = to_html("<script>alert(1)</script> [x](javascript:alert(1))")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "href" not in html


def test_title() -> None:
    assert title_of("# Calendários\n\ntexto") == "Calendários"
    assert title_of("sem título") == "Ajuda"


def test_table() -> None:
    html = to_html("| Situação | data |\n|---|---|\n| Natal | **24/12** |\n")
    assert "<th>Situação</th>" in html and "<td><strong>24/12</strong></td>" in html
    assert "---" not in html
