from pathlib import Path


def test_base_template_uses_relative_static_assets() -> None:
    template = (
        Path(__file__).parents[1] / "src" / "hamalivpn" / "templates" / "base.html"
    ).read_text()

    assert 'href="/static/app.css' in template
    assert 'src="/static/app.js' in template
    assert "url_for('static'" not in template


def test_connect_template_has_one_guided_incy_import() -> None:
    template = (
        Path(__file__).parents[1] / "src" / "hamalivpn" / "templates" / "connect.html"
    ).read_text()

    assert template.count('href="{{ incy_link }}"') == 1
    assert "Интегрированные ноды в Incy" not in template
    assert "incy_integrated_link" not in template
    assert 'data-client-choice="happ"' in template
    assert 'data-client-choice="incy"' in template
    assert "Подключить всё в Incy" in template
