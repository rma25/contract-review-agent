"""Contract analysis agent: text extraction, clause segmentation, and playbook risk review.

The agent proposes risk flags; humans decide. It never approves a flag or a contract.
"""

from app.agent.budget import AgentBudget, AgentBudgetExceededError, AgentError
from app.agent.pipeline import AnalysisRunner, ContractTooLongError, run_analysis
from app.agent.segmentation import SegmentationError

__all__ = [
    "AgentBudget",
    "AgentBudgetExceededError",
    "AgentError",
    "AnalysisRunner",
    "ContractTooLongError",
    "SegmentationError",
    "run_analysis",
]
