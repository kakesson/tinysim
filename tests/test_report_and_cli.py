"""The report, the plots and the command line -- the parts students touch."""

import io

import matplotlib
import pytest

matplotlib.use("Agg")           # no window during tests

import tinysim
from tinysim import report
from tinysim.cli import main

from conftest import RC_CIRCUIT


def explain_to_string(model, stages="all") -> str:
    buffer = io.StringIO()
    tinysim.explain(model, stages=stages, file=buffer)
    return buffer.getvalue()


def test_explain_shows_every_stage_of_the_pipeline():
    text = explain_to_string(tinysim.load_source(RC_CIRCUIT, "RC"))
    for heading in ["1. MODEL", "2. FLATTENED MODEL", "3. CONNECTION SETS",
                    "4. ALIAS ELIMINATION", "5. VARIABLES", "6. INCIDENCE MATRIX",
                    "7. MATCHING", "8. INCIDENCE MATRIX", "9. BLT SORTING",
                    "10. GENERATED SIMULATION CODE", "11. INITIALIZATION",
                    "12. EVENTS"]:
        assert heading in text


def test_explain_can_show_selected_stages():
    text = explain_to_string(tinysim.load_source(RC_CIRCUIT, "RC"), "flat,code")
    assert "FLATTENED MODEL" in text
    assert "MATCHING" not in text


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError, match="unknown stage"):
        explain_to_string(tinysim.load_source(RC_CIRCUIT, "RC"), "nonsense")


def test_the_flat_model_report_names_where_equations_came_from():
    text = explain_to_string(tinysim.load_source(RC_CIRCUIT, "RC"), "flat")
    assert "connect(" in text                     # equations from connections
    assert "Capacitor" in text                    # and from component classes


def test_incidence_report_marks_the_matched_entries():
    text = explain_to_string(tinysim.load_source(RC_CIRCUIT, "RC"), "blt")
    assert "X" in text and "x" in text
    assert "block" in text


def test_events_report_describes_when_clauses(examples):
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    text = explain_to_string(model, "events")
    assert "when h < 0" in text
    assert "reinit(v, -(e * v))" in text or "reinit(v, -e * v)" in text


def test_plotting_returns_a_figure():
    from tinysim.plotting import plot, plot_incidence
    model = tinysim.load_source(RC_CIRCUIT, "RC")
    result = tinysim.simulate(model, stop=0.5, points=51)
    figure = plot(result, ["c.v", "r.i"])
    assert len(figure.axes) == 1
    figure = plot(result, ["c.v", "r.i"], separate=True)
    assert len(figure.axes) == 2
    assert plot_incidence(model.analysis, sorted_form=True) is not None


def test_cli_check(capsys, examples):
    assert main(["check", str(examples / "electrical.tiny")]) == 0
    assert "RCCircuit: ok" in capsys.readouterr().out


def test_cli_show_selected_stages(capsys, examples):
    assert main(["show", str(examples / "pendulum.tiny"), "--stages", "code"]) == 0
    assert "def evaluate" in capsys.readouterr().out


def test_cli_show_without_alias_elimination(capsys, examples):
    assert main(["show", str(examples / "electrical.tiny"), "--stages", "alias",
                 "--no-alias-elimination"]) == 0
    assert "skipped" in capsys.readouterr().out


def test_cli_run_writes_csv(tmp_path, capsys, examples):
    target = tmp_path / "out.csv"
    assert main(["run", str(examples / "pendulum.tiny"), "--stop", "1",
                 "--points", "11", "--csv", str(target)]) == 0
    assert "simulated Pendulum" in capsys.readouterr().out
    lines = target.read_text().splitlines()
    assert lines[0].startswith("time,")
    assert len(lines) == 12


def test_cli_run_saves_a_plot(tmp_path, examples):
    target = tmp_path / "plot.png"
    assert main(["run", str(examples / "bouncing_ball.tiny"), "--stop", "2",
                 "--points", "201", "--plot", "h,v", "--save", str(target)]) == 0
    assert target.exists() and target.stat().st_size > 1000


def test_cli_reports_errors_without_a_traceback(capsys, tmp_path):
    broken = tmp_path / "broken.tiny"
    broken.write_text("model M Real x equation x = 1; end M;")
    assert main(["check", str(broken)]) == 1
    assert "error:" in capsys.readouterr().err


def test_show_prints_the_stages_that_succeeded_before_a_failure(capsys, examples):
    """A model that cannot be solved is still worth looking at."""
    assert main(["show", str(examples / "pendulum_cartesian.tiny")]) == 1
    captured = capsys.readouterr()
    assert "FLATTENED MODEL" in captured.out
    assert "THE PIPELINE STOPPED HERE" in captured.out
    assert "x^2 + y^2 = L^2" in captured.out
    assert "structurally singular" in captured.err
    assert "Pantelides" in captured.err


def test_cli_show_can_write_html(tmp_path, examples):
    target = tmp_path / "report.html"
    assert main(["show", str(examples / "dcmotor.tiny"), "--html", str(target)]) == 0
    text = target.read_text()
    assert "The generated simulation model" in text
    assert "emf.flange.tau + load.flange.tau = 0" in text     # a connect equation


def test_cli_html_report_of_a_model_that_does_not_compile(tmp_path, examples):
    target = tmp_path / "broken.html"
    assert main(["show", str(examples / "pendulum_cartesian.tiny"),
                 "--html", str(target)]) == 1
    text = target.read_text()
    assert "structurally singular" in text
    assert "The stages that did succeed" in text
