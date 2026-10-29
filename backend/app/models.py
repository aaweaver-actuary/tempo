"""Explicit compatibility contract exports for unmoved composition/test callers.

New domain code imports the specific app.contracts module directly. No model
implementation belongs in this temporary compatibility facade.
"""
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts.opening_evidence import OpeningEvidenceCheckpoint

from .contracts.transport.settings import (
    Settings,
)
from .contracts.transport.study import (
    QueueAttemptFailureRequest,
    PreparedNextIdentity,
    ReviewRequest,
    TeachingStateRequest,
)
from .contracts.transport.authored_lines import (
    BranchRequest,
    AnalysisPastePreviewRequest,
    AnalysisPasteSelection,
    AnalysisPasteCommitRequest,
    RemoveBranchRequest,
)
from .contracts.transport.authored_integrity import (
    IntegrityResolutionRequest,
    CoverageMaiaMove,
    CoverageMaiaSubmission,
)
from .contracts.transport.content import (
    CardRevisionRequest,
    PrefixSplitRequest,
    PrefixSplitCard,
    PrefixSplitResponse,
)
from .contracts.transport.authored_repertoire import (
    RepertoireSettingsRequest,
    RepertoireSettingsResponse,
    RepertoireRenameRequest,
    AnnotationArrow,
    AnnotationSquare,
    PositionAnnotationRequest,
)
from .contracts.transport.tactics import (
    TacticCaptureRequest,
    TacticCaptureResponse,
    TacticAttemptRequest,
    TacticActivationRequest,
)
from .contracts.transport.game_analysis import (
    GameAnalysisCandidate,
    GameAnalysisEvaluation,
    GameAnalysisRequest,
    GameAnalysisFailureRequest,
    GameAnalysisPositionClaimRequest,
    GameAnalysisLeaseRequest,
)
from .contracts.transport.engine import (
    EngineAttemptDiagnostics,
)
from .contracts.transport.defense import (
    ThreatAnalysisSubmission,
    ThreatAnalysisFailureRequest,
    DefenseAttemptRequest,
    DefenseRecognitionRequest,
)
from .contracts.transport.discovery import (
    DiscoveryAcceptanceRequest,
)
from .contracts.transport.game_findings import (
    GameFindingDecisionRequest,
    GameExclusionRequest,
    GameFindingCardRequest,
    GameFindingCurationRequest,
)
from .contracts.transport.game_sync import (
    AccountSettings,
    GameSyncRequest,
    ProviderSyncResult,
    ProviderSyncStatus,
    CompletedProviderSyncResult,
    CompletedGameSyncResult,
    GameSyncJob,
    GameSyncEnqueueResponse,
    ActiveGameFilters,
    GameSyncStatusResponse,
    GameProvider,
    ProviderSyncState,
    GameSyncJobState,
)
from .contracts.transport.games import (
    GameTimelineEvent,
    GamePublicRecord,
    GamesSummaryResponse,
    GameSummaryRecord,
)
from .contracts.transport.authored_import import (
    ImportResult,
)

__all__ = [
    'Settings',
    'QueueAttemptFailureRequest',
    'PreparedNextIdentity',
    'ReviewRequest',
    'TeachingStateRequest',
    'BranchRequest',
    'AnalysisPastePreviewRequest',
    'AnalysisPasteSelection',
    'AnalysisPasteCommitRequest',
    'RemoveBranchRequest',
    'IntegrityResolutionRequest',
    'CoverageMaiaMove',
    'CoverageMaiaSubmission',
    'CardRevisionRequest',
    'PrefixSplitRequest',
    'PrefixSplitCard',
    'PrefixSplitResponse',
    'RepertoireSettingsRequest',
    'RepertoireSettingsResponse',
    'RepertoireRenameRequest',
    'AnnotationArrow',
    'AnnotationSquare',
    'PositionAnnotationRequest',
    'TacticCaptureRequest',
    'TacticCaptureResponse',
    'TacticAttemptRequest',
    'TacticActivationRequest',
    'GameAnalysisCandidate',
    'GameAnalysisEvaluation',
    'GameAnalysisRequest',
    'GameAnalysisFailureRequest',
    'GameAnalysisPositionClaimRequest',
    'GameAnalysisLeaseRequest',
    'EngineAttemptDiagnostics',
    'ThreatAnalysisSubmission',
    'ThreatAnalysisFailureRequest',
    'DefenseAttemptRequest',
    'DefenseRecognitionRequest',
    'DiscoveryAcceptanceRequest',
    'GameFindingDecisionRequest',
    'GameExclusionRequest',
    'GameFindingCardRequest',
    'GameFindingCurationRequest',
    'AccountSettings',
    'GameSyncRequest',
    'ProviderSyncResult',
    'ProviderSyncStatus',
    'CompletedProviderSyncResult',
    'CompletedGameSyncResult',
    'GameSyncJob',
    'GameSyncEnqueueResponse',
    'ActiveGameFilters',
    'GameSyncStatusResponse',
    'GameTimelineEvent',
    'GamePublicRecord',
    'GamesSummaryResponse',
    'GameSummaryRecord',
    'ImportResult',
    'GameProvider',
    'ProviderSyncState',
    'GameSyncJobState',
    'OpeningEvidenceCheckpoint',
]
