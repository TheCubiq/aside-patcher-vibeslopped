"""Shared file definitions, installation states, native structures, and constants."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .exceptions import Refusal

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "builds.json"


@dataclass(frozen=True)
class PatchDefinition:
    """Declare a patch ID, supported builds, file recipes, and local renderers."""

    patch_id: str
    description: str
    versions: tuple[str, ...]
    conflicts: tuple[str, ...]
    files: dict[str, dict]
    renderers: dict[str, Callable[[bytes], bytes]] = field(default_factory=dict)


@dataclass(frozen=True)
class Artifact:
    """Describe one installed file and its verified patch recipes."""

    path: Path
    original_sha256: str
    recipes: dict


@dataclass(frozen=True)
class Installation:  # pylint: disable=too-many-instance-attributes
    """Describe compatible file states and mutually exclusive targets."""

    version: str
    files: dict[str, Artifact]
    states: dict[tuple[str, ...], dict[str, str]]
    conflicts: tuple[tuple[str, ...], ...]
    component: dict | None = None
    patches: dict[str, PatchDefinition] = field(default_factory=dict)
    previous_states: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = ()
    state_payloads: dict = field(default_factory=dict)
    patch_metadata: dict = field(default_factory=dict)

    @property
    def targets(self) -> tuple[str, ...]:
        """Return patch IDs present in the reviewed output combinations.

        Returns
        -------
        tuple[str, ...]
            Available patch IDs in alphabetical order.
        """
        return tuple(sorted({target for state in self.states for target in state}))

    def identify(self, hashes: dict[str, str]) -> tuple[str, ...]:
        """Identify the unique installation state matching all file hashes.

        Parameters
        ----------
        hashes : dict of str to str
            Observed SHA-256 digest for every artifact.

        Returns
        -------
        tuple of str
            Targets installed across the matching file state.
        """
        accepted = tuple(self.states.items()) + self.previous_states
        matches = [state for state, expected in accepted if expected == hashes]
        if len(matches) != 1:
            known = all(any(expected[name] == digest for _, expected in accepted) for name, digest in hashes.items())
            label = "inconsistent file combination" if known else "unknown / unsupported file hash"
            raise Refusal(f"Installed files have an {label}; no files were changed.")
        return matches[0]

    def choose(self, current: tuple[str, ...], selected: list[str], *, enable: bool) -> tuple[str, ...]:
        """Enable or disable patch IDs and require a verified resulting state.

        Parameters
        ----------
        current : tuple[str, ...]
            Installed patches identified from file hashes.
        selected : list[str]
            Requested patch IDs.
        enable : bool
            True to enable selections, False to remove them.

        Returns
        -------
        tuple[str, ...]
            Normalized patch combination with reviewed output hashes.
        """
        requested = set(selected)
        if not requested or not requested <= set(self.targets):
            raise Refusal("Select at least one configured target.")
        active = set(current) - requested
        if enable:
            for group in self.conflicts:
                if len(requested.intersection(group)) > 1:
                    raise Refusal(f'Mutually exclusive targets: {", ".join(group)}')
                if requested.intersection(group):
                    active.difference_update(group)
            active.update(requested)
        state = tuple(sorted(active))
        if state not in self.states:
            raise Refusal("Requested combination has no verified hashes.")
        return state


class Code:
    """Emit x64 instructions and resolve relative address fixups.

    Parameters
    ----------
    rva : int
        Relative virtual address of the first emitted instruction.
    """

    def __init__(self, rva: int) -> None:
        self.rva = rva
        self.data = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, instruction_hex: str) -> None:
        """Append machine-code bytes written as hexadecimal text.

        Parameters
        ----------
        instruction_hex : str
            Hexadecimal instruction bytes.
        """
        self.data.extend(bytes.fromhex(instruction_hex))

    def label(self, name: str) -> None:
        """Record the current instruction address under a symbolic name.

        Parameters
        ----------
        name : str
            Symbolic instruction label.
        """
        self.labels[name] = self.rva + len(self.data)

    def rel(self, opcode: str, destination: int | str) -> None:
        """Emit an instruction with a deferred relative-address operand.

        Parameters
        ----------
        opcode : str
            Hexadecimal opcode preceding a signed 32-bit displacement.
        destination : int or str
            Destination RVA or symbolic label.
        """
        self.emit(opcode)
        self.fixups.append((len(self.data), destination))
        self.data.extend(b"\0" * 4)

    def sequence(self, instructions: tuple[str | tuple[str, int | str], ...]) -> None:
        """Emit an ordered listing of raw instructions and relative-address operands.

        Parameters
        ----------
        instructions : tuple[str or tuple[str, int or str], ...]
            Hexadecimal byte strings, or opcode/destination pairs with deferred fixups.
        """
        for instruction in instructions:
            if isinstance(instruction, str):
                self.emit(instruction)
            else:
                self.rel(*instruction)

    def finish(self, labels: dict[str, int]) -> bytes:
        """Resolve all relative operands and return immutable machine code.

        Parameters
        ----------
        labels : dict of str to int
            Resolved label RVAs.

        Returns
        -------
        bytes
            Machine code with resolved relative displacements.
        """
        for offset, destination in self.fixups:
            if isinstance(destination, str):
                destination = labels[destination]
            struct.pack_into("<i", self.data, offset, destination - (self.rva + offset + 4))
        return bytes(self.data)


@dataclass(frozen=True)
class Section:
    """Record virtual and physical bounds of one PE section."""

    rva: int
    raw_size: int
    file_offset: int
    virtual_size: int


@dataclass(frozen=True)
class ImageLayout:
    """Record the original PE headers and section mapping."""

    pe_header: int
    optional_header: int
    section_table: int
    sections: tuple[Section, ...]


@dataclass
class Payload:
    """Assemble aligned hook blocks and their shared data.

    Attributes
    ----------
    rva : int
        Relative virtual address of the new section.
    data : bytearray
        Section contents in block order.
    blocks : list of Code
        Emitters awaiting relative-address resolution.
    labels : dict of str to int
        Instruction and shared-data RVAs by name.
    """

    rva: int
    data: bytearray = field(default_factory=bytearray)
    blocks: list[Code] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)

    def start(self, name: str) -> Code:
        """Start an aligned hook block and register its symbolic address.

        Parameters
        ----------
        name : str
            Symbolic hook or block name.

        Returns
        -------
        Code
            Emitter for the new aligned block.
        """
        self.data.extend(b"\x90" * (-len(self.data) % 16))
        code = Code(self.rva + len(self.data))
        self.labels[name] = code.rva
        self.blocks.append(code)
        return code

    def append(self, code: Code) -> None:
        """Append a completed hook block and merge its internal labels.

        Parameters
        ----------
        code : Code
            Completed emitter with instruction bytes and internal labels.
        """
        self.labels.update(code.labels)
        self.data.extend(code.data)
