"""Games guided review contract."""
from pydantic import BaseModel, Field


class GuidedReviewAttemptRequest(BaseModel):
    """A legal correction attempted from the identified hidden-answer position."""

    finding_id: str = Field(min_length=1)

    move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
