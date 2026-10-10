"""Shared file definitions, installation states, native structures, and constants."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .exceptions import Refusal

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "builds.json"
TARGETS = ("chats", "ask-aside", "chats-toggle")
CHATS_PREFERENCE = b"aside.vertical_tabs.chats_section_enabled"
IMAGE_BASE = 0x180000000
ORIGINAL_DLL_SHA256 = "9cdc4b08d095abb48fdfec2babf7be54ed35eb67ec20af9341d4cf5302e5d510"


@dataclass(frozen=True)
class Artifact:
    """Describe one installed file and its verified patch recipes."""

    path: Path
    original_sha256: str
    recipes: dict


@dataclass(frozen=True)
class Installation:
    """Describe compatible file states and mutually exclusive targets."""

    version: str
    files: dict[str, Artifact]
    states: dict[tuple[str, ...], dict[str, str]]
    conflicts: tuple[tuple[str, ...], ...]
    targets: tuple[str, ...]
    component: dict | None = None

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
        matches = [state for state, expected in self.states.items() if expected == hashes]
        if len(matches) != 1:
            known = all(any(s[name] == digest for s in self.states.values()) for name, digest in hashes.items())
            label = "inconsistent file combination" if known else "unknown / unsupported file hash"
            raise Refusal(f"Installed files have an {label}; no files were changed.")
        return matches[0]

    def select(self, current: tuple[str, ...], selected: list[str]) -> tuple[str, ...]:
        """Merge requested targets and replace mutually exclusive active targets.

        Parameters
        ----------
        current : sequence of str
            Currently installed target names.
        selected : sequence of str
            Requested target names; conflicting requests are refused.

        Returns
        -------
        tuple of str
            Sorted targets for a verified desired state.
        """
        requested = set(selected)
        if not requested or not requested <= set(self.targets):
            raise Refusal("Select at least one configured target.")
        active = set(current)
        for group in self.conflicts:
            if len(requested.intersection(group)) > 1:
                raise Refusal(f'Mutually exclusive targets: {", ".join(group)}')
            if requested.intersection(group):
                active.difference_update(group)
        state = tuple(sorted(active | requested))
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
