from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import UUID

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field


app = FastAPI(
    title="ARGUS Backend API",
    version="0.1.0",
    description=(
        "Day-1 P4 API contract scaffold for ARGUS MVP. "
        "Endpoints are stubs and return placeholder payloads."
    ),
)


class Role(str, Enum):
    investigator = "investigator"
    analyst = "analyst"
    supervisor = "supervisor"
    admin = "admin"
    compliance = "compliance"


class EntityType(str, Enum):
    person = "person"
    organization = "organization"
    location = "location"
    vehicle = "vehicle"
    phone = "phone"
    financial_account = "financial_account"
    event = "event"


class PatternStatus(str, Enum):
    new = "new"
    confirmed = "confirmed"
    dismissed = "dismissed"
    escalated = "escalated"


class LoginRequest(BaseModel):
    employee_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    otp: Optional[str] = Field(default=None, description="MFA OTP for demo flow")


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    role: Role
    jurisdiction_id: str


class SearchEntityItem(BaseModel):
    entity_id: UUID
    entity_type: EntityType
    canonical_name: str
    confidence_score: float
    source_record_count: int


class SearchEntityResponse(BaseModel):
    query: str
    total: int
    items: List[SearchEntityItem]


class RelationshipSummary(BaseModel):
    relationship_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relationship_type: str
    is_direct: bool
    confidence_score: float


class EntityDetailResponse(BaseModel):
    entity_id: UUID
    entity_type: EntityType
    canonical_name: str
    confidence_score: Optional[float]
    aliases: List[str] = Field(default_factory=list)
    relationships: List[RelationshipSummary] = Field(default_factory=list)


class GraphExpandRequest(BaseModel):
    root_entity_id: UUID
    max_hops: int = Field(2, ge=1, le=5)
    confidence_threshold: float = Field(0.5, ge=0.0, le=1.0)
    include_indirect: bool = True


class GraphNode(BaseModel):
    entity_id: UUID
    entity_type: EntityType
    label: str
    influence_score: Optional[float] = None


class GraphEdge(BaseModel):
    relationship_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relationship_type: str
    confidence_score: float
    is_direct: bool


class GraphExpandResponse(BaseModel):
    root_entity_id: UUID
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class CaseCreateRequest(BaseModel):
    fir_number: Optional[str] = None
    title: str = Field(..., min_length=3)
    jurisdiction_id: UUID
    category: Optional[str] = None
    is_sensitive: bool = False
    sensitivity_reason: Optional[str] = None


class CaseSummary(BaseModel):
    case_id: UUID
    fir_number: Optional[str]
    title: str
    jurisdiction_id: UUID
    status: str
    is_sensitive: bool
    category: Optional[str]
    opened_at: datetime


class CaseListResponse(BaseModel):
    total: int
    items: List[CaseSummary]


class CaseUpdateRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = Field(default=None, description="open|under_review|closed")
    category: Optional[str] = None


class PinCaseEntityRequest(BaseModel):
    entity_id: UUID


class AddCaseNoteRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class PatternSummary(BaseModel):
    pattern_id: UUID
    pattern_type: str
    confidence_score: float
    status: PatternStatus
    detected_at: datetime
    description: Optional[str] = None


class PatternListResponse(BaseModel):
    total: int
    items: List[PatternSummary]


class PatternFeedbackRequest(BaseModel):
    verdict: PatternStatus = Field(..., description="confirmed or dismissed")
    comment: Optional[str] = None


class ReportExportRequest(BaseModel):
    case_id: UUID
    scope: str = Field(..., description="whole_case|selected_entities|selected_subgraph")
    include_sections: List[str] = Field(default_factory=list)
    format: str = Field(..., description="pdf|docx")
    justification: Optional[str] = None


class ReportExportResponse(BaseModel):
    report_id: UUID
    case_id: UUID
    format: str
    status: str
    download_url: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
def login(_: LoginRequest) -> LoginResponse:
    return LoginResponse(
        access_token="demo-token",
        expires_in_seconds=900,
        role=Role.investigator,
        jurisdiction_id="00000000-0000-0000-0000-000000000001",
    )


@app.get("/search/entities", response_model=SearchEntityResponse, tags=["search"])
def search_entities(
    q: str = Query(..., min_length=1),
    entity_type: Optional[EntityType] = None,
    limit: int = Query(20, ge=1, le=100),
) -> SearchEntityResponse:
    sample_entity = SearchEntityItem(
        entity_id=UUID("11111111-1111-1111-1111-111111111111"),
        entity_type=entity_type or EntityType.person,
        canonical_name=f"Sample Match for {q}",
        confidence_score=0.91,
        source_record_count=4,
    )
    return SearchEntityResponse(query=q, total=1, items=[sample_entity][:limit])


@app.get("/entities/{entity_id}", response_model=EntityDetailResponse, tags=["entities"])
def get_entity_detail(entity_id: UUID) -> EntityDetailResponse:
    return EntityDetailResponse(
        entity_id=entity_id,
        entity_type=EntityType.person,
        canonical_name="Sample Entity",
        confidence_score=0.91,
        aliases=["S. Entity"],
        relationships=[],
    )


@app.post("/graph/expand", response_model=GraphExpandResponse, tags=["graph"])
def expand_graph(payload: GraphExpandRequest) -> GraphExpandResponse:
    return GraphExpandResponse(root_entity_id=payload.root_entity_id, nodes=[], edges=[])


@app.get("/cases", response_model=CaseListResponse, tags=["cases"])
def list_cases(
    status: Optional[str] = None,
    jurisdiction_id: Optional[UUID] = None,
) -> CaseListResponse:
    _ = (status, jurisdiction_id)
    return CaseListResponse(total=0, items=[])


@app.post("/cases", response_model=CaseSummary, tags=["cases"])
def create_case(payload: CaseCreateRequest) -> CaseSummary:
    return CaseSummary(
        case_id=UUID("22222222-2222-2222-2222-222222222222"),
        fir_number=payload.fir_number,
        title=payload.title,
        jurisdiction_id=payload.jurisdiction_id,
        status="open",
        is_sensitive=payload.is_sensitive,
        category=payload.category,
        opened_at=datetime.now(timezone.utc),
    )


@app.get("/cases/{case_id}", response_model=CaseSummary, tags=["cases"])
def get_case(case_id: UUID) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        fir_number="FIR-0001",
        title="Sample Case",
        jurisdiction_id=UUID("00000000-0000-0000-0000-000000000001"),
        status="open",
        is_sensitive=False,
        category="trafficking",
        opened_at=datetime.now(timezone.utc),
    )


@app.patch("/cases/{case_id}", response_model=CaseSummary, tags=["cases"])
def update_case(case_id: UUID, payload: CaseUpdateRequest) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        fir_number="FIR-0001",
        title=payload.title or "Sample Case",
        jurisdiction_id=UUID("00000000-0000-0000-0000-000000000001"),
        status=payload.status or "open",
        is_sensitive=False,
        category=payload.category or "trafficking",
        opened_at=datetime.now(timezone.utc),
    )


@app.post("/cases/{case_id}/entities", tags=["cases"])
def pin_entity_to_case(case_id: UUID, payload: PinCaseEntityRequest) -> dict:
    return {
        "case_id": str(case_id),
        "entity_id": str(payload.entity_id),
        "linked": True,
    }


@app.post("/cases/{case_id}/notes", tags=["cases"])
def add_case_note(case_id: UUID, payload: AddCaseNoteRequest) -> dict:
    return {
        "case_id": str(case_id),
        "note_id": "33333333-3333-3333-3333-333333333333",
        "content": payload.content,
    }


@app.get("/patterns", response_model=PatternListResponse, tags=["patterns"])
def list_patterns(
    status: Optional[PatternStatus] = None,
    limit: int = Query(20, ge=1, le=100),
) -> PatternListResponse:
    _ = (status, limit)
    return PatternListResponse(total=0, items=[])


@app.get("/patterns/{pattern_id}", response_model=PatternSummary, tags=["patterns"])
def get_pattern(pattern_id: UUID) -> PatternSummary:
    return PatternSummary(
        pattern_id=pattern_id,
        pattern_type="recurring_co_location",
        confidence_score=0.76,
        status=PatternStatus.new,
        detected_at=datetime.now(timezone.utc),
        description="Placeholder pattern summary",
    )


@app.post("/patterns/{pattern_id}/feedback", tags=["patterns"])
def feedback_pattern(pattern_id: UUID, payload: PatternFeedbackRequest) -> dict:
    return {
        "pattern_id": str(pattern_id),
        "status": payload.verdict,
        "accepted": True,
    }


@app.post("/reports/export", response_model=ReportExportResponse, tags=["reports"])
def export_report(payload: ReportExportRequest) -> ReportExportResponse:
    return ReportExportResponse(
        report_id=UUID("44444444-4444-4444-4444-444444444444"),
        case_id=payload.case_id,
        format=payload.format,
        status="queued",
        download_url="/reports/44444444-4444-4444-4444-444444444444/download",
    )
