"""Create a separate original-browser fixture; never use the normal user profile."""

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aside_patcher.common.models import MANIFEST_PATH
from aside_patcher.common.utils import file_sha256
from aside_patcher.discovery import validate_browser_images
from aside_patcher.manifest import load_build


def prepare_trial(archive: Path, settings: Path | None, trial: Path, version: str | None = None) -> dict:
    """Copy a reviewed build into a fresh, independent application and profile."""
    trial = trial.resolve()
    if trial.exists():
        raise ValueError("Trial must be a new directory; never replace an existing profile")
    build = load_build(MANIFEST_PATH, version)
    matches = list(archive.glob(f"browsers/{build.version}/*/browser.json"))
    if len(matches) != 1:
        raise ValueError("Need one verified original archive for the requested build")
    metadata = json.loads(matches[0].read_text(encoding="utf-8"))
    package_root = matches[0].parent.resolve()
    dll = (package_root / metadata["dll"]).resolve()
    if not dll.is_relative_to(package_root) or not dll.parent.parent.is_relative_to(package_root):
        raise ValueError("Archived browser path escaped its package directory")
    if metadata.get("verified") is not True or file_sha256(dll) != build.files["dll"].original_sha256:
        raise ValueError("Original browser hash mismatch")
    validate_browser_images({"executable": str(dll.parent.parent / "Aside.exe"), "dll": str(dll), "version": build.version})
    if settings is not None:
        if not build.component or "appearance" not in build.files:
            raise ValueError("Full trial requires a reviewed settings component")
        if file_sha256(settings) != build.files["appearance"].original_sha256:
            raise ValueError("Original settings hash mismatch")

    shutil.copytree(dll.parent.parent, trial / "Application")
    (trial / "profile").mkdir()
    if settings is not None:
        assets = trial / "profile" / "AsideAgentManager" / build.component["version"] / "assets"
        assets.mkdir(parents=True)
        shutil.copyfile(settings, assets / build.files["appearance"].path.name)
    request = {
        "protocol_version": 1,
        "installation": str(trial / "Application" / build.version / "Aside.dll"),
        "data_root": str(trial / "profile"),
    }
    (trial / "request-context.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return request


def main() -> None:
    """Copy a verified archived browser and settings artifact to a fresh trial."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--settings", type=Path, help="Verified original settings asset; omit for native-only trials")
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--version", help="Exact reviewed build; required when multiple builds are registered")
    args = parser.parse_args()
    print(json.dumps(prepare_trial(args.archive, args.settings, args.trial, args.version)))


if __name__ == "__main__":
    main()
