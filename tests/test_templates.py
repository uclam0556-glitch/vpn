from pathlib import Path


def test_base_template_uses_relative_static_assets() -> None:
    template = (
        Path(__file__).parents[1] / "src" / "hamalivpn" / "templates" / "base.html"
    ).read_text()

    assert 'href="/static/app.css"' in template
    assert 'src="/static/app.js"' in template
    assert "url_for('static'" not in template
