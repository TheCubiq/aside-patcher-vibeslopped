"""Resolve Aside's official Windows redirect without trusting changelog versions."""

import json
import hashlib
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


def package_identity(url: str) -> dict:
    """Identify actual browser bytes rather than a version label or cached ETag."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 AsidePatcher/0.1"})
    digest, size = hashlib.sha256(), 0
    with urlopen(request, timeout=60) as response:
        parts = urlsplit(response.url)
        if parts.scheme != "https" or parts.hostname != "releases.aside.com":
            raise ValueError("Browser package left the official HTTPS release host")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 512 * 1024 * 1024:
                raise ValueError("Browser package exceeds the acquisition limit")
            digest.update(chunk)
    return {"package_url": url, "package_sha256": digest.hexdigest(), "package_size": size}


def main() -> None:
    """Emit a bounded release descriptor and optional GitHub step outputs."""
    request = Request("https://aside.com/api/download/windows", method="HEAD", headers={"User-Agent": "Mozilla/5.0 AsidePatcher/0.1"})
    with urlopen(request, timeout=30) as response:
        url = response.url
        parts = urlsplit(url)
        match = re.fullmatch(r"/dev-updater/windows/(1\.0\.\d+\.\d+)/AsideInstaller-\1-win-x64\.exe", parts.path)
        if parts.scheme != "https" or parts.hostname != "releases.aside.com" or not match:
            raise ValueError("Official download redirect format changed; review it before acquisition")
        descriptor = {
            "version": match[1],
            "installer_url": url,
            "installer_size": response.headers.get("Content-Length"),
            "etag": response.headers.get("ETag"),
        }
    descriptor.update(package_identity(f"https://releases.aside.com/dev-updater/windows/{match[1]}/Aside-{match[1]}-win-x64.crx3"))
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if output:
        output.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(descriptor))


if __name__ == "__main__":
    main()
