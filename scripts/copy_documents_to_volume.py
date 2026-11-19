"""
Download the policy documents from their old Vercel Blob links (`policy_repo_documents.blob_url`) into the local
folder `files-to-transfer/`, filed the way the app expects them in the repository:

    files-to-transfer/
        _manifest.json      {document id: path}  – the app finds every transferred document through this file
        _manifest.csv       the same, with country / year / tier / language / record name – for people
        _HOW_TO_UPLOAD.txt
        <ISO3>_<Country>/<National|Subnational_<entity>|Supranational>/<Year>/<Tier>/<ENG|ORI_<language>>/
            <id8>_<file name>

Then upload the CONTENTS of files-to-transfer/ (including _manifest.json) by hand into
    /Volumes/prd_mega/sgpbpi163/vgpbpi163/PIMPAM-AI/policy-profiles/upload-files/documents/pim-repositary/
(constants.FILES_ROOT + constants.DOCUMENTS_DIR).  The documents table is only read, never changed.
Resumable: files already downloaded (same size) are skipped, so it can be stopped and run again; the manifest
always lists every file present in the folder.

    python scripts/copy_documents_to_volume.py --dry-run      # where each file would go, nothing is downloaded
    python scripts/copy_documents_to_volume.py --limit 20     # only the first 20 documents (a trial run)
    python scripts/copy_documents_to_volume.py                # everything (≈ 5.7 GB)
    python scripts/copy_documents_to_volume.py --workers 8    # more parallel downloads (default 4)

After the upload, build the search index:   python scripts/reindex.py --lang all
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import constants as C  # noqa: E402
from utils import db, queries, storage  # noqa: E402
from utils.db import table  # noqa: E402

TIMEOUT = 120
_print_lock = threading.Lock()


def say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def load_documents() -> list[dict]:
    rows = db.query(
        f"SELECT d.id, d.record_id, d.lang_type, d.lang_label, d.file_name, d.file_size, d.blob_url, "
        f"r.country, r.year, r.policy_guidance_tier, r.jurisdiction_level, r.jurisdiction_name, r.name_eng, "
        f"r.record_status FROM {table('documents')} d JOIN {table('policy_records')} r ON r.id = d.record_id "
        f"ORDER BY r.country, r.year, r.policy_guidance_tier, d.lang_type")
    names = queries.country_name_map()
    for r in rows:
        r["country_name"] = names.get(r["country"], r["country"])
        r["key"] = storage.repository_key(r, r["country_name"], r["id"], r["lang_type"], r["lang_label"],
                                          r["file_name"])
    return rows


def copy_one(i: int, total: int, doc: dict, out: Path, dry_run: bool) -> str:
    """→ "copied" | "exists" | "skipped" | "failed"."""
    dest = out / doc["key"]
    label = f"[{i}/{total}] {doc['key']}"
    expected = db.to_int(doc.get("file_size"))
    if dest.exists() and dest.stat().st_size > 0 and (not expected or dest.stat().st_size == expected):
        return "exists"
    url = (doc.get("blob_url") or "").strip()
    if urlparse(url).scheme != "https":
        say(f"{label}: SKIPPED – no https source link in blob_url ({url[:60] or 'empty'})")
        return "skipped"
    if dry_run:
        say(f"{label}: would download")
        return "copied"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.content
        if len(data) > C.MAX_UPLOAD_BYTES:
            raise ValueError(f"file is larger than {C.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")          # never leave a half-written file under the real name
        tmp.write_bytes(data)
        tmp.replace(dest)
        note = f"  (size differs from file_size {expected})" if expected and expected != len(data) else ""
        say(f"{label}: {len(data) / 1e6:.1f} MB{note}")
        return "copied"
    except Exception as exc:  # noqa: BLE001 - report and carry on with the next file
        say(f"{label}: FAILED – {type(exc).__name__}: {exc}")
        return "failed"


def write_manifest(out: Path, docs: list[dict]) -> int:
    """_manifest.json + _manifest.csv for every document whose file is in the folder.  Returns the count."""
    present = [d for d in docs if (out / d["key"]).is_file()]
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "upload_to": storage.location("", C.DOCUMENTS_DIR) if C.DB_BACKEND != "sqlite" else C.DOCUMENTS_DIR,
               "documents": {d["id"]: d["key"] for d in present}}
    (out / C.DOCUMENT_MANIFEST).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    with open(out / "_manifest.csv", "w", newline="", encoding="utf-8-sig") as fh:   # BOM: opens cleanly in Excel
        w = csv.writer(fh)
        w.writerow(["document_id", "record_id", "country", "country_name", "jurisdiction_level", "jurisdiction_name",
                    "year", "policy_guidance_tier", "lang_type", "language", "record_name", "record_status",
                    "file_name", "size_bytes", "path"])
        for d in present:
            w.writerow([d["id"], d["record_id"], d["country"], d["country_name"], d["jurisdiction_level"],
                        d["jurisdiction_name"], d["year"], d["policy_guidance_tier"], d["lang_type"], d["lang_label"],
                        d["name_eng"], d["record_status"], d["file_name"], (out / d["key"]).stat().st_size, d["key"]])
    (out / "_HOW_TO_UPLOAD.txt").write_text(
        "Upload the CONTENTS of this folder - every country folder AND _manifest.json - into\n\n"
        f"    {C.FILES_ROOT}/{C.DOCUMENTS_DIR}/\n\n"
        "keeping the folder structure.  The app finds each document through _manifest.json, so upload it\n"
        "(again) after every run of scripts/copy_documents_to_volume.py.  _manifest.csv is a readable list of the\n"
        "same files.  Then build the search index:  python scripts/reindex.py --lang all\n", encoding="utf-8")
    return len(present)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show where each file would go, download nothing")
    ap.add_argument("--limit", type=int, default=0, help="only the first N documents (0 = all)")
    ap.add_argument("--workers", type=int, default=4, help="parallel downloads (default 4)")
    ap.add_argument("--out", type=Path, default=C.TRANSFER_DIR, help=f"target folder (default {C.TRANSFER_DIR})")
    args = ap.parse_args()

    docs = load_documents()
    out = args.out.resolve()
    say(f"{len(docs)} documents · local folder {out}")
    todo = docs[:args.limit] if args.limit else docs
    counts = {"copied": 0, "exists": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for r in pool.map(lambda a: copy_one(a[0], len(todo), a[1], out, args.dry_run), enumerate(todo, 1)):
            counts[r] += 1
    verb = "to download" if args.dry_run else "downloaded"
    say(f"\nDone: {counts['copied']} {verb}, {counts['exists']} already in the folder, "
        f"{counts['skipped']} without a source link, {counts['failed']} failed.")
    if args.dry_run:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    n = write_manifest(out, docs)
    say(f"Manifest: {n} of {len(docs)} documents are in {out}  (_manifest.json, _manifest.csv)")
    if counts["failed"]:
        say("Run the script again to retry the failed files (downloaded ones are skipped).")
    say(f"Next: upload the contents of {out.name}/ into {C.FILES_ROOT}/{C.DOCUMENTS_DIR}/ "
        "(see _HOW_TO_UPLOAD.txt), then: python scripts/reindex.py --lang all")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
