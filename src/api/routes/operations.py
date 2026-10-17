from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.pipeline_capability_responses import (
    VectorCapabilitiesResponse,
    vector_capabilities_response,
)
from src.api.worker_operations_responses import (
    WorkerOperationsResponse,
    worker_operations_response,
)
from src.core.database import get_db
from src.core.security import require_roles
from src.models.user import User
from src.services.pipeline_capability_read_service import (
    get_vector_capability_overview,
)
from src.services.worker_operational_read_service import (
    get_worker_operational_overview,
)


router = APIRouter(
    prefix="/api/operations",
    tags=["operations"],
)


@router.get(
    "/workers",
    response_model=WorkerOperationsResponse,
)
def get_worker_operations(
    user: User = Depends(
        require_roles("admin")
    ),
    db: Session = Depends(
        get_db
    ),
) -> WorkerOperationsResponse:
    del user

    overview = get_worker_operational_overview(
        db
    )

    return worker_operations_response(
        overview
    )


@router.get(
    "/capabilities",
    response_model=VectorCapabilitiesResponse,
)
def get_vector_capabilities(
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> VectorCapabilitiesResponse:
    """Report vector worker configuration, not external provider availability."""
    del user
    worker_overview = get_worker_operational_overview(db)
    capability_overview = get_vector_capability_overview(worker_overview)
    return vector_capabilities_response(capability_overview)
