"""The report, the plots and the command line -- the parts students touch."""

import io
import pathlib

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
                    "10. HOW THIS SYSTEM IS ACTUALLY SOLVED",
                    "11. GENERATED SIMULATION CODE", "12. INITIALIZATION",
                    "13. EVENTS"]:
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


EXPERIMENTS = pathlib.Path(__file__).resolve().parents[1] / "experiments"
GENERATED = pathlib.Path(__file__).resolve().parents[1] / "html"


def test_every_experiment_reports_its_contracts():
    """A page that shows a model should show what the model promised."""
    without = [script.name for script in sorted(EXPERIMENTS.glob("0*.py"))
               if "add_contracts" not in script.read_text()]
    assert without == [], f"no contracts reported in: {', '.join(without)}"


@pytest.mark.skipif(not GENERATED.exists(),
                    reason="the reports have not been generated")
def test_every_generated_report_contains_a_contract():
    for page in sorted(GENERATED.glob("0*.html")):
        text = page.read_text()
        assert "class='contract'" in text, f"{page.name} reports no contract"
        assert "assume implies" in text, f"{page.name} omits what a contract means"


def test_cli_show_html_includes_the_contracts(tmp_path, examples):
    target = tmp_path / "report.html"
    assert main(["show", str(examples / "electrical.tiny"), "--html",
                 str(target)]) == 0
    text = target.read_text()
    assert "ChargesInTime" in text
    assert "r : WithinItsRating" in text          # the component contract too


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


# ---------------------------------------------------------------------------
# The solution procedure: how the sorted blocks are actually solved
# ---------------------------------------------------------------------------

def procedure_of(model) -> str:
    return explain_to_string(model, "procedure")


def test_the_procedure_explains_an_explicit_chain():
    text = procedure_of(tinysim.load_source(RC_CIRCUIT, "RC"))
    assert "HOW THIS SYSTEM IS ACTUALLY SOLVED" in text
    assert "c.v = 0" in text                          # the start value
    assert "c.i := r.v/r.R" in text                   # model names, not c__i
    assert "der(c.v) goes back to the integrator" in text
    assert "c__i" not in text                         # no generated Python here


def test_the_procedure_explains_a_linear_loop(examples):
    text = procedure_of(tinysim.load(examples / "resistor_network.tiny"))
    assert "solve simultaneously for" in text
    assert "linear in those unknowns" in text
    assert "one matrix solve A x = b, no iteration" in text


def test_the_procedure_explains_a_nonlinear_loop(examples):
    text = procedure_of(tinysim.load(examples / "diode_circuit.tiny"))
    assert "not linear in those unknowns" in text
    assert "solved by iteration" in text
    assert "d.i = d.Isat * (exp(d.v / d.Vt) - 1)" in text


def test_the_procedure_explains_the_initialization_system(examples):
    text = procedure_of(tinysim.load(examples / "tank.tiny"))
    assert "initial equations" in text
    assert "der(h) := 0" in text                      # solved as its own system
    assert "h := q^2/k^2" in text


def test_the_procedure_explains_a_state_jump(examples):
    text = procedure_of(tinysim.load(examples / "bouncing_ball.tiny"))
    assert "is watched as the crossing function  -h" in text
    assert "the state v jumps to -(e * v)" in text
    assert "restarts from the updated state" in text


def test_the_procedure_explains_discrete_variables(examples):
    text = procedure_of(tinysim.load(examples / "thermostat.tiny"))
    assert "Discrete on starts at 1" in text
    assert "the discrete on becomes 0" in text
    assert "T - Tset - band" in text                  # the crossing function


def test_the_procedure_appears_in_the_html_between_sorting_and_code(tmp_path,
                                                                    examples):
    from tinysim.htmlreport import Page
    model = tinysim.load(examples / "bouncing_ball.tiny", "BouncingBall")
    page = Page("procedure", output=tmp_path / "p.html")
    page.add_model(model)
    text = page.finish().read_text()
    assert (text.index("Solution order")
            < text.index("How this system is actually solved")
            < text.index("The generated simulation model"))
    for phrase in ["1. Once, before the first step", "2. At every evaluation",
                   "3. Whenever a condition changes", "4. In between"]:
        assert phrase in text
