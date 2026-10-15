"""Acquire immutable official artifacts without executing an installer."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PUBLISHERS = {"CN=AT YOUR SIDE INC, O=AT YOUR SIDE INC, L=San Francisco, S=California, C=US"}


def _trusted(evidence: dict) -> bool:
    return evidence["signature_status"] == "Valid" and evidence["publisher"] in PUBLISHERS


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _preserve(source: Path, target: Path, digest: str) -> None:
    if target.exists():
        if _digest(target) != digest:
            raise ValueError("Existing archive artifact was altered")
    else:
        shutil.copyfile(source, target)
    target.chmod(0o444)


def attest(path: Path) -> dict:
    """Read Windows file version and Authenticode evidence."""
    script = r"""
 $ErrorActionPreference='Stop'
 Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
 $p=$env:ASIDE_ARCHIVE_FILE
 $s=Get-AuthenticodeSignature -LiteralPath $p
 $v=(Get-Item -LiteralPath $p).VersionInfo
 @{signature_status=[string]$s.Status;publisher=[string]$s.SignerCertificate.Subject;
 timestamp_publisher=[string]$s.TimeStamperCertificate.Subject;
 file_version=$v.FileVersion;product_version=$v.ProductVersion}|ConvertTo-Json -Compress
 """
    env = dict(os.environ, ASIDE_ARCHIVE_FILE=str(path))
    env.pop("PSModulePath", None)  # Windows PowerShell must not inherit PowerShell 7 modules.
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def acquire(root: Path, version: str) -> Path:
    """Download one official installer to a content-addressed directory."""
    if not re.fullmatch(r"1\.0\.\d+\.\d+", version):
        raise ValueError("Invalid browser version")
    url = f"https://releases.aside.com/dev-updater/windows/{version}/AsideInstaller-{version}-win-x64.exe"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        source = Path(temporary) / "installer.exe"
        digest = hashlib.sha256()
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 AsidePatcher/0.1"})
        with urlopen(request, timeout=120) as response, source.open("xb") as output:
            resolved = response.url
            if urlsplit(resolved).scheme != "https" or urlsplit(resolved).hostname != "releases.aside.com":
                raise ValueError("Installer redirected outside official release host")
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        evidence = attest(source)
        trusted = _trusted(evidence)
        destination = root / "installers" / version / digest.hexdigest()
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"AsideInstaller-{version}-win-x64.exe"
        _preserve(source, target, digest.hexdigest())
        metadata = dict(
            requested_version=version,
            actual_browser_version=None,
            url=url,
            resolved_url=resolved,
            sha256=digest.hexdigest(),
            size=target.stat().st_size,
            acquired_at=datetime.now(timezone.utc).isoformat(),
            trusted_publisher=trusted,
            evidence=evidence,
            execution="never",
            payload_status="not inspected",
        )
        metadata_path = destination / "acquisition.json"
        if not metadata_path.exists():
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        verification = destination / ("verification-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        verification.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if not trusted:
            raise ValueError(f"Archived for inspection only: invalid signature/publisher at {target}")
        return target


def inspect_payload(path: Path, seven_zip: Path) -> dict:
    """Save a static archive listing, never run the executable."""
    listing = subprocess.run([str(seven_zip), "l", "-slt", str(path)], capture_output=True, text=True)
    evidence = path.parent / "archive-listing.txt"
    if not evidence.exists():
        evidence.write_text(listing.stdout + listing.stderr, encoding="utf-8")
    return dict(
        file=str(path),
        archive_listing_exit=listing.returncode,
        contains_browser_dll="Aside.dll" in listing.stdout,
        updater_payload="updater.7z" in listing.stdout.lower(),
    )


def acquire_package(root: Path, version: str, seven_zip: Path) -> dict:
    """Archive and unpack the official CRX/mini-installer without execution."""
    if not re.fullmatch(r"1\.0\.\d+\.\d+", version):
        raise ValueError("Invalid browser version")
    url = f"https://releases.aside.com/dev-updater/windows/{version}/Aside-{version}-win-x64.crx3"
    work = root / "incoming" / version
    work.mkdir(parents=True, exist_ok=True)
    source = _download_package(work, url)
    digest = _digest(source)
    destination = root / "browsers" / version / digest
    destination.mkdir(parents=True, exist_ok=True)
    package = destination / f"Aside-{version}-win-x64.crx3"
    _preserve(source, package, digest)
    extracted = destination / "extracted"
    if not list(extracted.rglob("Aside.dll")):
        tree = _browser_tree(_zip_payload(source, work), work, seven_zip)
        if extracted.exists():
            raise ValueError("Partial extraction exists; inspect it before retrying")
        shutil.copytree(tree, extracted)
    dlls = list(extracted.rglob("Aside.dll"))
    if len(dlls) != 1:
        raise ValueError("Ambiguous extracted browser DLL")
    evidence = attest(dlls[0])
    actual = evidence["product_version"]
    dll_digest = _digest(dlls[0])
    verified = actual == version and _trusted(evidence)
    record = dict(
        url=url,
        requested_version=version,
        actual_browser_version=actual,
        acquired_at=datetime.now(timezone.utc).isoformat(),
        package_sha256=digest,
        dll_sha256=dll_digest,
        dll=str(dlls[0].relative_to(destination)),
        evidence=evidence,
        verified=verified,
        execution="never",
    )
    metadata = destination / "browser.json"
    if not metadata.exists():
        metadata.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if not verified:
        raise ValueError(f"Extracted version/signature not verified: {record}")
    return record


def _download_package(work: Path, url: str) -> Path:
    source = work / "browser.crx3"
    if source.exists():
        return source
    partial = source.with_suffix(".partial")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 AsidePatcher/0.1"})
    with urlopen(request, timeout=120) as response, partial.open("wb") as output:
        parts = urlsplit(response.url)
        if parts.scheme != "https" or parts.hostname != "releases.aside.com":
            raise ValueError("Unexpected package redirect")
        shutil.copyfileobj(response, output)
    partial.replace(source)
    return source


def _zip_payload(source: Path, work: Path) -> Path:
    target = work / "browser.zip"
    with source.open("rb") as stream, target.open("wb") as output:
        magic, version, header_size = struct.unpack("<4sII", stream.read(12))
        if magic != b"Cr24" or version != 3 or header_size > source.stat().st_size - 12:
            raise ValueError("Invalid CRX3 header")
        stream.seek(12 + header_size)
        shutil.copyfileobj(stream, output)
    return target


def _nested_archives(folder: Path) -> list[Path]:
    return [
        path
        for path in folder.rglob("*")
        if path.is_file() and (path.name == "mini_installer.exe" or path.suffix.lower() in (".7z", ".zip"))
    ]


def _browser_tree(source: Path, work: Path, seven_zip: Path) -> Path:
    layers = [source]
    for depth in range(5):
        following = []
        for layer in layers:
            folder = work / f"layer-{depth}-{layer.stem}"
            subprocess.run([str(seven_zip), "x", "-y", f"-o{folder}", str(layer)], check=True, stdout=subprocess.DEVNULL)
            if list(folder.rglob("Aside.dll")):
                return folder
            following.extend(_nested_archives(folder))
        layers = following
    raise ValueError(f"No browser DLL found; static layers retained at {work}")


def main() -> None:
    """Acquire requested releases and print evidence locations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--seven-zip", type=Path, default=Path(r"C:\Program Files\7-Zip\7z.exe"))
    parser.add_argument("--packages", action="store_true")
    parser.add_argument("versions", nargs="+")
    args = parser.parse_args()
    for version in args.versions:
        path = acquire(args.root, version)
        print(json.dumps(inspect_payload(path, args.seven_zip)), flush=True)
        if args.packages:
            print(json.dumps(acquire_package(args.root, version, args.seven_zip)), flush=True)


if __name__ == "__main__":
    main()
