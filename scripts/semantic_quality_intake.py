"""Offline single-owner diagnostics or two-reviewer annotation for SDE.

No provider, database, network, SDK, or repo write operations.
A one-reviewer corpus is never certified as independent holdout evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

NAME = re.compile(r"[a-z][a-z0-9_]{2,79}\Z")
LABELS = {"material", "equivalent", "unsure"}
REASONS = {"obligation", "eligibility", "scope", "quantity", "time", "privacy", "other"}
RIGHTS = {"public_reviewed", "internal_authorized", "customer_authorized"}
MAX_BYTES = 5_000_000
MAX_CASES = 100


def die(message: str) -> None:
    raise ValueError(message)


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def safe_json_file(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        die("Input file missing.")
    if path.stat().st_size > MAX_BYTES:
        die("Input exceeds 5 MB limit.")
    raw = path.read_bytes()
    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            if key in output:
                die("Input has a repeated JSON field.")
            output[key] = value
        return output
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError):
        die("Invalid JSON input.")
    if not isinstance(value, dict):
        die("Input must be a JSON object.")
    return value, hash_bytes(raw)


def identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(NAME.fullmatch(value))


def validate_intake(doc: dict[str, Any]) -> list[dict[str, Any]]:
    if set(doc) != {"schema_version", "dataset_id", "source_type", "annotation_protocol_id", "cases"}:
        die("Invalid intake schema.")
    if type(doc["schema_version"]) is not int or doc["schema_version"] != 1:
        die("Intake schema version must be 1.")
    if not identifier(doc["dataset_id"]) or not identifier(doc["annotation_protocol_id"]):
        die("Dataset or annotation protocol identifier invalid.")
    if doc["source_type"] not in ("public", "internal", "customer_authorized"):
        die("Invalid source_type.")
    rows = doc["cases"]
    if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_CASES:
        die("Intake must contain 2 to 100 cases.")
    seen_ids, seen_pairs = set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "case_id", "domain", "change_category", "rights_basis", "source_reference", "old_text", "new_text"
        }:
            die("A case has invalid or missing fields.")
        if not all(identifier(row[key]) for key in ("case_id", "domain", "change_category")):
            die("A case has invalid anonymous identifiers.")
        if not isinstance(row["rights_basis"], str) or row["rights_basis"] not in RIGHTS:
            die("A case lacks an accepted data rights declaration.")
        if not isinstance(row["source_reference"], str) or not row["source_reference"].strip():
            die("A case lacks a source provenance reference.")
        a, b = row["old_text"], row["new_text"]
        if any(not isinstance(t, str) or not t.strip() or len(t) > 12_000 for t in (a,b)) or a == b:
            die("A case has invalid document versions.")
        key = hash_bytes(json.dumps(sorted((a, b)), ensure_ascii=False).encode("utf-8"))
        if row["case_id"] in seen_ids or key in seen_pairs:
            die("Intake includes a duplicate case or text pair.")
        seen_ids.add(row["case_id"])
        seen_pairs.add(key)
    return rows


def repo_root() -> Path | None:
    try:
        call = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, check=False)
        if call.returncode == 0 and call.stdout.strip():
            return Path(call.stdout.strip()).resolve()
    except (FileNotFoundError, OSError):
        pass
    return None


def require_external(path: Path) -> None:
    root = repo_root()
    if root is not None and (path.resolve() == root or root in path.resolve().parents):
        die("Private corpus, annotations and outputs must stay outside the Git repository.")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path, expected: list[str]) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        die("Reviewer or adjudication CSV is absent/oversized.")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != expected:
                die("Reviewer CSV columns do not match the prepared template.")
            rows = list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in rows):
            die("Malformed reviewer CSV rows.")
        return rows
    except (UnicodeError, csv.Error):
        die("Could not read reviewer CSV.")


def prepare(intake: Path, output: Path, *, reviewers: int = 2) -> None:
    if type(reviewers) is not int or reviewers not in (1, 2):
        die("Only one- or two-reviewer protocols are supported.")
    require_external(intake)
    require_external(output)
    doc, digest = safe_json_file(intake)
    cases = validate_intake(doc)
    output.mkdir(parents=True, exist_ok=False)
    # Documents are HTML-escaped; editable CSVs contain case IDs only, not text.
    for reviewer in (("a",) if reviewers == 1 else ("a", "b")):
        import random
        items = [dict(row) for row in cases]
        random.Random(int(hash_bytes((digest + reviewer).encode()), 16)).shuffle(items)
        write_csv(output / f"reviewer_{reviewer}.csv", ["case_id", "label"],
                  [{"case_id": row["case_id"], "label": ""} for row in items])
        body = []
        for row in items:
            body.append(
                "<section><h2>" + html.escape(row["case_id"]) +
                " <small>(" + html.escape(row["domain"]) + ")</small></h2>" +
                "<h3>Previous revision</h3><pre>" + html.escape(row["old_text"]) + "</pre>" +
                "<h3>Current revision</h3><pre>" + html.escape(row["new_text"]) + "</pre></section>"
            )
        instruction = (
            "This is a ONE-person diagnostic, NOT independently adjudicated ground truth. "
            "Mark material, equivalent, or unsure; unsure cases will be excluded and counted."
            if reviewers == 1 else
            "Each reviewer labels independently. Do not compare notes before both submit. "
            "Mark material, equivalent, or unsure in the CSV."
        )
        packet = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
                  "<title>Private blind semantic review</title>"
                  "<style>body{max-width:60rem;margin:2rem auto;font:16px/1.5 system-ui;padding:1rem}"
                  "section{border-top:2px solid #bbb;padding:1rem 0}pre{white-space:pre-wrap;word-break:break-word}"
                  "h2 small{font-size:.7em;color:#555}</style></head><body>"
                  "<h1>Blind document change review</h1><p>"
                  + html.escape(instruction) + "</p>"
                  + "".join(body) + "</body></html>")
        (output / f"reviewer_{reviewer}.html").write_text(packet, encoding="utf-8")
    manifest = {
        "schema_version": 1, "dataset_id": doc["dataset_id"], "intake_sha256": digest,
        "case_count": len(cases), "annotation_protocol_id": doc["annotation_protocol_id"],
        "annotation_independence_verified": False,
    }
    if reviewers == 1:
        manifest["reviewer_count"] = 1
    (output / "manifest.json").write_bytes(json_bytes(manifest))
    if reviewers == 2:
        (output / "adjudication.csv").write_text(
            "case_id,final_label,reason_code\r\n", encoding="utf-8"
        )
    print(f"PREPARED: {len(cases)} private cases; {reviewers} reviewer(s); no provider requests.")
    if reviewers == 1:
        print("SOLO DIAGNOSTIC ONLY: not independent validation or a production accuracy claim.")
    else:
        print("Two reviewers must label independently before comparing decisions.")


def checked_review(path: Path, cases: list[dict[str, Any]]) -> dict[str, str]:
    rows = read_csv(path, ["case_id", "label"])
    known = {case["case_id"]: case for case in cases}
    if len(rows) != len(known):
        die("Reviewer case count differs from frozen intake.")
    decisions: dict[str, str] = {}
    for row in rows:
        key = row["case_id"]
        if key not in known or key in decisions:
            die("Reviewer CSV has a missing or duplicate case ID.")
        label = row["label"].strip().lower()
        if label not in LABELS:
            die("A reviewer label is missing or invalid; use material/equivalent/unsure.")
        decisions[key] = label
    return decisions


def finalize(intake: Path, workdir: Path, output: Path, *, attest_frozen: bool) -> None:
    for path in (intake, workdir, output):
        require_external(path)
    doc, digest = safe_json_file(intake)
    cases = validate_intake(doc)
    manifest, _ = safe_json_file(workdir / "manifest.json")
    required = {"schema_version", "dataset_id", "intake_sha256", "case_count",
                "annotation_protocol_id", "annotation_independence_verified"}
    if set(manifest) not in (required, required | {"reviewer_count"}):
        die("Invalid preparation manifest.")
    reviewers = manifest.get("reviewer_count", 2)
    if type(reviewers) is not int or reviewers not in (1, 2):
        die("Invalid reviewer count in manifest.")
    if (manifest["intake_sha256"] != digest or manifest["dataset_id"] != doc["dataset_id"]
            or manifest["case_count"] != len(cases) or
            manifest["annotation_protocol_id"] != doc["annotation_protocol_id"] or
            manifest["annotation_independence_verified"] is not False):
        die("Private intake content or review manifest changed after preparation.")
    if output.exists() or output.with_name(output.stem + "_receipt.json").exists():
        die("Output or receipt already exists; refusing to overwrite.")
    a = checked_review(workdir / "reviewer_a.csv", cases)
    reviewer_b_hash: str | None = None
    adjudication_hash: str | None = None
    excluded_unsure = 0
    adjudication_count = 0
    if reviewers == 1:
        if attest_frozen:
            die("Single-reviewer diagnostics cannot attest an independent holdout.")
        # Never force ambiguous claims into binary ground truth. Report exclusions.
        excluded_unsure = sum(label == "unsure" for label in a.values())
        decided = {key: label for key, label in a.items() if label != "unsure"}
        selected = [case for case in cases if case["case_id"] in decided]
    else:
        b = checked_review(workdir / "reviewer_b.csv", cases)
        requires_adjudication = {case["case_id"] for case in cases
                                  if a[case["case_id"]] != b[case["case_id"]]
                                  or a[case["case_id"]] == "unsure"}
        judged = read_csv(workdir / "adjudication.csv", ["case_id", "final_label", "reason_code"])
        final: dict[str, str] = {}
        for row in judged:
            case_id = row["case_id"]
            if case_id not in requires_adjudication or case_id in final:
                die("Unexpected or duplicated adjudication case.")
            if row["final_label"] not in LABELS - {"unsure"} or row["reason_code"] not in REASONS:
                die("Adjudication must have a binary final label and reason code.")
            final[case_id] = row["final_label"]
        if set(final) != requires_adjudication:
            print(f"NOT READY: {len(requires_adjudication - set(final))} cases require adjudication; case IDs:")
            for case_id in sorted(requires_adjudication - set(final)):
                print(case_id)
            die("All disagreements/unsure labels need explicit adjudication.")
        decided = {case["case_id"]: final.get(case["case_id"], a[case["case_id"]])
                   for case in cases}
        selected = cases
        reviewer_b_hash = hash_bytes((workdir / "reviewer_b.csv").read_bytes())
        adjudication_hash = hash_bytes((workdir / "adjudication.csv").read_bytes())
        adjudication_count = len(requires_adjudication)
    material = sum(label == "material" for label in decided.values())
    if not (0 < material < len(selected)) or len(selected) < 2:
        die("At least one certain material and one equivalent label required.")
    corpus = {
        "schema_version": 1,
        "dataset_id": doc["dataset_id"],
        "provenance": {
            "source_type": doc["source_type"],
            "annotation_protocol_id": doc["annotation_protocol_id"],
            "annotator_count": reviewers,
            "adjudicated": reviewers == 2,
            "held_out_before_model_selection": bool(attest_frozen) if reviewers == 2 else False,
        },
        "cases": [{"case_id": c["case_id"], "domain": c["domain"],
                   "expected_material": decided[c["case_id"]] == "material",
                   "old_text": c["old_text"], "new_text": c["new_text"]}
                  for c in selected],
    }
    body = json_bytes(corpus)
    receipt = {
        "schema_version": 1,
        "dataset_id": doc["dataset_id"],
        "intake_sha256": digest,
        "reviewer_a_sha256": hash_bytes((workdir / "reviewer_a.csv").read_bytes()),
        "reviewer_b_sha256": reviewer_b_hash,
        "adjudication_sha256": adjudication_hash,
        "corpus_sha256": hash_bytes(body),
        "reviewer_count": reviewers,
        "excluded_unsure_count": excluded_unsure,
        "adjudication_count": adjudication_count,
        "held_out_attested_by_operator": bool(attest_frozen) if reviewers == 2 else False,
        "independence_verification": "not_automatically_verifiable",
        "evaluation_scope": "single_owner_diagnostic_only" if reviewers == 1 else "reviewer_adjudicated",
    }
    # Refuse existing files before either write; cannot clobber prior evidence.
    with output.open("xb") as handle:
        handle.write(body)
    receipt_path = output.with_name(output.stem + "_receipt.json")
    try:
        with receipt_path.open("xb") as handle:
            handle.write(json_bytes(receipt))
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f"FINALIZED: {len(selected)}/{len(cases)} cases, {material} material, "
          f"{len(selected)-material} equivalent; excluded unsure={excluded_unsure}.")
    print("CORPUS SHA256:", hash_bytes(body))
    if reviewers == 1:
        print("STATUS: single-owner diagnostic; NOT a blinded independent holdout.")
    else:
        print("STATUS: annotation independence and held-out provenance remain human attestations.")
    print("RESULT: not an accuracy report. Zero provider requests.")
    if len(selected) < 50:
        print("LIMITATION: fewer than 50 cases; not sufficient for proposed pilot quality evaluation.")


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="sde-p12-test-") as folder:
        base = Path(folder)
        intake = base / "intake.json"
        original = {
            "schema_version": 1, "dataset_id": "smoke_private_sample",
            "source_type": "internal", "annotation_protocol_id": "blind_review_v1",
            "cases": [
                {"case_id": "case_one", "domain": "policy", "change_category": "deadline",
                 "rights_basis": "internal_authorized", "source_reference": "synthetic_local_only",
                 "old_text": "Policy is active for 30 days.", "new_text": "Policy is active for 3 days."},
                {"case_id": "case_two", "domain": "policy", "change_category": "paraphrase",
                 "rights_basis": "internal_authorized", "source_reference": "synthetic_local_only",
                 "old_text": "Requests require approval.", "new_text": "An approval is required for requests."},
            ]}
        intake.write_bytes(json_bytes(original))
        out = base / "review"
        prepare(intake, out)
        for reviewer in ("a", "b"):
            path = out / f"reviewer_{reviewer}.csv"
            records = read_csv(path, ["case_id", "label"])
            for rec in records:
                rec["label"] = "material" if rec["case_id"] == "case_one" else "equivalent"
            write_csv(path, ["case_id", "label"], records)
        result = base / "final.json"
        finalize(intake, out, result, attest_frozen=False)
        assert len(safe_json_file(result)[0]["cases"]) == 2
        print("PASS: Offline self-test; synthetic examples are NOT an independent holdout.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare", help="Create private blinded review templates")
    p.add_argument("--intake", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--reviewers", type=int, choices=(1, 2), default=2,
                   help="Number of human reviewers; one means diagnostic only")
    f = sub.add_parser("finalize", help="Finalize one-owner diagnostic or two-reviewer corpus")
    f.add_argument("--intake", required=True, type=Path)
    f.add_argument("--review-dir", required=True, type=Path)
    f.add_argument("--output", required=True, type=Path)
    f.add_argument("--attest-frozen", action="store_true",
                   help="Human attests corpus was held out before model selection")
    sub.add_parser("self-test", help="No network or files outside a temporary directory")
    args = parser.parse_args(argv)
    if args.action == "prepare":
        prepare(args.intake, args.output, reviewers=args.reviewers)
    elif args.action == "finalize":
        finalize(args.intake, args.review_dir, args.output,
                 attest_frozen=args.attest_frozen)
    else:
        self_test()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        # Messages are controlled, except unexpected OS exceptions; never emit filenames.
        print(f"STOP: {str(exc) if isinstance(exc, ValueError) else 'filesystem error'}", file=sys.stderr)
        raise SystemExit(2)
