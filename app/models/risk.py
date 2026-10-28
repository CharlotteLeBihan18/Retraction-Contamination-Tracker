"""Response model for the Risk endpoint.

Owner: Lara Normand. Only the owner edits this file.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    """How the model was trained and how good it is (cross-validated)."""

    trained_at: str
    n_papers: int
    n_retracted: int
    precision: float
    recall: float
    f1: float
    roc_auc: float
    baseline_precision: float = Field(description="Precision of always answering 'retracted': the bar to beat")


class RiskResponse(BaseModel):
    """The model's probability that a paper looks like a retracted one."""

    doi: str
    risk_score: float = Field(ge=0, le=1, description="Out-of-fold probability from the classifier")
    evidence: Literal["hypothesis"] = "hypothesis"
    already_retracted: bool = Field(description="True when the paper is already retracted: the score is then moot")
    explanation: str
    model: ModelInfo
