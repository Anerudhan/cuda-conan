"""Regenerate the Linux section of conandata.yml for the 13.4.0 developer preview.

The preview is only published as distro packages: there is no tarball installer and no
redistrib_13.4.0.json, so the download URLs and their checksums cannot be derived from
the version alone. URLs embed an opaque batch id that changes when NVIDIA respins the
preview, and no checksums are published at all, so this scrapes the package listing and
hashes each deb locally.

Usage:
    python update_linux_conandata.py            # refresh URLs, keep known checksums
    python update_linux_conandata.py --hash     # also download and hash every package
"""

import argparse
import hashlib
import re
import urllib.request
from pathlib import Path

import yaml

VERSION = "13.4.0-preview"

# bookworm, jammy and noble serve byte-identical artifacts; jammy is used because it
# implies the lowest glibc floor (2.35) of the three.
SUITE = "jammy"
LISTING = "https://packages.nvidia.com/{suite}/{deb_arch}/prerelease/cuda/13.4.0/"

DEB_ARCH = {"x86_64": "amd64", "armv8": "arm64"}

# Mirrors the redist component set the 13.3 recipe consumes. The deb split is finer
# grained than redist, so headers and static libs need the matching -dev packages.
COMMON_PACKAGES = [
    "cccl-13-4",
    "cuda-crt-13-4",
    "cuda-cudart-13-4",
    "cuda-cudart-dev-13-4",
    "cuda-culibos-dev-13-4",
    "cuda-cuobjdump-13-4",
    "cuda-cupti-13-4",
    "cuda-cupti-dev-13-4",
    "cuda-cuxxfilt-13-4",
    "cuda-driver-dev-13-4",
    "cuda-nvcc-13-4",
    "cuda-nvdisasm-13-4",
    "cuda-nvml-dev-13-4",
    "cuda-nvprune-13-4",
    "cuda-nvrtc-13-4",
    "cuda-nvrtc-dev-13-4",
    "cuda-nvtx-13-4",
    "cuda-profiler-api-13-4",
    "cuda-sandbox-dev-13-4",
    "cuda-sanitizer-13-4",
    "cuda-tileiras-13-4",
    "libcublas-13-4",
    "libcublas-dev-13-4",
    "libcufft-13-4",
    "libcufft-dev-13-4",
    "libcufile-13-4",
    "libcufile-dev-13-4",
    "libcuobjclient-13-4",
    "libcuobjclient-dev-13-4",
    "libcurand-13-4",
    "libcurand-dev-13-4",
    "libcusolver-13-4",
    "libcusolver-dev-13-4",
    "libcusparse-13-4",
    "libcusparse-dev-13-4",
    "libnpp-13-4",
    "libnpp-dev-13-4",
    "libnvfatbin-13-4",
    "libnvfatbin-dev-13-4",
    "libnvjitlink-13-4",
    "libnvjitlink-dev-13-4",
    "libnvjpeg-13-4",
    "libnvjpeg-dev-13-4",
    "libnvptxcompiler-13-4",
    "libnvvm-13-4",
]

ARCH_PACKAGES = {
    "x86_64": ["cuda-opencl-13-4", "cuda-opencl-dev-13-4"],
    "armv8": ["libcudla-13-4", "libcudla-dev-13-4"],
}


def scrape_listing(deb_arch):
    url = LISTING.format(suite=SUITE, deb_arch=deb_arch)
    with urllib.request.urlopen(url) as response:
        html = response.read().decode("utf-8")

    hrefs = re.findall(r'href="([^"]*\.deb)"', html)
    by_name = {}
    for href in sorted(set(hrefs)):
        filename = href.rsplit("/", 1)[-1]
        by_name[filename.split("_", 1)[0]] = "https://packages.nvidia.com" + href
    return by_name


def sha256_of(url):
    digest = hashlib.sha256()
    read = 0
    with urllib.request.urlopen(url) as response:
        declared = int(response.headers["Content-Length"])
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            digest.update(chunk)
            read += len(chunk)

    # A stream that ends early still produces a perfectly plausible checksum, which would
    # then be baked into conandata.yml and only surface as a hash mismatch on CI
    if read != declared:
        raise SystemExit(f"{url}: read {read} of {declared} bytes")

    return digest.hexdigest()


def verify(linux, only_arch):
    mismatches = 0
    for arch, packages in linux.items():
        if only_arch and arch != only_arch:
            continue
        for name, data in packages.items():
            actual = sha256_of(data["url"])
            if actual == data["sha256"]:
                print(f"ok       {arch}/{name}")
            else:
                mismatches += 1
                print(f"MISMATCH {arch}/{name}\n  recorded {data['sha256']}\n  actual   {actual}")

    print(f"\n{mismatches} mismatch(es)")
    return 1 if mismatches else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash", action="store_true", help="download each package to compute its sha256")
    parser.add_argument("--verify", action="store_true", help="re-hash every package and report mismatches")
    parser.add_argument("--arch", choices=sorted(DEB_ARCH), help="restrict --verify to a single architecture")
    args = parser.parse_args()

    conandata_path = Path(__file__).parent / "conandata.yml"
    conandata = yaml.safe_load(conandata_path.read_text())
    previous = conandata["sources"][VERSION].get("Linux", {})

    if args.verify:
        raise SystemExit(verify(previous, args.arch))

    linux = {}
    for arch, deb_arch in DEB_ARCH.items():
        available = scrape_listing(deb_arch)
        wanted = COMMON_PACKAGES + ARCH_PACKAGES[arch]

        missing = [name for name in wanted if name not in available]
        if missing:
            raise SystemExit(f"{deb_arch}: packages not found in listing: {', '.join(missing)}")

        packages = {}
        for name in wanted:
            url = available[name]
            known = previous.get(arch, {}).get(name, {})
            sha256 = known.get("sha256") if known.get("url") == url else None
            if args.hash and not sha256:
                print(f"hashing {name} ...")
                sha256 = sha256_of(url)
            packages[name] = {"url": url, "sha256": sha256}
        linux[arch] = packages

    conandata["sources"][VERSION]["Linux"] = linux
    conandata_path.write_text(yaml.safe_dump(conandata, sort_keys=False, default_flow_style=False, width=200))


if __name__ == "__main__":
    main()
