"""Run the takeoff app: python -m adel_takeoff"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="adel_takeoff")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("ADEL_DATA_DIR", "data"),
        help="Directory for the job database, PDFs, and review files",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("serve", help="Run the local review site")

    takeoff = commands.add_parser("takeoff", help="Read a PDF and store a job")
    takeoff.add_argument("pdf")

    review = commands.add_parser("review", help="Record a review decision")
    review.add_argument("job_id")
    review.add_argument("--item", default="")
    review.add_argument(
        "--action",
        choices=["accept", "reject", "correct", "existing_to_remain", "nic", "by_others"],
        default="",
    )
    review.add_argument("--label", default="")
    review.add_argument("--panel", default="")
    review.add_argument("--circuit", default="")
    review.add_argument("--page", type=int)
    review.add_argument("--x", type=float)
    review.add_argument("--y", type=float)

    export_cmd = commands.add_parser("export", help="Write the Excel report and marked-up PDF")
    export_cmd.add_argument("job_id")
    export_cmd.add_argument("--out", default=".")

    benchmark = commands.add_parser(
        "benchmark",
        help="Score a plan set against its ground truth and write a scorecard",
    )
    benchmark.add_argument("set_dir")
    benchmark.add_argument("--out", default="", help="Scorecard folder. Default is <set-dir>/scorecard")

    truth = commands.add_parser(
        "truth",
        help="Export accepted, corrected, and added review decisions as benchmark truth",
    )
    truth.add_argument("job_id")
    truth.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    if args.command == "takeoff":
        _takeoff(data_dir, Path(args.pdf))
        return
    if args.command == "review":
        _review(data_dir, args)
        return
    if args.command == "export":
        _export(data_dir, args.job_id, Path(args.out))
        return
    if args.command == "benchmark":
        _benchmark(Path(args.set_dir), Path(args.out) if args.out else None)
        return
    if args.command == "truth":
        _truth(data_dir, args.job_id, Path(args.out))
        return
    _serve(data_dir, args.host, args.port)


def _serve(data_dir: Path, host: str, port: int) -> None:
    import uvicorn

    from adel_takeoff.main import create_app

    uvicorn.run(create_app(data_dir), host=host, port=port, reload=False)


def _takeoff(data_dir: Path, pdf_path: Path) -> None:
    from adel_takeoff.main import ingest_pdf

    payload = pdf_path.read_bytes()
    job_id = ingest_pdf(data_dir, pdf_path.name, payload)
    print(job_id)


def _review(data_dir: Path, args) -> None:
    from adel_takeoff.db import session
    from adel_takeoff.main import db_path_for, load_job
    from adel_takeoff.review_store import ReviewError, add_missed_item, apply_decision

    loaded = load_job(data_dir, args.job_id)
    if loaded is None:
        raise SystemExit(f"Job {args.job_id} was not found")
    try:
        with session(db_path_for(data_dir)) as conn:
            if args.page is not None:
                if args.x is None or args.y is None:
                    raise ReviewError(400, "A missed item needs --page, --x, and --y.")
                pdf_bytes = Path(loaded["job"]["pdf_path"]).read_bytes()
                item_id = add_missed_item(
                    conn,
                    data_dir,
                    args.job_id,
                    pdf_bytes=pdf_bytes,
                    page_index=args.page,
                    x=args.x,
                    y=args.y,
                    label=args.label,
                    panel=args.panel,
                    circuit=args.circuit,
                )
                print(item_id)
                return
            if not args.item or not args.action:
                raise ReviewError(400, "A review needs --item and --action, or --page with a location.")
            apply_decision(
                conn,
                data_dir,
                args.job_id,
                args.item,
                args.action,
                args.label,
                args.panel,
                args.circuit,
            )
    except ReviewError as exc:
        raise SystemExit(str(exc)) from exc
    print(args.item)


def _export(data_dir: Path, job_id: str, out_dir: Path) -> None:
    from adel_takeoff.export_excel import build_workbook
    from adel_takeoff.export_pdf import build_markup_pdf
    from adel_takeoff.main import load_job

    loaded = load_job(data_dir, job_id)
    if loaded is None:
        raise SystemExit(f"Job {job_id} was not found")
    out_dir.mkdir(parents=True, exist_ok=True)
    excel_path = out_dir / f"{job_id}-quantities.xlsx"
    pdf_path = out_dir / f"{job_id}-markup.pdf"
    excel_path.write_bytes(
        build_workbook(
            loaded["job"],
            loaded["items"],
            loaded["allowances"],
            loaded["rfis"],
            loaded["claims"],
        )
    )
    pdf_path.write_bytes(
        build_markup_pdf(
            Path(loaded["job"]["pdf_path"]).read_bytes(),
            loaded["job"],
            loaded["items"],
            loaded["allowances"],
            loaded["rfis"],
            loaded["claims"],
        )
    )
    print(excel_path)
    print(pdf_path)


def _benchmark(set_dir: Path, out_dir: Path | None) -> None:
    from adel_takeoff.benchmark import BenchmarkError, load_thresholds, score_set, write_scorecard

    try:
        report = score_set(set_dir)
        thresholds = load_thresholds(set_dir)
    except BenchmarkError as exc:
        raise SystemExit(str(exc)) from exc
    markdown_path, excel_path = write_scorecard(report, out_dir or (set_dir / "scorecard"))
    print(markdown_path)
    print(excel_path)
    print(f"precision {report.precision:.3f}")
    print(f"recall {report.recall:.3f}")
    print(f"count error {report.max_abs_count_error}")
    print(f"schedule recall {report.schedule_recall:.3f}")
    if thresholds and not report.passes(thresholds):
        raise SystemExit(1)


def _truth(data_dir: Path, job_id: str, out_dir: Path) -> None:
    import shutil

    from adel_takeoff.benchmark import documents_from_rows, write_truth_dir
    from adel_takeoff.main import load_job
    from adel_takeoff.present import build_review

    loaded = load_job(data_dir, job_id)
    if loaded is None:
        raise SystemExit(f"Job {job_id} was not found")
    review = build_review(
        loaded["job"],
        loaded["items"],
        loaded["allowances"],
        loaded["rfis"],
        loaded["claims"],
    )
    documents = documents_from_rows(review["rows"])
    if not documents:
        raise SystemExit("This job has no accepted, corrected, or added items to export.")
    write_truth_dir(out_dir, documents)
    drawing = out_dir / "drawing.pdf"
    if not drawing.exists():
        shutil.copyfile(loaded["job"]["pdf_path"], drawing)
    print(out_dir)


if __name__ == "__main__":
    main()
