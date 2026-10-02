import uuid

from sqlalchemy.orm import Session

from app.db.models import FlagRow
from app.llm.cache import cached_explanation
from app.llm.client import LLMClient
from app.llm.explain import explain_flag
from app.services import llm_budget
from app.services.cases import flag_from_row

NOT_CONFIGURED = "explanations are not configured on this server"
OVER_BUDGET = "hourly explanation limit reached; try again later"
UNGROUNDED = "could not produce an explanation grounded in the evidence"


def explain_row(
    session: Session, workspace_id: uuid.UUID, row: FlagRow, claim_id: str, llm: LLMClient | None
) -> tuple[str, str | None]:
    flag = flag_from_row(row, claim_id)
    text = cached_explanation(flag)
    reason: str | None = None
    if text is None:
        if llm is None:
            reason = NOT_CONFIGURED
        elif not llm_budget.try_consume(session, workspace_id):
            reason = OVER_BUDGET
        else:
            text = explain_flag(flag, llm)
            reason = None if text else UNGROUNDED
    row.explanation = text
    row.explanation_status = "ready" if text else "unavailable"
    return row.explanation_status, reason
