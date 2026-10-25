"""Authored discovery contracts."""
from pydantic import BaseModel, Field




class DiscoveryAcceptanceRequest(BaseModel):
    selected_move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")
    evidence_fingerprint: str = Field(min_length=1)
