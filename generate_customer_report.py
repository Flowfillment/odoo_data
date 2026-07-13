#!/usr/bin/env python3
"""Generate the per-customer sales report (phase 4) as PDF.

Reads the pipeline outputs (staging CSVs + output/report/) and produces a
2-3 page A4 report for one customer, in one or both variants:

    customer  - for the customer: contains no margin data whatsoever
                (excluded at data-assembly level, not hidden by styling)
    internal  - for sales: includes gross margin and margin %

Run a fresh full refresh first so the report reflects current data:
    .\\scripts\\run-full-refresh.ps1

Usage:
    python generate_customer_report.py --customer "Acme"
    python generate_customer_report.py --customer 501 --variant internal
    python generate_customer_report.py --customer "Acme" --variant both

PDF rendering uses a locally installed Chromium-family browser (Edge is
present on every Windows machine). If no browser is found, the HTML file
is still written and named so it can be printed to PDF manually.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys

from src.customer_report import compute_kpis, find_customer, render_html
from src.transform import TransformError, read_staging_csv

# Where a Chromium-family browser typically lives (first hit wins).
BROWSER_CANDIDATES = (
    os.environ.get("BROWSER_PATH", ""),
    shutil.which("msedge") or "",
    shutil.which("chrome") or "",
    shutil.which("chromium") or "",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/opt/pw-browsers/chromium",
)


def find_browser() -> str | None:
    for candidate in BROWSER_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def html_to_pdf(html_path: str, pdf_path: str) -> tuple[bool, str]:
    """Render HTML to PDF via a headless Chromium-family browser.

    Returns (ok, reason-when-not-ok). Retries with --no-sandbox, which
    Chromium requires when running as root (e.g. in a container); on a
    normal desktop the first attempt succeeds.
    """
    browser = find_browser()
    if browser is None:
        return False, "no Chromium/Edge/Chrome found"
    base_cmd = [
        browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={os.path.abspath(pdf_path)}",
        "file:///" + os.path.abspath(html_path).replace(os.sep, "/"),
    ]
    for extra in ([], ["--no-sandbox"]):
        result = subprocess.run(
            base_cmd[:1] + extra + base_cmd[1:], capture_output=True, timeout=120
        )
        if result.returncode == 0 and os.path.exists(pdf_path):
            return True, ""
    stderr = result.stderr.decode(errors="replace").strip().splitlines()
    return False, f"conversion failed ({stderr[-1] if stderr else 'unknown error'})"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the per-customer sales report (phase 4) as PDF."
    )
    parser.add_argument(
        "--customer",
        required=True,
        help="Customer: partner id, or a (partial) company name.",
    )
    parser.add_argument(
        "--variant",
        choices=("customer", "internal", "both"),
        default="both",
        help="Which variant(s) to generate (default: both).",
    )
    parser.add_argument(
        "--input-dir",
        default="output",
        help="Pipeline output directory (default: output/).",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join("output", "customer_reports"),
        help="Where the PDFs are written (default: output/customer_reports/).",
    )
    return parser.parse_args(argv)


def safe_filename(label: str) -> str:
    return "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in label).strip()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        partners = read_staging_csv(args.input_dir, "res_partner.csv")
        match = find_customer(partners, args.customer)
        if isinstance(match, list):
            if not match:
                print(f"No customer matches {args.customer!r}.", file=sys.stderr)
            else:
                print(f"{args.customer!r} is ambiguous; candidates:", file=sys.stderr)
                for label in match[:15]:
                    print(f"  - {label}", file=sys.stderr)
            return 2
        customer = match

        print(f"Customer: {customer.label} (partner id(s) {sorted(customer.ids)})")
        kpis = compute_kpis(args.input_dir, customer, today=dt.date.today())
        if not kpis.products and not kpis.open_invoices and not kpis.qty_ordered:
            print("  Note: no invoices or orders found for this customer in the "
                  "current data window.", file=sys.stderr)

        variants = ("customer", "internal") if args.variant == "both" else (args.variant,)
        os.makedirs(args.output_dir, exist_ok=True)
        stamp = kpis.generated_at.strftime("%Y-%m-%d")
        for variant in variants:
            internal = variant == "internal"
            html_text = render_html(kpis, internal=internal)
            base = f"{safe_filename(customer.label)} - {stamp} - {variant}"
            html_path = os.path.join(args.output_dir, base + ".html")
            pdf_path = os.path.join(args.output_dir, base + ".pdf")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(html_text)
            ok, reason = html_to_pdf(html_path, pdf_path)
            if ok:
                os.remove(html_path)
                print(f"  {variant}: {pdf_path}")
            else:
                print(f"  {variant}: {html_path}  "
                      f"({reason} - print this HTML to PDF manually)",
                      file=sys.stderr)
    except TransformError as exc:
        print(f"Report error: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
