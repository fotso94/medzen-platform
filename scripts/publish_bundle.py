#!/usr/bin/env python3
"""Publish the code bundle to S3 with a provenance record the builder can check.

The builder fetches medzen_code.tgz and builds an image from it. Without a
record of which commit those bytes came from, the image's git-SHA tag is a
claim nobody can verify: the bundle could have been rebuilt from a dirty tree,
or from a different commit entirely, and the tag would look identical.

BUNDLE.json carries the full commit SHA and a per-file sha256, and
build_image.sh refuses to build unless the SHA it was told to tag with matches
the bundle's, and every extracted file hashes correctly. Same shape as the base
model cache: provenance is enforced, not annotated.

A dirty working tree is refused outright. `git archive HEAD` silently ignores
uncommitted changes, so a bundle built from one would be tagged with a commit
whose contents it does not contain.

    python scripts/publish_bundle.py            # publish
    python scripts/publish_bundle.py --verify   # check what is in S3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
PREFIX = "candidates/bootstrap"   # per-commit subpath: PREFIX/<full 40-char sha>/
PATHS = ["pipeline", "scripts", "registry", "schemas", "platform", "requirements.txt"]

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def cli():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the archive and print GIT_SHA/TAR_SHA256 WITHOUT "
                         "uploading. The archive is byte-reproducible, so the "
                         "hash printed here is the hash a later publish will "
                         "produce -- which lets an approval packet quote it "
                         "before anything is written to S3.")
    # There is deliberately no --allow-dirty. A bundle that does not correspond
    # to a commit cannot be verified by anything downstream, and an image tagged
    # with that commit would be a false record. Commit first.
    a = ap.parse_args()

    c = cli()
    head = git("rev-parse", "HEAD")            # always the full 40 chars
    assert len(head) == 40, f"expected a 40-char SHA, got {head!r}"

    if a.verify:
        key = f"{PREFIX}/{head}/BUNDLE.json"
        try:
            man = json.loads(c.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        except Exception as e:
            print(f"NOT PUBLISHED for {head[:7]} — no {key} ({type(e).__name__})")
            return 1
        print(f"published git_sha : {man['git_sha']}")
        print(f"local HEAD        : {head}")
        print(f"files             : {len(man['files'])}")
        if man["git_sha"] != head:
            print("MISMATCH — the published bundle is not this commit")
            return 1
        print(f"\nOK — s3://{BUCKET}/{PREFIX}/{head}/ matches local HEAD")
        return 0

    dirty = git("status", "--porcelain")
    if dirty:
        print("REFUSING: working tree is dirty. `git archive HEAD` ignores\n"
              "uncommitted changes, so the bundle would be tagged with a commit\n"
              "whose contents it does not contain. Commit first.\n")
        print(dirty)
        return 2

    sha = head
    print(f"git_sha {sha}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "src"
        src.mkdir()
        # git archive, not the working tree: the bundle is the commit by construction
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD", *PATHS],
                                 capture_output=True, check=True).stdout
        (tmp / "archive.tar").write_bytes(archive)
        with tarfile.open(tmp / "archive.tar") as t:
            # filter="data" is the safe extraction policy and the default from
            # Python 3.14; without it this warns now and breaks later.
            t.extractall(src, filter="data")

        files = {}
        for p in sorted(x for x in src.rglob("*") if x.is_file()):
            rel = p.relative_to(src).as_posix()
            data = p.read_bytes()
            files[rel] = {"sha256": sha256_bytes(data), "bytes": len(data)}
        print(f"  {len(files)} files")

        # REPRODUCIBLE archive: a given commit must always produce identical
        # bytes, so tar_sha256 can be recomputed and checked independently
        # rather than being an incidental value only this run knows. gzip
        # normally records the current time, and tar records mtimes and
        # ownership, all of which would change the hash on every publish.
        bundle = tmp / "medzen_code.tgz"

        def normalise(ti: tarfile.TarInfo) -> tarfile.TarInfo:
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mode = 0o755 if ti.mode & 0o100 else 0o644
            return ti

        import gzip
        with open(bundle, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as t:
                    for rel in files:          # already sorted
                        t.add(src / rel, arcname=rel, filter=normalise)

        manifest = {"git_sha": sha, "git_sha_short": sha[:7],
                    "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "paths": PATHS, "files": files}

        # Per-commit path so a bundle is immutable once published: a shared
        # "latest" key means a builder can fetch different bytes than the ones
        # verified a moment earlier, and two concurrent publishes race.
        base = f"{PREFIX}/{sha}"
        manifest["tar_sha256"] = sha256_bytes(bundle.read_bytes())
        if a.dry_run:
            print(f"\nDRY RUN — nothing uploaded")
            print(f"GIT_SHA={sha}")
            print(f"TAR_SHA256={manifest['tar_sha256']}")
            print(f"  would publish to s3://{BUCKET}/{base}/")
            return 0
        c.upload_file(str(bundle), BUCKET, f"{base}/medzen_code.tgz")
        print(f"  uploaded {base}/medzen_code.tgz ({bundle.stat().st_size} bytes)")
        # BUNDLE.json last: its presence means the pair is complete and matched
        c.put_object(Bucket=BUCKET, Key=f"{base}/BUNDLE.json",
                     Body=(json.dumps(manifest, indent=2) + "\n").encode())
        print(f"  uploaded {base}/BUNDLE.json ({len(files)} files)")

        tar_sha = manifest["tar_sha256"]

    # Printed here, from the machine that built the archive. Launchers must take
    # TAR_SHA256 from THIS output, never by reading BUNDLE.json back out of S3:
    # a hash fetched from the same place as the artifact verifies nothing.
    print(f"\npublished s3://{BUCKET}/{PREFIX}/{sha}/")
    print(f"GIT_SHA={sha}")
    print(f"TAR_SHA256={tar_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
