"""For test_procs: a tool that starts a child and then gets stuck, ignoring Cancel, as a job deep inside C++ does."""
from atlas_toolkit.core import Inputs, Output, Tool

TOOL = Tool(
    id="test-sleepy",
    name="Sleepy",
    version="0.1",
    category="svg",
    icon="dot",
    action="Run",
    summary="Starts a child process and never finishes.",
    inputs=Inputs("svg"),
    output=Output("{stem}_sleepy.svg", "svg"),
    signature="<desc>test-sleepy</desc>",
    engine=".engine:run",
    runs_in="process",
    preview="svg",
    params=[],
)
