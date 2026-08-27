"""
Plotting helpers -- Matplotlib only, nothing clever.

Two kinds of picture are useful when teaching this material:

* the simulation results themselves, and
* the *structure* of the equation system: the incidence matrix, before and
  after sorting, which is where the block lower triangular shape becomes
  visible at a glance.
"""

from typing import List, Optional, Sequence

import matplotlib.pyplot as plt
import numpy as np

from .analysis import StructuralAnalysis
from .simulator import SimulationResult


def plot(result: SimulationResult, names: Optional[Sequence[str]] = None,
         title: Optional[str] = None, show_events: bool = True,
         separate: bool = False, ax=None, show: bool = False):
    """
    Plot variables of a simulation result against time.

    `names` defaults to the state variables.  Set `separate=True` for one
    subplot per variable, which is usually what you want when the quantities
    have different units.
    """
    if names is None:
        names = [n for n in result.names if not n.startswith("der(")][:4]
    names = list(names)

    if separate:
        figure, axes = plt.subplots(len(names), 1, sharex=True,
                                    figsize=(8, 2.2 * len(names)))
        axes = np.atleast_1d(axes)
    else:
        if ax is None:
            figure, single = plt.subplots(figsize=(8, 4.5))
        else:
            figure, single = ax.figure, ax
        axes = [single] * len(names)

    for axis, name in zip(axes, names):
        axis.plot(result.time, result[name], label=name)
        axis.set_ylabel(name if separate else "")
        axis.grid(True, alpha=0.3)

    if show_events and result.events:
        for axis in dict.fromkeys(axes):
            for event in result.events:
                axis.axvline(event.time, color="crimson", linestyle=":",
                             linewidth=0.8, alpha=0.6)

    axes[-1].set_xlabel("time [s]")
    if not separate:
        axes[0].legend(loc="best")
    axes[0].set_title(title or f"{result.model_name}"
                      + (f"   ({len(result.events)} events)" if result.events else ""))
    figure.tight_layout()
    if show:
        plt.show()
    return figure


def plot_incidence(analysis: StructuralAnalysis, sorted_form: bool = False,
                   title: Optional[str] = None, ax=None, show: bool = False):
    """
    Show which unknown appears in which equation, as a spy plot.

    With `sorted_form=True` the rows and columns are permuted into the order
    the BLT sorting found, which makes the block lower triangular structure
    plain: everything above the diagonal blocks is empty, so the blocks can be
    solved one after the other from the top down.
    """
    if sorted_form:
        row_order = [index for block in analysis.blocks for index in block]
        column_order = [analysis.matching[index] for index in row_order]
    else:
        row_order = list(range(len(analysis.equations)))
        column_order = list(analysis.unknowns)

    matrix = np.zeros((len(row_order), len(column_order)))
    for row, equation_index in enumerate(row_order):
        for column, unknown in enumerate(column_order):
            if unknown in analysis.incidence[equation_index]:
                matrix[row, column] = 1.0
                if analysis.matching.get(equation_index) == unknown:
                    matrix[row, column] = 2.0        # the matched entry

    if ax is None:
        figure, ax = plt.subplots(figsize=(1 + 0.4 * len(column_order),
                                           1 + 0.4 * len(row_order)))
    else:
        figure = ax.figure

    ax.imshow(matrix, cmap="Blues", vmin=0, vmax=2)
    ax.set_xticks(range(len(column_order)))
    ax.set_xticklabels(column_order, rotation=90, fontsize=7)
    ax.set_yticks(range(len(row_order)))
    ax.set_yticklabels([f"eq {i + 1}" for i in row_order], fontsize=7)
    ax.set_xlabel("unknowns   (dark = the one this equation is solved for)")
    ax.set_ylabel("equations")

    if sorted_form:
        # Outline every diagonal block, so algebraic loops stand out.
        position = 0
        for block in analysis.blocks:
            size = len(block)
            ax.add_patch(plt.Rectangle((position - 0.5, position - 0.5), size, size,
                                       fill=False, edgecolor="crimson", linewidth=1.2))
            position += size

    ax.set_title(title or ("incidence matrix, BLT sorted" if sorted_form
                           else "incidence matrix, as written"))
    figure.tight_layout()
    if show:
        plt.show()
    return figure
