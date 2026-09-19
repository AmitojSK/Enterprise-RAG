"""Generate the evaluation fixture corpus (needs the dev dependency ``fpdf2``).

One page per topic, with content we author, so the golden set's expected page
numbers are ground truth rather than guesses. Run from the repo root:

    python -m evals.build_corpus

The generated PDF is committed, so ``run_eval`` works without fpdf2; this script
only needs running to regenerate or extend the corpus.
"""

from pathlib import Path

from fpdf import FPDF

# (title, body) per page. The page number is the index, starting at 1.
PAGES = [
    ("Rightsizing", "Rightsizing means matching instance types to real workload demand rather than over-provisioning, which removes the waste of paying for idle capacity."),
    ("Reserved Instances", "Reserved instances require a one or three year commitment in exchange for a large discount over on-demand pricing, and suit steady, predictable baseline workloads."),
    ("Spot Instances", "Spot instances use spare capacity at up to ninety percent discount but can be reclaimed with two minutes notice, so they fit fault-tolerant, interruptible batch jobs."),
    ("Storage Tiering", "Storage tiering automatically moves infrequently accessed data to cheaper cold tiers, reducing storage cost while keeping the data retrievable when it is needed."),
    ("Tagging and Accountability", "A consistent resource tagging policy attributes every cost to a team or project, which is the foundation of accountability and chargeback."),
    ("Autoscaling", "Autoscaling adjusts capacity up and down with demand so you never pay for a permanently peak-sized fleet during quiet periods."),
    ("Savings Plans", "Savings plans are flexible commitment discounts that apply across instance families and regions for a predictable hourly spend."),
    ("Budgets and Alerts", "Budgets with threshold alerts catch runaway spend early, at the moment it crosses a limit, rather than at the end-of-month invoice."),
]


def build() -> Path:
    pdf = FPDF()
    for index, (title, body) in enumerate(PAGES, start=1):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 22)
        pdf.cell(0, 20, f"{index}. {title}")
        pdf.ln(24)
        pdf.set_font("Helvetica", "", 13)
        pdf.multi_cell(0, 9, body)
    out_dir = Path(__file__).resolve().parent / "corpus"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "cloud-cost-guide.pdf"
    pdf.output(str(out_path))
    return out_path


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes)")
