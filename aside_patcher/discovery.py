"""Discover Windows Aside installations without assuming an account or drive."""

import os
import struct
from dataclasses import replace
from pathlib import Path

from .common.exceptions import Refusal
from .common.models import Installation
from .common.utils import file_sha256, resolve_manifest_path
from .manifest import load_build
from .native_scope import native_only_build
from .windows import file_version


def discover(roots: list[Path] | None = None) -> list[dict]:
    """Find browser DLLs and their matching launcher.

    Parameters
    ----------
    roots : list of Path or None
        Optional application directories; defaults to Windows install locations.

    Returns
    -------
    list of dict
        Discovered DLL paths, version directories, and launcher paths.
    """
    if roots is None:
        roots = [resolve_manifest_path(f"%{base}%/Aside/Application") for base in ("ProgramFiles", "LOCALAPPDATA")]
        roots += _registry_roots()
    found = {}
    for root in roots:
        root = root.resolve()
        if not (root / "Aside.exe").is_file():
            continue
        for dll in root.glob("*/Aside.dll"):
            if not all(part.isdigit() for part in dll.parent.name.split(".")):
                continue
            found[str(dll.resolve()).casefold()] = {
                "dll": str(dll.resolve()),
                "version": dll.parent.name,
                "executable": str(root / "Aside.exe"),
            }
    return sorted(found.values(), key=lambda item: item["dll"].casefold())


def _registry_roots() -> list[Path]:
    """Read Aside application registrations from both Windows registry views.

    Returns
    -------
    list of Path
        Registered application directories accessible to the caller.
    """
    if os.name != "nt":
        return []
    import winreg  # pylint: disable=import-outside-toplevel

    result = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(
                    hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Aside.exe", 0, winreg.KEY_READ | view
                ) as key:
                    result.append(Path(winreg.QueryValue(key, None)).parent)
            except OSError:
                continue
    return result


def resolve_installation(
    manifest: Path, item: dict, data_root: Path, native_only: bool = False, adapter: Installation | None = None
) -> Installation:
    """Bind a verified build to discovered browser and current-user settings paths.

    Parameters
    ----------
    manifest : Path
        Reviewed build registry.
    item : dict
        Browser entry returned by discovery.
    data_root : Path
        Original user's Aside user-data directory.
    native_only : bool
        Whether only DLL artifacts should be considered.
    adapter : Installation or None
        Authenticated cached adapter, or the bundled registry when absent.

    Returns
    -------
    Installation
        Hash-guarded installation using the selected machine paths.
    """
    validate_browser_images(item)
    build = adapter or load_build(manifest, item["version"])
    dll = Path(item["dll"]).resolve()
    if dll.name != "Aside.dll" or dll.parent.name != build.version:
        raise Refusal("Discovered browser version does not match its build.")
    files = dict(build.files)
    files["dll"] = replace(files["dll"], path=dll)
    component = _bind_component(build, files, data_root, native_only)
    result = replace(build, files=files, component=component)
    return native_only_build(result) if native_only else result


def validate_browser_images(item: dict) -> None:
    """Require matching actual browser versions and supported x64 PE images.

    Parameters
    ----------
    item : dict
        Discovered launcher, DLL, and expected browser version.
    """
    for name in ("executable", "dll"):
        path = Path(item[name])
        _validate_x64_image(path)
        actual_version = file_version(path)
        if actual_version != item["version"]:
            raise Refusal(f"Browser files have mixed versions: {path.name} is {actual_version}, expected {item['version']}.")


def _validate_x64_image(path: Path) -> None:
    """Read only PE headers and reject truncated, non-PE, or non-x64 images.

    Parameters
    ----------
    path : Path
        Selected browser launcher or DLL.
    """
    with path.open("rb") as stream:
        dos_header = stream.read(64)
        if len(dos_header) != 64 or dos_header[:2] != b"MZ":
            raise Refusal(f"Browser file is not a valid Windows executable: {path}")
        pe_offset = struct.unpack_from("<I", dos_header, 60)[0]
        stream.seek(pe_offset)
        pe_header = stream.read(26)

    if len(pe_header) != 26 or pe_header[:4] != b"PE\0\0":
        raise Refusal(f"Browser PE header is invalid: {path}")
    machine = struct.unpack_from("<H", pe_header, 4)[0]
    optional_magic = struct.unpack_from("<H", pe_header, 24)[0]
    if machine != 0x8664 or optional_magic != 0x20B:
        raise Refusal("Only Windows x64 Aside installations are supported.")


def _bind_component(build, files, data_root, native_only):
    """Bind the independently updated settings artifact without broadening support.

    Parameters
    ----------
    build : Installation
        Reviewed artifact hash sets.
    files : dict
        Mutable artifact mapping being rebound.
    data_root : Path
        Original user's browser-data root.
    native_only : bool
        Whether unavailable settings should be omitted.

    Returns
    -------
    dict or None
        Rebound component version guard.
    """
    if "appearance" not in files:
        return build.component
    directory = data_root / "AsideAgentManager"
    known_hashes = {state["appearance"] for _, state in tuple(build.states.items()) + build.previous_states}
    matches = [p for p in directory.glob("*/assets/appearance-*.js") if file_sha256(p) in known_hashes]
    if len(matches) != 1 and not native_only:
        raise Refusal("Settings component is missing, unsupported, or ambiguous. Native-only operations remain available.")
    if matches:
        files["appearance"] = replace(files["appearance"], path=matches[0])
    return dict(build.component, directory=str(directory)) if build.component else None
