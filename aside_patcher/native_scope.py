"""Scope bookmark appearance operations to the verified native artifact."""

from dataclasses import replace

from .common.exceptions import Refusal
from .common.models import Installation


def native_only_build(build: Installation) -> Installation:
    """Scope an already validated build to its hash-guarded DLL.

    Parameters
    ----------
    build : Installation
        Fully parsed supported build with native recipes and output hashes.

    Returns
    -------
    Installation
        Native-only definition that never reads or modifies settings artifacts.
    """
    if "dll" not in build.files:
        raise Refusal("Native-only mode requires a supported DLL.")
    states = {targets: {"dll": hashes["dll"]} for targets, hashes in build.states.items()}
    if len({hashes["dll"] for hashes in states.values()}) != len(states):
        raise Refusal("Native-only states are ambiguous.")
    previous = tuple((targets, {"dll": hashes["dll"]}) for targets, hashes in build.previous_states)
    return replace(build, files={"dll": build.files["dll"]}, states=states, previous_states=previous, component=None)
