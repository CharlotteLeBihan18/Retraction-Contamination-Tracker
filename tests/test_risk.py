"""Tests for the at-risk model (app/services/risk.py) and GET /api/papers/{doi}/risk."""

import math
import random

import pytest

from app.services import risk

URL = "/api/papers/{}/risk"


def synthetic_rows(n=40, retracted_every=4, seed=0):
    """Papers whose embedding loosely separates retracted ones, like a real but noisy signal."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        retracted = i % retracted_every == 0
        centre = 1.0 if retracted else -1.0
        embedding = [centre + rng.gauss(0, 0.8) for _ in range(risk.EMBEDDING_DIM)]
        rows.append({
            "doi": f"10.1000/p{i}",
            "embedding": embedding,
            "year": 2010 + i % 10 if i % 7 else None,  # some missing years
            "references": rng.randint(0, 60),
            "retracted": retracted,
        })
    return rows


# ---------- Features ----------


def test_features_fill_missing_year_and_log_references():
    rows = [
        {"doi": "10.1/a", "embedding": [0.0] * risk.EMBEDDING_DIM, "year": 2010, "references": 0, "retracted": True},
        {"doi": "10.1/b", "embedding": [1.0] * risk.EMBEDDING_DIM, "year": None, "references": 9, "retracted": False},
        {"doi": None, "embedding": [1.0] * risk.EMBEDDING_DIM, "year": 2020, "references": 1, "retracted": False},
    ]
    dois, x, y = risk.to_features(rows)
    assert dois == ["10.1/a", "10.1/b"]  # the paper without DOI is skipped
    assert x.shape == (2, risk.EMBEDDING_DIM + 2)
    assert x[1, -2] == 2010  # missing year -> median of the known years
    assert x[1, -1] == pytest.approx(2.302585, rel=1e-5)  # log(1 + 9)
    assert list(y) == [1, 0]


def test_missing_reference_counts_get_the_median():
    rows = [
        {"doi": f"10.1/{i}", "embedding": [0.0] * risk.EMBEDDING_DIM, "year": 2010, "references": refs,
         "retracted": False}
        for i, refs in enumerate([9, 99, None])
    ]
    _, x, _ = risk.to_features(rows)
    assert x[2, -1] == pytest.approx(math.log1p(54))  # median of 9 and 99


def test_no_reference_count_at_all_gives_a_constant_column():
    rows = synthetic_rows(n=8)
    for row in rows:
        row["references"] = None
    _, x, _ = risk.to_features(rows)
    assert set(x[:, -1]) == {0.0}


def test_references_never_come_from_the_citations_in_our_graph():
    # Kenza's review: counting CITES inside our graph gives the label away (citers >= 1, retracted ~0).
    assert "COUNT {" not in risk.FASTRP_QUERY and "CITES" not in risk.FASTRP_QUERY
    assert "p.reference_count AS references" in risk.FASTRP_QUERY


# ---------- Training ----------


def test_training_gives_honest_metrics_and_a_score_per_paper():
    model = risk.train_from_rows(synthetic_rows())
    assert model.n_papers == 40 and model.n_retracted == 10
    assert set(model.scores) == {f"10.1000/p{i}" for i in range(40)}
    assert all(0 <= s <= 1 for s in model.scores.values())
    assert model.metrics["folds"] == 5
    assert model.metrics["baseline_precision"] == 0.25
    # The synthetic signal is learnable, so the model must beat the always-retracted baseline.
    assert model.metrics["roc_auc"] > 0.8
    assert model.metrics["precision"] > model.metrics["baseline_precision"]
    assert model.references_known == 40


def test_ablation_shows_where_the_signal_comes_from():
    model = risk.train_from_rows(synthetic_rows())
    assert set(model.ablation) == {"graph_only", "year_and_references_only"}
    # The synthetic signal is only in the embedding: the graph features carry it, the others do not.
    assert model.ablation["graph_only"] > 0.8
    assert model.ablation["year_and_references_only"] < model.ablation["graph_only"]


def test_training_is_reproducible():
    first, second = risk.train_from_rows(synthetic_rows()), risk.train_from_rows(synthetic_rows())
    assert first.scores == second.scores


def test_too_few_retracted_papers():
    rows = synthetic_rows(n=10, retracted_every=100)  # only paper 0 is retracted
    with pytest.raises(risk.NotEnoughDataError):
        risk.train_from_rows(rows)


def test_train_saves_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "fetch_rows", lambda *a, **k: synthetic_rows())
    path = tmp_path / "model.joblib"
    trained = risk.train(None, path=path)
    loaded = risk.load_model(path)
    assert loaded.scores == trained.scores


def test_untrained_model_raises(tmp_path):
    with pytest.raises(risk.ModelNotTrainedError):
        risk.load_model(tmp_path / "missing.joblib")


# ---------- Endpoint ----------


@pytest.fixture
def trained(monkeypatch):
    """A small trained model served by the endpoint, without touching the real file."""
    model = risk.train_from_rows(synthetic_rows())
    monkeypatch.setattr("app.routers.risk.load_model", lambda: model)
    return model


def test_risk_endpoint_returns_a_hypothesis(client, trained):
    body = client.get(URL.format("10.1000/p1")).json()
    assert body["doi"] == "10.1000/p1"
    assert body["risk_score"] == trained.scores["10.1000/p1"]
    assert body["evidence"] == "hypothesis" and body["already_retracted"] is False
    assert body["model"]["n_papers"] == 40 and "never evidence" in body["explanation"]


def test_doi_with_slashes_and_capitals_is_found(client, trained):
    body = client.get(URL.format("10.1000/P0")).json()
    assert body["doi"] == "10.1000/p0" and body["already_retracted"] is True


def test_unknown_paper_is_404(client, trained):
    assert client.get(URL.format("10.9999/unknown")).status_code == 404


def test_untrained_model_is_503(client, monkeypatch):
    def missing():
        raise risk.ModelNotTrainedError("not trained")

    monkeypatch.setattr("app.routers.risk.load_model", missing)
    assert client.get(URL.format("10.1000/p1")).status_code == 503


# ---------- Real Neo4j + GDS, on the sample graph ----------


@pytest.mark.neo4j
def test_trains_end_to_end_on_the_sample_graph(neo4j_client, tmp_path):
    """FastRP + training on the sample graph (20 papers, 3 retracted): it runs, it does not prove much."""
    from scripts.load_sample import SAMPLE_FILE, read_statements  # pylint: disable=import-outside-toplevel

    for statement in read_statements(SAMPLE_FILE):
        neo4j_client.write(statement)
    try:
        model = risk.train(neo4j_client, graph="test_risk", path=tmp_path / "model.joblib")
    finally:
        neo4j_client.write("CALL gds.graph.drop('test_risk', false) YIELD graphName RETURN graphName")
    assert "10.0000/sample.001" in model.scores and "10.0000/sample.001" in model.retracted
    assert model.n_retracted >= 3
