"""The HTML reports: do they contain the pipeline, and are they self-contained?"""

from html.parser import HTMLParser

import matplotlib
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

import tinysim                            # noqa: E402
from tinysim import htmlreport            # noqa: E402

from conftest import RC_CIRCUIT           # noqa: E402

VOID_ELEMENTS = {"br", "img", "hr", "meta", "link", "input", "source"}


class WellFormed(HTMLParser):
    """A minimal check that every element that opens also closes, in order."""

    def __init__(self):
        super().__init__()
        self.open_elements = []
        self.problems = []

    def handle_starttag(self, tag, attributes):
        if tag not in VOID_ELEMENTS:
            self.open_elements.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID_ELEMENTS:
            return
        if not self.open_elements:
            self.problems.append(f"</{tag}> with nothing open")
        elif self.open_elements[-1] != tag:
            self.problems.append(f"</{tag}> closes <{self.open_elements[-1]}>")
        else:
            self.open_elements.pop()


def check_well_formed(text):
    checker = WellFormed()
    checker.feed(text)
    assert checker.problems == []
    assert [tag for tag in checker.open_elements if tag != "html"] == []


@pytest.fixture
def rc_page(tmp_path):
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = tinysim.simulate(model, stop=0.5, points=51)
    page = htmlreport.Page("Test report", "a subtitle",
                           output=tmp_path / "report.html")
    page.add_source(_write_source(tmp_path))
    page.add_model(model)
    page.add_result(result)
    page.add_figure(tinysim.plot(result, ["c.v"]), "the capacitor voltage")
    page.finish()
    return (tmp_path / "report.html").read_text()


def _write_source(tmp_path):
    path = tmp_path / "rc.tiny"
    path.write_text(RC_CIRCUIT)
    return path


def test_the_page_is_well_formed_html(rc_page):
    check_well_formed(rc_page)
    assert rc_page.startswith("<!doctype html>")
    assert "<title>Test report</title>" in rc_page


def test_the_page_contains_every_stage(rc_page):
    for heading in ["The flat model", "Connection sets", "Alias elimination",
                    "Variables", "Incidence matrix, as written", "Matching",
                    "Incidence matrix, sorted into blocks", "Solution order",
                    "The generated simulation model"]:
        assert heading in rc_page


def test_the_page_shows_the_equations_and_the_generated_code(rc_page):
    assert "src.p.i + r.p.i = 0" in rc_page            # a connect() equation
    assert "c.C * der(c.v) = c.i" in rc_page           # a component equation
    assert "der_c__v = c__i/c__C" in rc_page           # the generated code


def test_the_page_is_self_contained(rc_page):
    """No network at all: styles inline, images embedded as data URIs."""
    assert "data:image/png;base64," in rc_page
    assert "<style>" in rc_page
    for forbidden in ["http://", "https://", "<script"]:
        assert forbidden not in rc_page


def test_a_disabled_page_writes_nothing_and_shows_the_plots(tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(True))
    page = htmlreport.Page("unused", enabled=False)
    page.add_text("ignored")
    page.add_code("ignored")
    assert page.finish() is None
    assert shown == [True]
    assert list(tmp_path.iterdir()) == []


def test_the_start_helper_reads_the_html_option(tmp_path):
    quiet = htmlreport.start(__file__, "title", argv=[])
    assert quiet.enabled is False

    loud = htmlreport.start(__file__, "title", argv=["--html", str(tmp_path)])
    assert loud.enabled is True
    assert loud.output == tmp_path / "test_htmlreport.html"


def test_a_failing_model_still_gets_a_page(tmp_path, examples):
    page = htmlreport.Page("Failure", output=tmp_path / "failure.html")
    try:
        tinysim.load(examples / "pendulum_cartesian.tiny", "CartesianPendulum")
    except tinysim.StructuralError as error:
        page.add_error(error, getattr(error, "partial_model", None))
    written = page.finish().read_text()
    check_well_formed(written)
    assert "structurally singular" in written
    assert "x^2 + y^2 = L^2" in written                # the flat model is shown


def test_the_index_page_links_the_reports(tmp_path):
    pages = [(tmp_path / "one.html", "First", "about the first"),
             (tmp_path / "two.html", "Second", "about the second")]
    written = htmlreport.write_index(pages, tmp_path / "index.html").read_text()
    check_well_formed(written)
    assert 'href="one.html"' in written and "about the second" in written
