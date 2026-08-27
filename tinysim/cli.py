"""
The `tinysim` command line tool.

    tinysim show examples/electrical.tiny                 # the whole pipeline
    tinysim show examples/electrical.tiny --stages blt,code
    tinysim run  examples/bouncing_ball.tiny --stop 5 --plot h,v
    tinysim check examples/pendulum_cartesian.tiny        # analyse only

It exists so that a model can be looked at without writing a script -- the
Python API in `tinysim/__init__.py` does exactly the same things.
"""

import argparse
import sys

from . import __version__, choose_model, compile_model, simulate
from .flatten import ModelError
from .lexer import TinySimSyntaxError
from .parser import parse_file
from .report import STAGES, explain


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tinysim",
        description="A tiny equation-based, acausal modeling language.")
    parser.add_argument("--version", action="version",
                        version=f"tinysim {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_common(subcommand):
        subcommand.add_argument("file", help="a .tiny model file")
        subcommand.add_argument("-m", "--model", default=None,
                                help="which model in the file to use")
        subcommand.add_argument("--no-alias-elimination", action="store_true",
                                help="keep trivial equations, to see the "
                                     "unsimplified system")

    show = subcommands.add_parser("show", help="print the compilation pipeline")
    add_common(show)
    show.add_argument("--stages", default="all",
                      help=f"comma-separated: {', '.join(STAGES)} (default: all)")

    check = subcommands.add_parser(
        "check", help="parse and analyse the model, reporting problems only")
    add_common(check)

    run = subcommands.add_parser("run", help="simulate the model")
    add_common(run)
    run.add_argument("--stop", type=float, default=1.0, help="stop time")
    run.add_argument("--start", type=float, default=0.0, help="start time")
    run.add_argument("--points", type=int, default=1001,
                     help="number of output points")
    run.add_argument("--method", default="Radau",
                     help="SciPy integrator: Radau, BDF, RK45, ...")
    run.add_argument("--plot", default=None,
                     help="comma-separated variables to plot")
    run.add_argument("--separate", action="store_true",
                     help="one subplot per plotted variable")
    run.add_argument("--save", default=None, help="write the plot to a file")
    run.add_argument("--csv", default=None, help="write the results to a CSV file")
    return parser


def _load(arguments):
    program = parse_file(arguments.file)
    name = choose_model(program, arguments.model, arguments.file)
    return compile_model(
        program, name,
        eliminate_alias_equations=not arguments.no_alias_elimination)


def main(argv=None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        compiled = _load(arguments)

        if arguments.command == "show":
            explain(compiled, stages=arguments.stages)

        elif arguments.command == "check":
            loops = [b for b in compiled.analysis.blocks if len(b) > 1]
            print(f"{compiled.name}: ok -- "
                  f"{len(compiled.analysis.equations)} equations, "
                  f"{len(compiled.analysis.states)} states, "
                  f"{len(compiled.analysis.blocks)} blocks, "
                  f"{len(loops)} algebraic loop(s), "
                  f"{len(compiled.model.when_equations)} when-clause(s)")

        elif arguments.command == "run":
            result = simulate(compiled, stop=arguments.stop, start=arguments.start,
                              points=arguments.points, method=arguments.method)
            _report_run(result, arguments)

    except (TinySimSyntaxError, ModelError) as error:
        # A model that cannot be solved is still worth looking at: show the
        # stages that did succeed before reporting what went wrong.
        partial = getattr(error, "partial_model", None)
        if partial is not None and arguments.command == "show":
            explain(partial, stages="model,flat,connections,alias,variables")
            print("\n" + "=" * 78)
            print("THE PIPELINE STOPPED HERE")
            print("=" * 78)
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _report_run(result, arguments):
    print(f"simulated {result.model_name} from {result.time[0]:g} to "
          f"{result.time[-1]:g} in {len(result.time)} points, "
          f"{len(result.events)} event(s)")
    if result.message:
        print(f"note: {result.message}")
    for event in result.events[:20]:
        print(f"  {event}")
    if len(result.events) > 20:
        print(f"  ... and {len(result.events) - 20} more")

    interesting = [n for n in result.names if not n.startswith("der(")]
    print("\nfinal values:")
    for name in interesting:
        print(f"  {name:<24} {result[name][-1]:12.6g}")

    if arguments.csv:
        _write_csv(result, arguments.csv, interesting)
        print(f"\nwrote {arguments.csv}")

    if arguments.plot or arguments.save:
        from .plotting import plot
        names = arguments.plot.split(",") if arguments.plot else None
        figure = plot(result, names, separate=arguments.separate)
        if arguments.save:
            figure.savefig(arguments.save, dpi=150)
            print(f"wrote {arguments.save}")
        else:
            import matplotlib.pyplot as plt
            plt.show()


def _write_csv(result, path, names):
    import csv
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time"] + names)
        for position, t in enumerate(result.time):
            writer.writerow([t] + [result[name][position] for name in names])


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
