"""
Writing a pipeline report as a standalone HTML page.

`report.py` prints the pipeline to a terminal.  This module writes the same
material to a single self-contained HTML file -- no external stylesheets, no
scripts, images embedded -- so an experiment can be handed out, opened offline,
or printed.

Each experiment script takes `--html`, and the page it produces holds, in
order:

* the model text, as written;
* every intermediate form of it -- flat equations, what alias elimination
  removed, the incidence matrix as written and after sorting, the solution
  order, the generated simulation code;
* the simulation results, with the figures the script drew;
* the experiment script itself, so the page says how its own numbers were made.

The `Page` object is deliberately forgiving: when `--html` was not given it is
switched off, every `add_...` call does nothing, and `finish()` shows the plots
on screen instead.  That keeps the experiment scripts free of `if` statements.
"""

import argparse
import base64
import html as html_escape
import io
import pathlib
from typing import List, Optional

from .analysis import StructuralAnalysis, find_states
from .ast_nodes import to_string

# ---------------------------------------------------------------------------
# Style.  Kept in one string so the generated file has no external dependency.
# ---------------------------------------------------------------------------
STYLESHEET = """
:root {
  --page: #ffffff;      --ink: #1b1c1d;     --muted: #5d6470;
  --rule: #dfe3e8;      --panel: #f6f8fa;   --accent: #0b62c4;
  --loop: #c62828;      --good: #1b7a43;    --code: #f6f8fa;
  --hit: #d6e6fb;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #14161a;    --ink: #e6e8ea;     --muted: #9aa3ad;
    --rule: #2b3038;    --panel: #1b1e24;   --accent: #61a5f2;
    --loop: #ef5b5b;    --good: #58c98a;    --code: #1b1e24;
    --hit: #24374d;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 1.5rem 5rem; background: var(--page); color: var(--ink);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
main { max-width: 60rem; margin: 0 auto; }
header { border-bottom: 2px solid var(--rule); margin: 0 0 2rem; padding: 2.5rem 0 1.5rem; }
header h1 { margin: 0 0 .3rem; font-size: 1.9rem; letter-spacing: -.01em; }
header p { margin: 0; color: var(--muted); }
h2 {
  margin: 3rem 0 .8rem; padding-bottom: .35rem; font-size: 1.3rem;
  border-bottom: 1px solid var(--rule);
}
h3 { margin: 1.8rem 0 .5rem; font-size: 1.05rem; color: var(--muted);
     text-transform: uppercase; letter-spacing: .06em; }
p { margin: .8rem 0; }
a { color: var(--accent); }
code, pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre {
  background: var(--code); border: 1px solid var(--rule); border-radius: 6px;
  padding: .9rem 1rem; overflow-x: auto; font-size: .82rem; line-height: 1.5;
}
nav.toc { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
          padding: 1rem 1.2rem; }
nav.toc ol { margin: 0; padding-left: 1.2rem; }
nav.toc li { margin: .15rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .87rem; margin: .8rem 0; }
th, td { text-align: left; padding: .32rem .6rem; border-bottom: 1px solid var(--rule); }
th { color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; color: var(--muted); width: 3rem; }
td.eq { font-family: ui-monospace, Menlo, Consolas, monospace; }
td.note { color: var(--muted); font-size: .8rem; }
.counts { display: flex; flex-wrap: wrap; gap: .6rem; margin: 1rem 0; }
.count { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
         padding: .5rem .8rem; min-width: 7rem; }
.count b { display: block; font-size: 1.35rem; line-height: 1.2; }
.count span { color: var(--muted); font-size: .78rem; }
.grid { overflow-x: auto; margin: 1rem 0; }
table.incidence { width: auto; border-collapse: collapse; font-size: .78rem; }
table.incidence th, table.incidence td { border: 1px solid var(--rule); padding: 0; text-align: center; }
table.incidence td { width: 1.6rem; height: 1.6rem; }
table.incidence th.col { writing-mode: vertical-rl; transform: rotate(180deg);
                         padding: .35rem .1rem; white-space: nowrap; font-weight: 500; }
table.incidence th.row { padding: 0 .5rem; white-space: nowrap; font-weight: 500; }
td.hit { background: var(--hit); }
td.matched { background: var(--accent); color: var(--page); font-weight: 700; }
td.inloop { outline: 2px solid var(--loop); outline-offset: -2px; }
.legend { color: var(--muted); font-size: .8rem; }
.block { border-left: 3px solid var(--rule); padding: .1rem 0 .1rem .8rem; margin: .6rem 0; }
.block.loop { border-left-color: var(--loop); }
.block .head { font-weight: 600; }
.block .eq { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .82rem;
             color: var(--muted); }
figure { margin: 1.5rem 0; }
figure img { max-width: 100%; height: auto; border: 1px solid var(--rule); border-radius: 6px;
             background: #fff; }
figcaption { color: var(--muted); font-size: .85rem; margin-top: .4rem; }
.error { border-left: 3px solid var(--loop); background: var(--panel);
         padding: .8rem 1rem; border-radius: 0 6px 6px 0; }
.error pre { background: transparent; border: none; padding: 0; }
footer { margin-top: 4rem; padding-top: 1rem; border-top: 1px solid var(--rule);
         color: var(--muted); font-size: .82rem; }
"""


def escape(text) -> str:
    return html_escape.escape(str(text), quote=False)


class Page:
    """
    One HTML report, built up section by section.

    When `enabled` is False every method does nothing except `finish()`, which
    shows the figures on screen.  That is what lets an experiment script call
    these methods unconditionally.
    """

    def __init__(self, title: str, subtitle: str = "", output: Optional[pathlib.Path] = None,
                 script: Optional[pathlib.Path] = None, enabled: bool = True):
        self.title = title
        self.subtitle = subtitle
        self.output = output
        self.script = pathlib.Path(script) if script else None
        self.enabled = enabled
        self.sections: List[str] = []
        self.contents: List[tuple] = []          # (anchor, title)
        self._anchors = 0

    # -- building blocks -----------------------------------------------------

    def _open(self, title: str) -> str:
        self._anchors += 1
        anchor = f"s{self._anchors}"
        self.contents.append((anchor, title))
        return f'<h2 id="{anchor}">{escape(title)}</h2>'

    def _add(self, html: str):
        self.sections.append(html)

    def add_text(self, text: str):
        """A paragraph of explanation.  Blank lines separate paragraphs."""
        if not self.enabled:
            return
        for paragraph in text.strip().split("\n\n"):
            self._add(f"<p>{escape(' '.join(paragraph.split()))}</p>")

    def add_code(self, code: str, title: str = "", language: str = "text"):
        if not self.enabled:
            return
        heading = self._open(title) if title else ""
        self._add(f"{heading}<pre><code>{escape(code)}</code></pre>")

    def add_figure(self, figure, caption: str = ""):
        """Embed a Matplotlib figure as a data URI, so the page stands alone."""
        if not self.enabled:
            return
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        note = f"<figcaption>{escape(caption)}</figcaption>" if caption else ""
        self._add(f'<figure><img alt="{escape(caption)}" '
                  f'src="data:image/png;base64,{encoded}">{note}</figure>')

    # -- the model and its intermediate forms --------------------------------

    def add_source(self, path, title: str = "The model"):
        """The `.tiny` file, as written."""
        if not self.enabled:
            return
        path = pathlib.Path(path)
        self._add(self._open(f"{title}  --  {path.name}"))
        self._add(f"<pre><code>{escape(path.read_text())}</code></pre>")

    def add_model(self, compiled, title: str = "How the model was compiled"):
        """Every intermediate form, in pipeline order."""
        if not self.enabled:
            return
        self._add(self._open(title))
        self._add(self._counts(compiled))
        self._add(_flat_equations(compiled))
        self._add(_connection_sets(compiled))
        self._add(_alias(compiled))
        self._add(_variables(compiled))
        if compiled.analysis is not None:
            self._add(_incidence(compiled.analysis, sorted_form=False))
            self._add(_matching(compiled.analysis))
            self._add(_incidence(compiled.analysis, sorted_form=True))
            self._add(_blocks(compiled.analysis))
        if compiled.code is not None:
            self._add("<h3>The generated simulation model</h3>")
            self._add("<p>This is the code that actually runs. It is generated "
                      "from the sorted blocks above, one block at a time.</p>")
            self._add(f"<pre><code>{escape(compiled.code.source)}</code></pre>")
        if compiled.initialization is not None:
            self._add("<h3>The initialization problem</h3>")
            self._add("<p>A second, different system of equations, solved once "
                      "before the integration starts: here the states are "
                      "unknowns too.</p>")
            self._add(_blocks(compiled.initialization_analysis))
            self._add(f"<pre><code>{escape(compiled.initialization.source)}</code></pre>")
        if compiled.model.when_equations:
            self._add(_events(compiled))

    @staticmethod
    def _counts(compiled) -> str:
        analysis = compiled.analysis
        loops = ([b for b in analysis.blocks if len(b) > 1]
                 if analysis is not None else [])
        entries = [
            (len(compiled.flat.equations), "equations after flattening"),
            (len(compiled.model.equations), "after alias elimination"),
            (len(analysis.states) if analysis else len(find_states(compiled.model)),
             "state variables"),
            (len(analysis.blocks) if analysis else "-", "blocks to solve"),
            (len(loops) if analysis else "-", "algebraic loops"),
            (len(compiled.model.when_equations), "when-clauses"),
        ]
        cells = "".join(f'<div class="count"><b>{value}</b><span>{label}</span></div>'
                        for value, label in entries)
        return f'<div class="counts">{cells}</div>'

    def add_error(self, error, compiled=None,
                  title: str = "Why this model cannot be simulated"):
        """A failure is teaching material: show it, and how far the tool got."""
        if not self.enabled:
            return
        self._add(self._open(title))
        self._add(f'<div class="error"><pre>{escape(error)}</pre></div>')
        if compiled is not None:
            self._add("<h3>The stages that did succeed</h3>")
            self._add(_flat_equations(compiled))
            self._add(_variables(compiled))

    # -- results -------------------------------------------------------------

    def add_result(self, result, names=None, title: str = "The simulation"):
        if not self.enabled:
            return
        self._add(self._open(title))
        span = f"{result.time[0]:g} to {result.time[-1]:g} s"
        self._add(f"<p><b>Solver:</b> <span class='mono'>"
                  f"{escape(result.solver)}</span></p>")
        self._add(f"<p>{len(result.time)} output points over {span}, "
                  f"{len(result.events)} event(s)."
                  + (f" <b>{escape(result.message)}</b>" if result.message else "")
                  + "</p>")
        if result.events:
            rows = "".join(
                f"<tr><td class='num'>{number}</td><td class='mono'>{event.time:.6g}</td>"
                f"<td class='mono'>{escape(event.condition)}</td>"
                f"<td class='mono'>" + escape(", ".join(
                    f"{name}: {before:g} -> {after:g}"
                    for name, (before, after) in event.changes.items())) + "</td></tr>"
                for number, event in enumerate(result.events[:40], start=1))
            more = ("" if len(result.events) <= 40 else
                    f"<p class='legend'>... and {len(result.events) - 40} more.</p>")
            self._add("<h3>Events</h3><table><tr><th class='num'>#</th><th>time</th>"
                      f"<th>condition</th><th>what changed</th></tr>{rows}</table>{more}")

        chosen = list(names) if names else [n for n in result.names
                                            if not n.startswith("der(")]
        rows = "".join(
            f"<tr><td class='mono'>{escape(name)}</td>"
            f"<td class='mono'>{result[name][0]:.6g}</td>"
            f"<td class='mono'>{result[name][-1]:.6g}</td></tr>"
            for name in chosen)
        self._add("<h3>Values</h3><table><tr><th>variable</th><th>at start</th>"
                  f"<th>at end</th></tr>{rows}</table>")

    # -- writing it out ------------------------------------------------------

    def finish(self) -> Optional[pathlib.Path]:
        """
        Write the page -- or, when reporting is switched off, show the plots.

        Returns the path written, or None.
        """
        if not self.enabled:
            import matplotlib.pyplot as plt
            plt.show()
            return None

        if self.script is not None and self.script.exists():
            self._add(self._open("The experiment script"))
            self._add("<p>The page above was produced by this script; it is "
                      "included so the numbers can be traced back to the code "
                      "that made them.</p>")
            self._add(f"<pre><code>{escape(self.script.read_text())}</code></pre>")

        contents = "".join(f'<li><a href="#{anchor}">{escape(title)}</a></li>'
                           for anchor, title in self.contents)
        document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(self.title)}</title>
<style>{STYLESHEET}</style>
</head>
<body>
<main>
<header>
  <h1>{escape(self.title)}</h1>
  <p>{escape(self.subtitle)}</p>
</header>
<nav class="toc"><ol>{contents}</ol></nav>
{"".join(self.sections)}
<footer>Generated by TinySim &mdash; a tiny equation-based, acausal modeling
language for teaching. Every number and every line of code on this page was
produced by the pipeline it describes.</footer>
</main>
</body>
</html>
"""
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(document)
        print(f"wrote {self.output}")
        return self.output


# ---------------------------------------------------------------------------
# The individual pipeline stages, as HTML fragments
# ---------------------------------------------------------------------------

def _equation_table(equations, caption: str) -> str:
    rows = "".join(
        f"<tr><td class='num'>{number}</td><td class='eq'>{escape(equation.source)}</td>"
        f"<td class='note'>{escape(equation.origin)}</td></tr>"
        for number, equation in enumerate(equations, start=1))
    return (f"<h3>{escape(caption)}</h3><table>"
            f"<tr><th class='num'>#</th><th>equation</th><th>where it came from</th></tr>"
            f"{rows}</table>")


def _flat_equations(compiled) -> str:
    flat = compiled.flat
    text = ("<p>Every component has been expanded into its own variables and "
            "equations, with dotted names, and every <code>connect</code> has "
            "become real equations: potential variables equal, flow variables "
            "summing to zero.</p>")
    table = _equation_table(flat.equations, "The flat model")
    if flat.initial_equations:
        table += _equation_table(flat.initial_equations, "Initial equations")
    return text + table


def _connection_sets(compiled) -> str:
    sets = compiled.flat.connection_sets
    if not sets:
        return ""
    rows = "".join(
        f"<tr><td class='num'>{number}</td><td class='mono'>{escape(s.connector_class)}</td>"
        f"<td class='mono'>{escape(', '.join(s.connectors))}</td></tr>"
        for number, s in enumerate(sets, start=1))
    return ("<h3>Connection sets</h3><table>"
            "<tr><th class='num'>#</th><th>connector</th><th>members</th></tr>"
            f"{rows}</table>")


def _alias(compiled) -> str:
    alias = compiled.alias
    if alias is None:
        return ("<h3>Alias elimination</h3><p>Skipped: this model was compiled "
                "with <code>eliminate_alias_equations=False</code>.</p>")
    before, after = len(compiled.flat.equations), len(compiled.model.equations)
    removed = "".join(f"<tr><td class='mono'>{escape(alias.describe(name))}</td></tr>"
                      for name in sorted(alias.eliminated))
    return ("<h3>Alias elimination</h3>"
            f"<p>{before} equations become {after}. "
            f"{len(alias.eliminated)} variables were found to be other variables "
            "under a different name, or constants; they are substituted away "
            "here and recovered at the end of the generated code so they can "
            "still be plotted.</p>"
            f"<table>{removed}</table>"
            + _equation_table(compiled.model.equations, "What is left to solve"))


def _variables(compiled) -> str:
    model = compiled.model
    analysis = compiled.analysis
    states = analysis.states if analysis is not None else find_states(model)
    rows = []
    for name, variable in model.variables.items():
        kind = variable.kind
        if name in states:
            kind = "state"
        elif variable.kind == "continuous":
            kind = "algebraic"
        if variable.is_parameter:
            value = f"{model.parameter_values.get(name, float('nan')):g}"
        else:
            value = "" if variable.start is None else to_string(variable.start)
        rows.append(f"<tr><td class='mono'>{escape(name)}</td><td>{kind}</td>"
                    f"<td class='mono'>{escape(value)}</td>"
                    f"<td class='note'>{escape(variable.description)}</td></tr>")
    unknowns = (", ".join(analysis.unknowns) if analysis is not None else "-")
    return ("<h3>Variables</h3>"
            "<p>A variable that appears inside <code>der(...)</code> is a "
            "<b>state</b>: during simulation the integrator supplies its value, "
            "so the unknown in its place is its derivative.</p>"
            "<table><tr><th>variable</th><th>kind</th><th>start / value</th>"
            f"<th>description</th></tr>{''.join(rows)}</table>"
            f"<p class='legend'>Unknowns of the simulation problem: "
            f"<span class='mono'>{escape(unknowns)}</span></p>")


def _incidence(analysis: StructuralAnalysis, sorted_form: bool) -> str:
    """The incidence matrix as a table: which unknown occurs in which equation."""
    if sorted_form:
        rows = [index for block in analysis.blocks for index in block]
        columns = [analysis.matching[index] for index in rows]
        block_of = {}
        for number, block in enumerate(analysis.blocks):
            for index in block:
                block_of[index] = number
        loop_blocks = {number for number, block in enumerate(analysis.blocks)
                       if len(block) > 1}
        heading = "Incidence matrix, sorted into blocks"
        explanation = ("The same matrix with the rows and columns in the order "
                       "the sorting found. Everything above the diagonal is "
                       "empty, which is exactly the statement that the blocks "
                       "can be solved one at a time from the top down. A block "
                       "outlined in red holds more than one equation: an "
                       "algebraic loop.")
    else:
        rows = list(range(len(analysis.equations)))
        columns = list(analysis.unknowns)
        block_of, loop_blocks = {}, set()
        heading = "Incidence matrix, as written"
        explanation = ("A mark means the unknown of that column appears in the "
                       "equation of that row. Nothing here says which equation "
                       "computes what -- that is the next step.")

    header = "".join(f"<th class='col'>{escape(name)}</th>" for name in columns)
    body = []
    for row_position, equation_index in enumerate(rows):
        cells = []
        for column_position, unknown in enumerate(columns):
            classes = []
            if unknown in analysis.incidence[equation_index]:
                matched = analysis.matching.get(equation_index) == unknown
                classes.append("matched" if matched else "hit")
                mark = "&#9679;" if matched else "&times;"
            else:
                mark = ""
            if (sorted_form and block_of.get(equation_index) in loop_blocks
                    and block_of.get(equation_index) == block_of.get(rows[column_position])):
                classes.append("inloop")
            attribute = f" class=\"{' '.join(classes)}\"" if classes else ""
            cells.append(f"<td{attribute}>{mark}</td>")
        body.append(f"<tr><th class='row'>eq {equation_index + 1}</th>"
                    f"{''.join(cells)}</tr>")

    return (f"<h3>{heading}</h3><p>{explanation}</p>"
            f"<div class='grid'><table class='incidence'>"
            f"<tr><th></th>{header}</tr>{''.join(body)}</table></div>"
            "<p class='legend'>&#9679; the unknown this equation was matched "
            "with &nbsp;&nbsp; &times; another occurrence</p>")


def _matching(analysis: StructuralAnalysis) -> str:
    rows = "".join(
        f"<tr><td class='num'>{index + 1}</td>"
        f"<td class='mono'>{escape(analysis.matching[index])}</td>"
        f"<td class='eq'>{escape(analysis.equations[index].source)}</td></tr>"
        for index in range(len(analysis.equations)))
    return ("<h3>Matching</h3>"
            "<p>Which equation is used to compute which unknown, found by the "
            "augmenting-path algorithm. An equation written as "
            "<code>v = R*i</code> may well end up computing the current: "
            "nothing decided that in advance.</p>"
            "<table><tr><th class='num'>eq</th><th>computes</th><th>equation</th>"
            f"</tr>{rows}</table>")


def _blocks(analysis: StructuralAnalysis) -> str:
    parts = []
    for position, block in enumerate(analysis.blocks, start=1):
        unknowns = ", ".join(analysis.matching[index] for index in block)
        loop = len(block) > 1
        label = (f"block {position} &mdash; algebraic loop of size {len(block)}: "
                 f"solve for {escape(unknowns)}" if loop else
                 f"block {position} &mdash; solve for {escape(unknowns)}")
        equations = "".join(
            f"<div class='eq'>{escape(analysis.equations[index].source)}</div>"
            for index in block)
        parts.append(f"<div class='block{' loop' if loop else ''}'>"
                     f"<div class='head'>{label}</div>{equations}</div>")
    return ("<h3>Solution order</h3>"
            "<p>Tarjan's algorithm returns the blocks already in the order they "
            "can be solved.</p>" + "".join(parts))


def _events(compiled) -> str:
    parts = []
    for when_equation in compiled.model.when_equations:
        body = []
        for statement in when_equation.body:
            if type(statement).__name__ == "Reinit":
                body.append(f"reinit({statement.name}, {to_string(statement.value)});")
            else:
                body.append(f"{statement.name} = {to_string(statement.value)};")
        parts.append("<div class='block'><div class='head mono'>when "
                     f"{escape(to_string(when_equation.condition))} then</div>"
                     + "".join(f"<div class='eq'>&nbsp;&nbsp;{escape(line)}</div>"
                               for line in body)
                     + "<div class='eq'>end;</div></div>")
    return ("<h3>Events</h3>"
            "<p>Each condition becomes a zero-crossing function handed to the "
            "integrator. When it crosses zero upwards the integration stops, "
            "the body runs, and integration restarts from the new state.</p>"
            + "".join(parts))


# ---------------------------------------------------------------------------
# The `--html` option the experiment scripts share
# ---------------------------------------------------------------------------

def start(script_path, title: str, subtitle: str = "", argv=None) -> Page:
    """
    Parse `--html` and return a `Page` for this experiment.

    Without `--html` the returned page is switched off: the script runs exactly
    as before and `finish()` shows the figures on screen.
    """
    parser = argparse.ArgumentParser(
        description=f"{title}. Add --html to write a report instead of showing plots.")
    parser.add_argument(
        "--html", nargs="?", const="html", default=None, metavar="DIRECTORY",
        help="write a standalone HTML report to DIRECTORY (default: html/)")
    arguments = parser.parse_args(argv)

    script = pathlib.Path(script_path)
    if arguments.html is None:
        return Page(title, subtitle, script=script, enabled=False)

    # Draw into memory rather than onto a screen that may not exist.
    import matplotlib
    matplotlib.use("Agg")
    output = pathlib.Path(arguments.html) / f"{script.stem}.html"
    return Page(title, subtitle, output=output, script=script, enabled=True)


def write_index(pages, path, title: str = "TinySim experiments"):
    """A small landing page linking the reports that were generated."""
    path = pathlib.Path(path)
    entries = []
    for page_path, page_title, page_subtitle in pages:
        name = pathlib.Path(page_path).name
        entries.append(f'<li><a href="{escape(name)}">{escape(page_title)}</a>'
                       f'<div class="note">{escape(page_subtitle)}</div></li>')
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{STYLESHEET}
ul.index {{ list-style: none; padding: 0; }}
ul.index li {{ border-bottom: 1px solid var(--rule); padding: .9rem 0; font-size: 1.05rem; }}
ul.index .note {{ color: var(--muted); font-size: .87rem; margin-top: .2rem; }}
</style>
</head>
<body>
<main>
<header>
  <h1>{escape(title)}</h1>
  <p>Each report shows one model, every intermediate form of its equations, the
  generated simulation code, and the results.</p>
</header>
<ul class="index">{''.join(entries)}</ul>
<footer>Generated by TinySim.</footer>
</main>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document)
    return path
