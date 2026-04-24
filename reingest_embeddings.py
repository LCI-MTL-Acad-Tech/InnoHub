"""
reingest_embeddings.py — re-parse CVs and cover letters from raw/ using
the Excel as the source of truth for filenames, then update student JSON
records with the real filenames and fresh embeddings.

Usage:
    ./bin/python reingest_embeddings.py
    ./bin/python reingest_embeddings.py --force   # re-embed even if embedding exists
"""
import sys
import argparse
from pathlib import Path
from datetime import date

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent))

from src.store       import list_ids, load_json, save_json
from src.embed       import embed_text, save_embedding
from src.parse       import parse_file
from src.bulk_import import (
    _read_tabular, _find_tabular, _get, _find_file,
    _COL_ID, _COL_CV, _COL_CL,
)

TODAY   = date.today().isoformat()
RAW_DIR = Path("raw")
CV_DIR  = RAW_DIR / "CV"
CL_DIR  = RAW_DIR / "CL"
EMB_DIR = Path("data/embeddings/students")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-embed even if embedding already exists.")
    args = parser.parse_args()

    EMB_DIR.mkdir(parents=True, exist_ok=True)

    # ── Read Excel to build sid -> (cv_url, cl_url) map ──────────────────────
    students_file = _find_tabular(RAW_DIR, "students")
    if not students_file:
        print("ERROR: no students.xlsx / students.csv found in raw/")
        sys.exit(1)

    rows = _read_tabular(students_file)
    print(f"Read {len(rows)} rows from {students_file.name}")

    excel_map = {}
    for row in rows:
        sid      = _get(row, _COL_ID).strip()
        cv_fname = _get(row, _COL_CV).strip()
        cl_fname = _get(row, _COL_CL).strip()
        if sid:
            excel_map[sid] = (cv_fname, cl_fname)

    # ── Process each student ──────────────────────────────────────────────────
    ok = skip = fail = 0

    for sid in sorted(list_ids("students")):
        m   = load_json("students", sid)
        emb = m.get("embedding_file", "")

        if not args.force and emb and Path(emb).exists():
            skip += 1
            continue

        if sid not in excel_map:
            print(f"SKIP {sid}  {m.get('name', '')} — not found in Excel")
            fail += 1
            continue

        cv_fname, cl_fname = excel_map[sid]
        cv_path = _find_file(CV_DIR, cv_fname) if cv_fname else None
        cl_path = _find_file(CL_DIR, cl_fname) if cl_fname else None

        if not cv_path:
            print(f"SKIP {sid}  {m.get('name', '')} — CV not found")
            print(f"     url: {cv_fname[:80]}")
            fail += 1
            continue

        # Parse files
        texts = []
        for p in filter(None, [cv_path, cl_path]):
            try:
                t = parse_file(p)
                if t.strip():
                    texts.append(t)
            except Exception as e:
                print(f"  parse error {p.name}: {e}")

        if not texts:
            print(f"FAIL {sid}  {m.get('name', '')} — could not parse any file")
            fail += 1
            continue

        # Embed and save
        try:
            vec      = embed_text("\n\n".join(texts))
            emb_path = EMB_DIR / f"{sid}.npy"
            save_embedding(vec, emb_path)

            # Replace document records with real filenames
            docs = []
            if cv_path:
                docs.append({
                    "type":         "cv",
                    "filename":     cv_path.name,
                    "ingested_date": TODAY,
                })
            if cl_path:
                docs.append({
                    "type":         "cover_letter",
                    "filename":     cl_path.name,
                    "ingested_date": TODAY,
                })

            m["documents"]      = docs
            m["embedding_file"] = str(emb_path)
            save_json("students", sid, m)

            print(f"OK   {sid}  {m.get('name', '')}")
            print(f"     cv = {cv_path.name}")
            if cl_path:
                print(f"     cl = {cl_path.name}")
            ok += 1

        except Exception as e:
            print(f"FAIL {sid}  {m.get('name', '')} — {e}")
            fail += 1

    print(f"\nDone: {ok} embedded, {skip} skipped (already OK), {fail} failed")


if __name__ == "__main__":
    main()
