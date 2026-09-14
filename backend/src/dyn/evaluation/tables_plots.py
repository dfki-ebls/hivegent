"""This script produces the final plots and graphs. The generation of the rows is LLM-GENERATED."""

from .run_eval import ApproachNumbers, _load_questions
import typer
from collections.abc import Iterable, Mapping
from pathlib import Path
from statistics import mean
from jinja2 import Template
import matplotlib.pyplot as plt
import numpy as np
from typing import Literal

app = typer.Typer()

PREFIXES = ("my", "rec", "sem")
"""Column prefixes used in the table templates, in table column order."""


def _fmt(values: Mapping[str, float | int], metrics: Iterable[str]) -> dict[str, str]:
    """Format each metric to two decimals, `--` if it is missing."""
    return {m: f"{values[m]:.2f}" if m in values else "--" for m in metrics}


def _metrics(names: Iterable[str]) -> tuple[str, ...]:
    """Cross the per-approach column prefixes with the metric `names`.
    E.g., gives my_cu, my_si, my_ip, rec_cu, rec_si, ..."""
    return tuple(f"{prefix}_{name}" for prefix in PREFIXES for name in names)


def _weighted_mean(
    rows: Iterable[Mapping[str, float]],
    weights: Iterable[float],
    metrics: Iterable[str],
) -> dict[str, float]:
    """Mean of each metric over `rows`, weighted by `weights` (one per row).

    Falls back to an unweighted mean if the weights sum to zero.
    """
    rows, weights, metrics = list(rows), list(weights), tuple(metrics)
    if not rows:
        return {}
    total = sum(weights)
    if total == 0:
        return {m: mean(r[m] for r in rows) for m in metrics}
    return {
        m: sum(w * r[m] for w, r in zip(weights, rows)) / total for m in metrics
    }


@app.command()
def make_tables(
    my: Path,
    rec_char: Path,
    sem: Path,
    questions_dir: Path | None = typer.Option(
        None,
        help="Folder with one <difficulty> subfolder of questions each; supplies the "
        "per-difficulty question counts used to weight the retriever/generator "
        "overall rows. Without it those rows fall back to an unweighted mean.",
    ),
):
    """Render the result tables from the <approach>.json files written by run_eval."""
    approaches = {
        prefix: ApproachNumbers.model_validate_json(path.read_bytes())
        for prefix, path in zip(PREFIXES, (my, rec_char, sem))
    }  # builds dict {"my": Approchnumbers for my, "rec": ...}

    # chunk quality
    template = Template(r"""
\begin{table}[htbp]
\centering
\begin{tabular}{lrrrrrrrrr}
\toprule
Dataset & \multicolumn{3}{c}{Concept Unity\,$\uparrow$} & \multicolumn{3}{c}{Sem.\,Independence\,$\uparrow$} & \multicolumn{3}{c}{Info Preservation\,$\uparrow$} \\
\cmidrule(lr){2-4} \cmidrule(lr){5-7} \cmidrule(lr){8-10}
 & Mine & Rec.\,Char & Semantic & Mine & Rec.\,Char & Semantic & Mine & Rec.\,Char & Semantic \\
\midrule
{% for item in items %}
{{ item.ds_name }} & {{ item.my_cu }} & {{ item.rec_cu }} & {{ item.sem_cu }} & {{ item.my_si }} & {{ item.rec_si }} & {{ item.sem_si }} & {{ item.my_ip }} & {{item.rec_ip}} & {{ item.sem_ip }} \\
{% endfor %}
\midrule
\textit{Overall} & {{ overall.my_cu }} & {{ overall.rec_cu }} & {{ overall.sem_cu }} & {{ overall.my_si }} & {{ overall.rec_si }} & {{ overall.sem_si }} & {{ overall.my_ip }} & {{overall.rec_ip}} & {{ overall.sem_ip }} \\
\bottomrule
\end{tabular}
\caption[Results: Chunk Quality per Dataset]{Chunk quality metrics across seven datasets.}
\label{tab:eval-chunk-qual}
\end{table}                        
""")

    metrics = _metrics(("cu", "si", "ip"))
    ds_names: list[str] = []
    rows: list[dict[str, float]] = []
    for ds_name in approaches["my"].chunk_quality:
        ds_names.append(ds_name)
        row: dict[str, float] = {}
        for prefix, numbers in approaches.items():
            q = numbers.chunk_quality[ds_name]
            row[f"{prefix}_cu"] = q.concept_unity
            row[f"{prefix}_si"] = q.semantic_independence
            row[f"{prefix}_ip"] = q.info_preservation
        rows.append(row)

    # unweighted mean over datasets, one value per column
    overall = {m: mean(r[m] for r in rows) for m in metrics} if rows else {}
    items = [
        {"ds_name": name, **_fmt(row, metrics)} for name, row in zip(ds_names, rows)
    ]
    print(template.render(items=items, overall=_fmt(overall, metrics)))

    # timing
    template = Template(r"""
\begin{table}[htbp]
\centering
\small\begin{tabular}{lccc}
\toprule
Chunker & Chunking(s) & Indexing (s) & Total(s)\\
\midrule
{% for item in items %}
{{ item.name }} & {{ item.chunk_time }} & {{ item.index_time }} & {{ item.total }} \\
{% endfor %}
\bottomrule
\end{tabular}
\caption[Results: Processing Times]{Processing times of the entire offline phase, comprising chunking and indexing. Information extraction is not included.}
\label{tab:processing-times}
\end{table}                        
""")
    names = {"my": "Mine (s)", "rec": "Rec. Char (s)", "sem": "Sem (s)"}
    timing_rows = [
        {
            "name": names[prefix],
            "chunk_time": f"{numbers.chunk_time:.2f}",
            "index_time": f"{numbers.index_time:.2f}",
            "total": f"{numbers.chunk_time + numbers.index_time:.2f}",
        }
        for prefix, numbers in approaches.items()
    ]
    print(template.render(items=timing_rows))

    # retriever
    template = Template(r"""
\begin{table}[htbp]
\centering
\small
\begin{tabular}{llrrr}
\toprule
Diff. & Approach & Recall\,$\uparrow$ & Precision\,$\uparrow$ & Ctx.\,Rel.\,$\uparrow$ \\
\midrule
{% for item in items %}
\multirow{3}{*}{ {{ item.diff  }} }
 & Mine & {{ item.my_recall }} & {{ item.my_precision }} & {{ item.my_cr }} \\
 & Rec. Char & {{ item.rec_recall }} & {{ item.rec_precision }} & {{ item.rec_cr }} \\
 & Sem & {{ item.sem_recall }} & {{ item.sem_precision }} & {{ item.sem_cr }} \\
\midrule
{% endfor %}
\multirow{3}{*}{ \textit{Overall} }
 & Mine & {{ overall.my_recall }} & {{ overall.my_precision }} & {{ overall.my_cr }} \\
 & Rec. Char & {{ overall.rec_recall }} & {{ overall.rec_precision }} & {{ overall.rec_cr }} \\
 & Sem & {{ overall.sem_recall }} & {{ overall.sem_precision }} & {{ overall.sem_cr }} \\
 \bottomrule
\end{tabular}
\caption[Results: Retriever Effectiveness Metrics]{Retriever Effectiveness across three difficulty levels. ``Ctx. Rel.'' represents context relevance. Values in parentheses indicate the theoretical maximum precision, calculated as $\frac{1}{|Q|} \sum_{q\in Q} \frac{|T_q|}{k}$ with $Q$ the set of questions and $T_q$ the set of text passages the question generation model deemed relevant to answer the question.}
\label{tab:eval-retriever-effectiveness}
\end{table}
""")

    ret_metrics = _metrics(("recall", "precision", "cr"))
    diffs: list[int] = []
    ret_rows: list[dict[str, float]] = []
    for diff in sorted(approaches["my"].retriever):
        diffs.append(diff)
        row = {}
        for prefix, numbers in approaches.items():
            r = numbers.retriever[diff]
            row[f"{prefix}_recall"] = r.recall
            row[f"{prefix}_precision"] = r.precision
            row[f"{prefix}_cr"] = r.context_relevance
        ret_rows.append(row)

    # weight the overall row by how many questions each difficulty contributes
    stats = _question_stats(questions_dir, diffs)
    ret_weights = [stats[d][0] if d in stats else 1 for d in diffs]
    ret_overall = _weighted_mean(ret_rows, ret_weights, ret_metrics)
    ret_items = [{"diff": d, **_fmt(r, ret_metrics)} for d, r in zip(diffs, ret_rows)]
    print(template.render(items=ret_items, overall=_fmt(ret_overall, ret_metrics)))

    # generator
    template = Template(r"""
\begin{table}[htbp]
\centering
\small
\begin{tabular}{llrrrr}
\toprule
Diff. & Approach & Faithf.\,$\uparrow$ & Ans.\,Rel.\,$\uparrow$ & Abstention\,$\downarrow$ & Correct.\,$\uparrow$ \\
\midrule
{% for item in items %}
\multirow{3}{*}{ {{item.diff}} } & Mine & {{ item.my_af }} & {{ item.my_ar }} & {{ item.my_a }} & {{ item.my_c }} \\
 & Rec.\,Char & {{ item.rec_af }} & {{ item.rec_ar }} & {{ item.rec_a }} & {{ item.rec_c }} \\
 & Semantic & {{ item.sem_af }} & {{ item.sem_ar }} & {{ item.sem_a }} & {{ item.sem_c }} \\
\midrule
{% endfor %}
\multirow{3}{*}{\textit{Overall}} & Mine & {{ overall.my_af }} & {{ overall.my_ar }} & {{ overall.my_a }} & {{ overall.my_c }} \\
 & Rec.\,Char & {{ overall.rec_af }} & {{ overall.rec_ar }} & {{ overall.rec_a }} & {{ overall.rec_c }} \\
 & Semantic & {{ overall.sem_af }} & {{ overall.sem_ar }} & {{ overall.sem_a }} & {{ overall.sem_c }} \\

\bottomrule
\end{tabular}
\caption[Results: Generation Effectiveness Metrics]{Generation effectiveness metrics across three difficulty levels. From left to right: question difficulty, chunking approach, answer faithfulness, answer relevance, abstention, correctness.}\label{tab:eval-generation-effectiveness}
\end{table}
""")

    gen_metrics = _metrics(("af", "ar", "a", "c"))
    diffs = []
    gen_rows: list[dict[str, float]] = []
    for diff in sorted(approaches["my"].generator):
        diffs.append(diff)
        row = {}
        for prefix, numbers in approaches.items():
            g = numbers.generator[diff]
            row[f"{prefix}_af"] = g.answer_faithfulness
            row[f"{prefix}_ar"] = g.answer_relevance
            row[f"{prefix}_a"] = g.abstention
            row[f"{prefix}_c"] = g.correctness
        gen_rows.append(row)

    gen_stats = _question_stats(questions_dir, diffs)
    gen_weights = [gen_stats[d][0] if d in gen_stats else 1 for d in diffs]
    gen_overall = _weighted_mean(gen_rows, gen_weights, gen_metrics)
    gen_items = [{"diff": d, **_fmt(r, gen_metrics)} for d, r in zip(diffs, gen_rows)]
    print(template.render(items=gen_items, overall=_fmt(gen_overall, gen_metrics)))


"""Below follows LLM-GENERATED plot routing code."""

METRIC_ATTRS: dict[str, str] = {
    "cu": "concept_unity",
    "si": "semantic_independence",
    "ip": "info_preservation",
}
"""Metric key -> attribute on `ChunkQualityScores`, in plot order."""

METRIC_LABELS = {
    "cu": "Concept Unity",
    "si": "Semantic Independence",
    "ip": "Information Preservation",
}

DIFF_METRIC_ATTRS: dict[str, tuple[Literal["retriever", "generator"], str]] = {
    "correctness": ("generator", "correctness"),
    "answer_faithfulness": ("generator", "answer_faithfulness"),
    "answer_relevance": ("generator", "answer_relevance"),
    "context_relevance": ("retriever", "context_relevance"),
    "abstention": ("generator", "abstention"),
}
"""Per-difficulty metric key -> (`ApproachNumbers` field, score attribute), in plot order."""

DIFF_METRIC_LABELS = {
    "correctness": "Correctness",
    "answer_faithfulness": "Answer Faithfulness",
    "answer_relevance": "Answer Relevance",
    "context_relevance": "Context Relevance",
    "abstention": "Abstention",
}


def _grouped_bar_plot(
    values: list[list[float]],
    x_labels: list[str],
    xlabel: str,
    ylabel: str,
):
    """Grouped bar chart with one bar group per `x_labels` entry and one bar per approach."""
    mine, rec_char, semantic = values
    x = np.arange(len(x_labels))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 5))

    # Create bars
    ax.bar(x - width, mine, width, label="Mine", color="#2A78D6")
    ax.bar(x, rec_char, width, label="Rec. Char", color="#1BAF7A")
    ax.bar(x + width, semantic, width, label="Semantic", color="#EDA100")

    # Labels and formatting
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_ylim(0, 1.0)
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.1, 1.1), ncol=3, frameon=False)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    return fig


def make_chunk_quality_plot(
    values: list[list[float]],
    datasets: list[str],
    metric: Literal["cu", "si", "ip"] = "cu",
):
    return _grouped_bar_plot(
        values, datasets, "Dataset", f"Mean {METRIC_LABELS[metric]}"
    )


K = 10
"""Retrieval depth `run_eval_retriever` computes precision against."""


def _question_stats(
    questions_dir: Path | None, diffs: list[int]
) -> dict[int, tuple[int, int]]:
    """Per difficulty, the (number of questions, number of relevant passages) in `questions_dir/<diff>`."""
    if questions_dir is None:
        return {}
    stats: dict[int, tuple[int, int]] = {}
    for diff in diffs:
        questions = _load_questions(questions_dir / str(diff))
        stats[diff] = (len(questions), sum(len(q.relevant_content) for q in questions))
    return stats


def make_retriever_plot(
    recall: list[list[float]],
    precision: list[list[float]],
    diffs: list[int],
    stats: Mapping[int, tuple[int, int]],
):
    """Recall and precision side by side, one panel each, grouped by difficulty.

    `stats` supplies the `n = ...` tick labels and, for precision, the dashed
    theoretical maximum mean |T_q| / k per difficulty.
    """
    n_diffs = len(diffs)
    gap = 1.0
    # recall panel at 0..n-1, precision panel after a one-slot gap
    x = np.concatenate([np.arange(n_diffs), np.arange(n_diffs) + n_diffs + gap])
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 5))
    for offset, values, label, color in (
        (-width, recall[0] + precision[0], "Mine", "#2A78D6"),
        (0.0, recall[1] + precision[1], "Rec. Char", "#1BAF7A"),
        (width, recall[2] + precision[2], "Semantic", "#EDA100"),
    ):
        ax.bar(x + offset, values, width, label=label, color=color)

    # theoretical maximum precision, one dashed line per difficulty
    for i, diff in enumerate(diffs):
        if diff not in stats:
            continue
        n_questions, n_aspects = stats[diff]
        max_precision = n_aspects / (K * n_questions)
        left, right = x[n_diffs + i] - 2 * width, x[n_diffs + i] + 2 * width
        ax.hlines(
            max_precision, left, right, color="#888888", linestyles="dashed", lw=1
        )
        ax.text(
            right,
            max_precision + 0.02,
            f"max {max_precision:.2f}",
            ha="right",
            va="bottom",
            fontsize=8,
            color="#888888",
        )

    def tick_label(diff: int, denominator: Literal["aspects", "retrieved"]) -> str:
        if diff not in stats:
            return f"Difficulty {diff}"
        n_questions, n_aspects = stats[diff]
        n = n_aspects if denominator == "aspects" else K * n_questions
        return f"Difficulty {diff}\n(n = {n})"

    ax.set_ylabel("Mean score")
    ax.set_ylim(0, 1.0)
    ax.set_xticks(x)
    ax.set_xticklabels(
        [tick_label(d, "aspects") for d in diffs]
        + [tick_label(d, "retrieved") for d in diffs]
    )

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.1, 1.1), ncol=3, frameon=False)
    ax.grid(axis="y", alpha=0.3)

    # panel brackets underneath the tick labels
    for start, end, name in (
        (x[0], x[n_diffs - 1], "Recall \u2191"),
        (x[n_diffs], x[-1], "Precision \u2191"),
    ):
        ax.plot(
            [start - 2 * width, end + 2 * width],
            [-0.13, -0.13],
            transform=ax.get_xaxis_transform(),
            color="#666666",
            lw=0.8,
            clip_on=False,
        )
        ax.text(
            (start + end) / 2,
            -0.19,
            name,
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="top",
            color="#333333",
        )

    plt.tight_layout()
    return fig


@app.command()
def make_plots(
    my: Path,
    rec_char: Path,
    sem: Path,
    out: Path = Path("."),
    questions_dir: Path | None = typer.Option(
        None,
        help="Folder with one <difficulty> subfolder of questions each, used for the `n = ...` tick labels",
    ),
):
    """Render one plot per chunk quality and per-difficulty metric from the <approach>.json files."""
    approaches = {
        prefix: ApproachNumbers.model_validate_json(path.read_bytes())
        for prefix, path in zip(PREFIXES, (my, rec_char, sem))
    }
    # `datasets` in the plot is in the sorted order run_eval writes the keys in
    ds_names = sorted(approaches["my"].chunk_quality)
    out.mkdir(parents=True, exist_ok=True)
    for metric, attr in METRIC_ATTRS.items():
        values = [
            [getattr(numbers.chunk_quality[ds], attr) for ds in ds_names]
            for numbers in approaches.values()
        ]
        fig = make_chunk_quality_plot(values, ds_names, metric)
        fig.savefig(out / f"chunk_quality_{metric}.pdf")
        plt.close(fig)

    diffs = sorted(approaches["my"].generator)
    stats = _question_stats(questions_dir, diffs)
    x_labels = [
        f"Difficulty {d}\n(n = {stats[d][0]})" if d in stats else f"Difficulty {d}"
        for d in diffs
    ]
    for metric, (field, attr) in DIFF_METRIC_ATTRS.items():
        values = [
            [getattr(getattr(numbers, field)[d], attr) for d in diffs]
            for numbers in approaches.values()
        ]
        fig = _grouped_bar_plot(
            values,
            x_labels,
            "Question difficulty",
            f"Mean {DIFF_METRIC_LABELS[metric]}",
        )
        fig.savefig(out / f"{metric}.pdf")
        plt.close(fig)

    ret_diffs = sorted(approaches["my"].retriever)
    recall = [
        [numbers.retriever[d].recall for d in ret_diffs]
        for numbers in approaches.values()
    ]
    precision = [
        [numbers.retriever[d].precision for d in ret_diffs]
        for numbers in approaches.values()
    ]
    fig = make_retriever_plot(
        recall, precision, ret_diffs, _question_stats(questions_dir, ret_diffs)
    )
    fig.savefig(out / "retriever_effectiveness.pdf")
    plt.close(fig)


if __name__ == "__main__":
    app()
