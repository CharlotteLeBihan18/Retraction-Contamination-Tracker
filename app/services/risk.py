"""At-risk model: FastRP embeddings + year + reference count -> logistic regression.

Owner: Person 3. Only the owner edits this file.

The question: does a paper *look like* the retracted ones, from its place in the
citation graph and two simple facts about it? The answer is a probability and is
always a hypothesis, never a judgement that a paper is flawed.

Features, for every paper:

* a FastRP embedding (16 numbers) of its neighbourhood in the citation graph, computed
  by GDS on the undirected projection from app/services/gds.py
* its publication year (missing years get the median year)
* its number of references (``log(1 + n)``): ``reference_count`` when the loader stored it,
  otherwise the citations it makes inside our graph

Training (``python -m app.services.risk``) runs stratified k-fold cross-validation.
Each paper's served score is its *out-of-fold* probability: it comes from a model that
never saw that paper's label, so the scores and the precision/recall are honest.
Everything is saved to ``risk_model.joblib`` next to this file, which the endpoint reads.

Known bias: the dataset is built around retracted papers and the papers citing them, so
being heavily cited is itself a strong signal here. The README discusses it.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.database import Neo4jClient
from app.services.gds import GRAPH_NAME, UNDIRECTED, ensure_projection

MODEL_PATH = Path(__file__).with_name("risk_model.joblib")
EMBEDDING_DIM = 16
SEED = 42
MAX_FOLDS = 5
THRESHOLD = 0.5

FASTRP_QUERY = """
CALL gds.fastRP.stream($graph, {
    relationshipTypes: [$relationship],
    embeddingDimension: $dim,
    randomSeed: $seed,
    concurrency: 1
})
YIELD nodeId, embedding
WITH gds.util.asNode(nodeId) AS p, embedding
RETURN p.doi AS doi,
       embedding,
       p.year AS year,
       coalesce(p.reference_count, COUNT { (p)-[:CITES]->() }) AS references,
       p:Retracted AS retracted
"""


class NotEnoughDataError(ValueError):
    """Raised when there are too few retracted or non-retracted papers to train."""


class ModelNotTrainedError(RuntimeError):
    """Raised when the endpoint is called before the model was trained."""


@dataclass
class RiskModel:  # pylint: disable=too-many-instance-attributes
    """Everything the endpoint needs, saved together in one file."""

    pipeline: Any
    scores: dict[str, float]
    retracted: set[str]
    metrics: dict[str, float]
    n_papers: int
    n_retracted: int
    trained_at: str
    features: list[str] = field(default_factory=list)


def to_features(rows: list[dict[str, Any]]) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Turn the FastRP rows into (dois, X, y). Papers without a DOI are skipped."""
    rows = [r for r in rows if r.get("doi")]
    years = [r["year"] for r in rows if isinstance(r.get("year"), int)]
    default_year = median(years) if years else 2000
    dois, features, labels = [], [], []
    for row in rows:
        year = row["year"] if isinstance(row.get("year"), int) else default_year
        references = row.get("references") or 0
        features.append([*row["embedding"], float(year), math.log1p(references)])
        labels.append(1 if row.get("retracted") else 0)
        dois.append(row["doi"])
    return dois, np.array(features, dtype=float), np.array(labels, dtype=int)


def train_from_rows(rows: list[dict[str, Any]]) -> RiskModel:
    """Cross-validate, compute honest out-of-fold scores, then fit on everything."""
    dois, x, y = to_features(rows)
    positives, negatives = int(y.sum()), int(len(y) - y.sum())
    folds = min(MAX_FOLDS, positives, negatives)
    if folds < 2:
        raise NotEnoughDataError(
            f"Need at least 2 retracted and 2 non-retracted papers (got {positives} and {negatives})"
        )
    pipeline = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000))
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=SEED)
    probabilities = cross_val_predict(pipeline, x, y, cv=cv, method="predict_proba")[:, 1]
    predicted = (probabilities >= THRESHOLD).astype(int)
    metrics = {
        "precision": round(float(precision_score(y, predicted, zero_division=0)), 3),
        "recall": round(float(recall_score(y, predicted, zero_division=0)), 3),
        "f1": round(float(f1_score(y, predicted, zero_division=0)), 3),
        "roc_auc": round(float(roc_auc_score(y, probabilities)), 3),
        "folds": folds,
        # A model that always says "retracted" gets this precision: the bar to beat.
        "baseline_precision": round(positives / len(y), 3),
    }
    pipeline.fit(x, y)
    return RiskModel(
        pipeline=pipeline,
        scores={doi: round(float(p), 4) for doi, p in zip(dois, probabilities)},
        retracted={doi for doi, label in zip(dois, y) if label},
        metrics=metrics,
        n_papers=len(y),
        n_retracted=positives,
        trained_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        features=[f"fastrp_{i}" for i in range(EMBEDDING_DIM)] + ["year", "log1p_references"],
    )


def fetch_rows(db: Neo4jClient, graph: str = GRAPH_NAME, label: str = "Paper") -> list[dict[str, Any]]:
    """FastRP embeddings and the two extra features for every paper in the projection."""
    ensure_projection(db, name=graph, label=label)
    params = {"graph": graph, "relationship": UNDIRECTED, "dim": EMBEDDING_DIM, "seed": SEED}
    return db.read(FASTRP_QUERY, params)


_CACHE: dict[Path, RiskModel] = {}


def load_model(path: Path = MODEL_PATH) -> RiskModel:
    """Load the saved model once and keep it in memory."""
    if path not in _CACHE:
        if not path.exists():
            raise ModelNotTrainedError("The risk model is not trained yet: run python -m app.services.risk")
        _CACHE[path] = joblib.load(path)
    return _CACHE[path]


def train(db: Neo4jClient, graph: str = GRAPH_NAME, label: str = "Paper", path: Path = MODEL_PATH) -> RiskModel:
    """Train on the current graph, save the model and forget any older copy in memory."""
    model = train_from_rows(fetch_rows(db, graph=graph, label=label))
    joblib.dump(model, path)
    _CACHE.pop(path, None)
    return model


def main() -> None:
    """Command line: train the model on the graph in Neo4j and print the metrics."""
    from app.config import get_settings  # pylint: disable=import-outside-toplevel

    db = Neo4jClient.from_settings(get_settings())
    try:
        model = train(db)
    finally:
        db.close()
    print(f"Trained on {model.n_papers} papers ({model.n_retracted} retracted), saved to {MODEL_PATH.name}")
    for name, value in model.metrics.items():
        print(f"  {name}: {value}")


if __name__ == "__main__":
    main()
