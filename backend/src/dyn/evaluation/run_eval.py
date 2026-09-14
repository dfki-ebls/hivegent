from pydantic_evals.reporting import EvaluationReport
import random
import os
from pathlib import Path
from time import time
from typing import Annotated, Literal
import asyncio

import jsonlines
import typer
from pydantic import BaseModel, TypeAdapter
from dataclasses import dataclass

from .generate_chunks import process_folder
from .generate_chunks_chonkie import process_folder as process_folder_chonkie
from .generate_vector_db import build as build_vector_db
from ..rag import RAG
from .llm_as_a_judge import (
    eval_concept_unity,
    eval_info_preservation,
    eval_semantic_independence,
    eval_context_relevance,
    eval_rag_measures,
    InfoPresSentence,
    Question,
)

app = typer.Typer()
random.seed(42)


_INFO_PRES_ADAPTER = TypeAdapter(list[InfoPresSentence])


def _load_info_pres(path: Path) -> list[InfoPresSentence]:
    """Validate the JSON content read from the provided file path.

    Args:
        path (Path): The file path to load information from.

    Returns:
        list[InfoPresSentence]: The validated list of information sentences
    """
    return _INFO_PRES_ADAPTER.validate_json(path.read_bytes())


@dataclass
class TimingResult:
    chunk_time: float
    index_time: float


def _dataset_folders(docfolder: Path) -> list[Path]:
    """Return the sorted list of immediate subdirectories or the input folder.

    Args:
        docfolder (Path): The path to the directory to inspect

    Returns:
        list[Path]: A list of directory paths
    """
    subfolders = sorted(p for p in docfolder.iterdir() if p.is_dir())
    return subfolders or [docfolder]


def _chunk_datasets(
    approach: Literal["my", "rec_char", "sem"],
    docfolder: Path,
    glob: str,
    chunk_size: int,
    chunks_out: Path,
) -> Path:
    """LLM-GENERATED. Chunk every dataset into `chunks_out/<approach>/<dataset>.jsonl`.

    Returns the folder the chunk files were written to.
    """
    outfolder = chunks_out / approach
    outfolder.mkdir(parents=True, exist_ok=True)

    for dataset in _dataset_folders(docfolder):
        outpath = outfolder / f"{dataset.name}.jsonl"
        if approach == "my":
            process_folder(dataset, glob, chunk_size, outpath)
        else:
            process_folder_chonkie(dataset, glob, chunk_size, outpath, approach)

    return outfolder


@app.command()
def run_eval_times(
    approach: Literal["my", "rec_char", "sem"],
    docfolder: Annotated[
        Path, typer.Argument(help="Folder with one subfolder per dataset")
    ],
    glob: str,
    chunks_out: Annotated[
        Path, typer.Argument(help="Folder into which the chunks are written")
    ],
    db_out: Annotated[
        Path, typer.Argument(help="Folder into which vector databases are written")
    ],
    n: int = 5,
    chunk_size: int = 2048,
) -> TimingResult:
    """Runs the timing evaluation including chunking and indexing times, averaged over n runs."""
    # chunking, one jsonl file per dataset
    chunk_times: list[float] = []
    index_times: list[float] = []
    for _ in range(n):
        start = time()
        chunks_folder = _chunk_datasets(
            approach, docfolder, glob, chunk_size, chunks_out
        )
        chunk_times.append(time() - start)

    # indexing: all datasets of one approach share a single vector database
    for _ in range(n):
        start = time()
        build_vector_db(chunks_folder, db_out)
        index_times.append(time() - start)

    avg_chunk_times = sum(chunk_times) / n
    avg_index_times = sum(index_times) / n
    print("----------------")
    print("Results")
    print("----------------")
    print(f"Average {approach} chunking time: {avg_chunk_times}(s)")
    print(f"Average {approach} indexing time: {avg_index_times}(s)")
    return TimingResult(avg_chunk_times, avg_index_times)


type ConceptUnityResults = EvaluationReport
type SemanticIndependenceResults = EvaluationReport
type InfoPresResults = EvaluationReport
type ChunkQualityResult = tuple[
    ConceptUnityResults, SemanticIndependenceResults, InfoPresResults
]
type ChunkQualityResults = dict[str, ChunkQualityResult]


@app.command()
def run_eval_chunk_quality(
    chunks_folder: Annotated[
        Path,
        typer.Argument(help="Folder with one <dataset>.jsonl chunk file per dataset"),
    ],
    info_preservation_sents_folder: Annotated[
        Path, typer.Argument(help="Folder with one <dataset>.json file per dataset")
    ],
    db_dir: Path,
    n_chunks: Annotated[
        int,
        typer.Argument(
            help="used for randomly sampling chunks for concept unity and semantic independence measures",
        ),
    ] = 30,
    glob: str = "*.jsonl",
) -> ChunkQualityResults:
    """Runs the chunk quality evaluation, including Concept Unity, Semantic Independence, Information Preservation
    per dataset."""
    datasets: dict[str, dict[str, str]] = {}  # dataset_name -> (key, chunk) dict
    dataset_eval_chunks: dict[str, dict[str, str]] = {}
    dataset_scores: dict[str, ChunkQualityResult] = {}
    files = sorted(chunks_folder.glob(glob))
    if not files:
        raise ValueError(f"No files matching {glob!r} in {chunks_folder}")

    for file in files:
        with jsonlines.open(file, "r") as reader:
            datasets[file.stem] = {obj["key"]: obj["chunk"] for obj in reader}

    # limit to n_chunks chunks per dataset
    for key in datasets:
        chunks = list(datasets[key].items())
        random.shuffle(chunks)
        dataset_eval_chunks[key] = dict(chunks[:n_chunks])

    rag = RAG(db_dir)

    async def async_part() -> ChunkQualityResults:
        for ds_name, ds in dataset_eval_chunks.items():
            cu = await eval_concept_unity(ds, ds_name)
            si = await eval_semantic_independence(ds, ds_name, rag.retrieve)

            # load info preservation sentences for this specific dataset
            sents_path = info_preservation_sents_folder / f"{ds_name}.json"
            if not sents_path.exists():
                raise ValueError(
                    f"No info preservation sentences for dataset {ds_name!r} "
                    f"(expected {sents_path})"
                )

            sents = _load_info_pres(sents_path)
            ip = await eval_info_preservation(sents, ds_name, rag.retrieve)
            dataset_scores[ds_name] = (cu, si, ip)
        return dataset_scores

    dataset_scores = asyncio.run(async_part())
    for ds_name, (cu, si, ip) in dataset_scores.items():
        print(f"\n=== {ds_name} ===")
        cu.print(include_averages=True)
        si.print(include_averages=True)
        ip.print(include_averages=True)

    print("\n----------------")
    print("Average scores")
    print("----------------")
    for ds_name, (cu, si, ip) in dataset_scores.items():
        for report in (cu, si, ip):
            avg = report.averages()
            if avg is None:
                continue
            for measure, value in avg.scores.items():
                print(f"{ds_name}\t{measure}: {value:.2f}")
    return dataset_scores


def _load_questions(questions_dir: Path) -> list[Question]:
    """Load questions from JSON files within the specified directory.

    Args:
        questions_dir (Path): The directory to search

    Returns:
        list[Question]: A list of loaded Question objects
    """
    return [
        Question.model_validate_json(q.read_bytes())
        for q in sorted(questions_dir.glob("*_question.json"))
    ]


@dataclass
class RetrieverResult:
    recall: float
    precision: float
    context_relevance: EvaluationReport


@app.command()
def run_eval_retriever(db_dir: Path, questions_dir: Path) -> RetrieverResult:
    """Runs the retriever evaluation, including Recall, Precision and Context Relevance per difficulty."""
    rag = RAG(db_dir)
    questions = _load_questions(questions_dir)
    cr = asyncio.run(
        eval_context_relevance(questions, questions_dir.name, rag.retrieve, rag.rag)
    )
    aspects_total, found_aspects = 0, 0
    for q in questions:
        results = rag.retrieve(q.question)
        aspects_total += len(q.relevant_content)

        # precision = sum(found_aspects) / total retrieval invocations
        # recall = sum(found_aspects) / aspects_total

        # here: how count found aspects
        for aspect in q.relevant_content:
            found = False
            for res in results:
                if aspect in res:
                    found = True
                    break
            if found:
                found_aspects += 1

    recall = found_aspects / aspects_total
    precision = found_aspects / (10 * len(questions))
    cr.print(include_averages=True)
    print("Recall ", recall)
    print("Precision ", precision)
    return RetrieverResult(recall, precision, cr)


@app.command()
def run_eval_generator(db_dir: Path, questions_dir: Path) -> EvaluationReport:
    """Runs the generator evaluation, including the RAG metrics Answer Faithfulness,
    Answer Relevance, Abstention and Correctness."""
    # unlike the other metrics, we can evaluate all generator metrics together
    rag = RAG(db_dir)
    questions = _load_questions(questions_dir)
    res = asyncio.run(
        eval_rag_measures(questions, questions_dir.name, rag.retrieve, rag.rag)
    )
    res.print(include_averages=True)
    return res


@dataclass
class CompleteResult:
    timing: TimingResult
    chunk_quality: ChunkQualityResults
    retriever: dict[int, RetrieverResult]
    generator: dict[int, EvaluationReport]


@app.command()
def run_complete_approach(
    approach: Literal["my", "rec_char", "sem"],
    docfolder: Path,
    questions_dir: Annotated[
        Path,
        typer.Argument(
            help="A folder with 3 subfolders (1,2,3), each of which containing files *_question.json"
        ),
    ],
    info_preservation_sentences: Annotated[
        Path,
        typer.Argument(
            help="A folder with <dataset.json> info preservations per dataset"
        ),
    ],
    n: int = 5,
    n_chunks: int = 30,
    chunk_size: int = 2048,
    numbers_out: Annotated[
        Path | None,
        typer.Option(help="JSON file the numbers used in the tables are written to"),
    ] = None,
) -> CompleteResult:
    """Runs the full evaluation (timing, chunk quality, retriever, generator) for a specified approach
    (my, rec_char, sem)."""
    chunks_out = Path("/tmp/eval/chunks")
    db_out = Path(f"/tmp/eval/dbs/{approach}")
    os.makedirs(chunks_out, exist_ok=True)
    os.makedirs(db_out, exist_ok=True)

    times_result = run_eval_times(
        approach, docfolder, "*.*", chunks_out, db_out, n=n, chunk_size=chunk_size
    )
    # run_eval_times writes to chunks_out/<approach>/<dataset>.jsonl
    chunk_qual = run_eval_chunk_quality(
        chunks_out / approach, info_preservation_sentences, db_out, n_chunks
    )
    retriever_eff: dict[int, RetrieverResult] = {}
    generator_eff: dict[int, EvaluationReport] = {}
    for diff in range(1, 4):
        retriever_eff[diff] = run_eval_retriever(db_out, questions_dir / str(diff))
        generator_eff[diff] = run_eval_generator(db_out, questions_dir / str(diff))
    result = CompleteResult(times_result, chunk_qual, retriever_eff, generator_eff)
    persist(approach, result, numbers_out or Path(f"/tmp/eval/numbers/{approach}.json"))
    return result


"""Below is LLM-generated logic to persist a complete report with all values needed for the tables/graphs."""


class ChunkQualityScores(BaseModel):
    """Average chunk quality scores of one dataset."""

    concept_unity: float
    semantic_independence: float
    info_preservation: float


class RetrieverScores(BaseModel):
    """Retriever effectiveness of one difficulty level."""

    recall: float
    precision: float
    context_relevance: float


class GeneratorScores(BaseModel):
    """Generation effectiveness of one difficulty level."""

    answer_faithfulness: float
    answer_relevance: float
    abstention: float
    correctness: float


class ApproachNumbers(BaseModel):
    """Exactly the numbers `tables_plots.py` needs for one chunking approach."""

    approach: Literal["my", "rec_char", "sem"]
    chunk_time: float
    index_time: float
    chunk_quality: dict[str, ChunkQualityScores]
    retriever: dict[int, RetrieverScores]
    generator: dict[int, GeneratorScores]


def _avg(report: EvaluationReport, measure: str) -> float:
    averages = report.averages()
    assert averages is not None
    return float(averages.scores[measure])


def persist(
    approach: Literal["my", "rec_char", "sem"], result: CompleteResult, out: Path
) -> ApproachNumbers:
    """Reduce `result` to the numbers used in the tables and write them to `out`."""
    numbers = ApproachNumbers(
        approach=approach,
        chunk_time=result.timing.chunk_time,
        index_time=result.timing.index_time,
        chunk_quality={
            ds_name: ChunkQualityScores(
                concept_unity=_avg(cu, "Concept Unity"),
                semantic_independence=_avg(si, "Semantic Independence"),
                info_preservation=_avg(ip, "Info Preservation"),
            )
            for ds_name, (cu, si, ip) in result.chunk_quality.items()
        },
        retriever={
            diff: RetrieverScores(
                recall=res.recall,
                precision=res.precision,
                context_relevance=_avg(res.context_relevance, "Context Relevance"),
            )
            for diff, res in result.retriever.items()
        },
        generator={
            diff: GeneratorScores(
                answer_faithfulness=_avg(report, "Answer Faithfulness"),
                answer_relevance=_avg(report, "Answer Relevance"),
                abstention=_avg(report, "Abstention"),
                correctness=_avg(report, "Correctness"),
            )
            for diff, report in result.generator.items()
        },
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(numbers.model_dump_json(indent=2))
    print(f"Wrote {approach} numbers to {out}")
    return numbers


if __name__ == "__main__":
    app()
