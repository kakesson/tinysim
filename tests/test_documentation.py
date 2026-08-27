"""
The documentation quotes the source; this checks it still says what the code says.

`docs/pipeline.md` walks through the event-detection code by quoting it. Quoted
code goes stale silently, which is worse than no quote at all, so every line of
those excerpts has to be findable in the source it came from -- or, for the one
excerpt that shows generated code, in code TinySim actually generates.
"""

import pathlib
import re

import pytest

import tinysim

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "docs" / "pipeline.md"


def code_blocks_of(section: str, document: str):
    """The fenced python blocks inside one `###` section of a markdown file."""
    start = document.index(f"### {section}")
    end = document.index("\n### ", start + 1)
    return re.findall(r"```python\n(.*?)```", document[start:end], re.DOTALL)


@pytest.fixture(scope="module")
def haystack():
    """Everything the excerpts are allowed to have come from."""
    sources = [(ROOT / "tinysim" / name).read_text()
               for name in ("simulator.py", "codegen.py", "integrators.py")]
    ball = tinysim.load(ROOT / "examples" / "bouncing_ball.tiny", "BouncingBall")
    return "\n".join(sources + [ball.code.source])


def test_the_event_walkthrough_quotes_the_code_verbatim(haystack):
    blocks = code_blocks_of("The code that finds an event", PIPELINE.read_text())
    assert len(blocks) >= 4, "the walkthrough should quote all four places"

    for block in blocks:
        for line in block.splitlines():
            if not line.strip():
                continue
            # Comments are trimmed for width in the document; code is not.
            quoted = line.split("#")[0].rstrip() if "#" in line else line
            if not quoted.strip():
                continue
            assert quoted in haystack, (
                f"docs/pipeline.md quotes a line that is not in the source "
                f"any more:\n    {quoted!r}")


def test_the_documented_event_policies_are_the_ones_the_code_accepts():
    from tinysim.simulator import EVENT_POLICIES
    document = PIPELINE.read_text()
    for policy in EVENT_POLICIES:
        assert f'`"{policy}"`' in document
    assert '| `events=` | what it does | what it costs |' in document


def test_the_documented_fixed_step_methods_are_the_ones_that_exist():
    from tinysim.integrators import FIXED_STEP_METHODS
    document = PIPELINE.read_text()
    for method in FIXED_STEP_METHODS:
        assert f"`{method}`" in document
