"""
Alert action-plane API routes.

Webhook subscriptions are always scoped to the authenticated organization.
Webhook secrets are returned only when created or rotated.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from pydantic import (
    BaseModel,
    Field,
    field_serializer,
)
from sqlalchemy.orm import Session

from src.api.auth_routes import require_csrf
from src.core.database import get_db
from src.core.security import (
    get_current_user,
    require_roles,
)
from src.models import User
from src.models.alert_delivery import AlertDelivery
from src.models.webhook_subscription import WebhookSubscription
from src.domain.alert_delivery_public_error import public_alert_delivery_error
from src.services.alert_delivery_management_service import (
    AlertDeliveryConflictError,
    AlertDeliveryNotFoundError,
    AlertDeliveryValidationError,
    get_alert_delivery,
    get_alert_delivery_retry_eligibility,
    list_alert_deliveries,
    retry_alert_delivery,
    count_alert_deliveries,
)
from src.services.alert_delivery_observability_service import (
    AlertDeliveryOperationalSummary,
    get_alert_delivery_operational_summary,
)
from src.services.webhook_subscription_service import (
    WebhookSubscriptionNotFoundError,
    WebhookSubscriptionValidationError,
    create_webhook_subscription,
    deactivate_webhook_subscription,
    list_webhook_subscriptions,
    rotate_webhook_secret,
)


router = APIRouter(
    prefix="/api/alerts",
    tags=["alerts"],
)


class WebhookSubscriptionCreateRequest(
    BaseModel
):
    url: str = Field(
        min_length=1,
        max_length=2048,
    )

    events: list[str] | None = None


class ApiResponseModel(BaseModel):
    @field_serializer("*", when_used="json")
    def serialize_api_value(
        self,
        value: object,
    ) -> object:
        if isinstance(
            value,
            datetime,
        ):
            return value.isoformat()

        return value


class AlertDeliveryResponse(ApiResponseModel):
    id: uuid.UUID
    alert_id: uuid.UUID
    destination_type: str
    destination_id: uuid.UUID
    status: str
    attempt_count: int
    retry_allowed: bool
    retry_block_reason: str | None
    next_attempt_at: datetime | None
    claimed_at: datetime | None
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime | None


class AlertDeliveryListResponse(BaseModel):
    data: list[AlertDeliveryResponse]
    total_count: int
    limit: int
    offset: int


class AlertDeliveryDetailResponse(ApiResponseModel):
    data: AlertDeliveryResponse


class AlertDeliverySummaryResponseData(ApiResponseModel):
    organization_id: uuid.UUID
    observed_at: datetime
    total_count: int
    pending_count: int
    processing_count: int
    retryable_count: int
    failed_count: int
    delivered_count: int
    claimable_count: int
    stale_processing_count: int
    exhausted_count: int
    total_attempt_count: int
    oldest_pending_at: datetime | None
    oldest_retryable_at: datetime | None
    oldest_processing_claimed_at: datetime | None


class AlertDeliverySummaryResponse(ApiResponseModel):
    data: AlertDeliverySummaryResponseData


class WebhookSubscriptionResponse(ApiResponseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime | None


class WebhookSubscriptionListResponse(ApiResponseModel):
    data: list[WebhookSubscriptionResponse]


class WebhookSubscriptionDetailResponse(ApiResponseModel):
    data: WebhookSubscriptionResponse


class WebhookSubscriptionSecretResponseData(
    WebhookSubscriptionResponse
):
    secret_key: str


class WebhookSubscriptionCreateResponse(ApiResponseModel):
    data: WebhookSubscriptionSecretResponseData


class WebhookSecretRotationResponseData(ApiResponseModel):
    id: uuid.UUID
    secret_key: str


class WebhookSecretRotationResponse(ApiResponseModel):
    data: WebhookSecretRotationResponseData


def _parse_delivery_id(
    value: str,
) -> uuid.UUID:
    try:
        return uuid.UUID(
            str(value)
        )

    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise HTTPException(
            status_code=404,
            detail="Alert delivery not found",
        ) from exc


def _serialize_delivery(
    delivery: AlertDelivery,
) -> dict:
    (
        retry_allowed,
        retry_block_reason,
    ) = get_alert_delivery_retry_eligibility(
        delivery
    )

    return {
        "id": str(
            delivery.id
        ),
        "alert_id": str(
            delivery.alert_id
        ),
        "destination_type": (
            delivery.destination_type
        ),
        "destination_id": str(
            delivery.destination_id
        ),
        "status": delivery.status,
        "attempt_count": int(
            delivery.attempt_count or 0
        ),
        "retry_allowed": retry_allowed,
        "retry_block_reason": retry_block_reason,
        "next_attempt_at": (
            delivery.next_attempt_at
        ),
        "claimed_at": delivery.claimed_at,
        "last_attempt_at": (
            delivery.last_attempt_at
        ),
        "delivered_at": (
            delivery.delivered_at
        ),
        "last_error": public_alert_delivery_error(delivery.last_error),
        "created_at": delivery.created_at,
        "updated_at": delivery.updated_at,
    }


def _serialize_delivery_summary(
    summary: AlertDeliveryOperationalSummary,
) -> dict:
    return {
        "organization_id": str(
            summary.organization_id
        ),
        "observed_at": summary.observed_at,
        "total_count": summary.total_count,
        "pending_count": summary.pending_count,
        "processing_count": (
            summary.processing_count
        ),
        "retryable_count": (
            summary.retryable_count
        ),
        "failed_count": summary.failed_count,
        "delivered_count": (
            summary.delivered_count
        ),
        "claimable_count": (
            summary.claimable_count
        ),
        "stale_processing_count": (
            summary.stale_processing_count
        ),
        "exhausted_count": (
            summary.exhausted_count
        ),
        "total_attempt_count": (
            summary.total_attempt_count
        ),
        "oldest_pending_at": (
            summary.oldest_pending_at
        ),
        "oldest_retryable_at": (
            summary.oldest_retryable_at
        ),
        "oldest_processing_claimed_at": (
            summary.oldest_processing_claimed_at
        ),
    }


def _raise_delivery_service_error(
    exc: Exception,
) -> None:
    if isinstance(
        exc,
        AlertDeliveryNotFoundError,
    ):
        raise HTTPException(
            status_code=404,
            detail="Alert delivery not found",
        ) from exc

    if isinstance(
        exc,
        AlertDeliveryValidationError,
    ):
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        AlertDeliveryConflictError,
    ):
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    raise exc



def _parse_subscription_id(
    value: str,
) -> uuid.UUID:
    try:
        return uuid.UUID(
            str(value)
        )

    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise HTTPException(
            status_code=404,
            detail="Webhook subscription not found",
        ) from exc


def _serialize_subscription(
    subscription: WebhookSubscription,
) -> dict:
    return {
        "id": str(
            subscription.id
        ),
        "url": subscription.url,
        "events": list(
            subscription.events or []
        ),
        "is_active": bool(
            subscription.is_active
        ),
        "created_at": subscription.created_at,
        "updated_at": subscription.updated_at,
    }


def _raise_service_error(
    exc: Exception,
) -> None:
    if isinstance(
        exc,
        WebhookSubscriptionNotFoundError,
    ):
        raise HTTPException(
            status_code=404,
            detail="Webhook subscription not found",
        ) from exc

    if isinstance(
        exc,
        WebhookSubscriptionValidationError,
    ):
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    raise exc

@router.get(
    "/deliveries",
    response_model=AlertDeliveryListResponse,
)
def get_alert_deliveries(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
) -> AlertDeliveryListResponse:
    try:
        deliveries = list_alert_deliveries(
            db,
            organization_id=(
                current_user.organization_id
            ),
            status=status,
            limit=limit,
            offset=offset,
        )

        total_count = count_alert_deliveries(
            db,
            organization_id=(
                current_user.organization_id
            ),
            status=status,
        )

    except AlertDeliveryValidationError as exc:
        _raise_delivery_service_error(
            exc
        )

    return AlertDeliveryListResponse(
        data=[
            _serialize_delivery(
                delivery
            )
            for delivery in deliveries
        ],
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/deliveries/summary",
    response_model=AlertDeliverySummaryResponse,
)
def get_alert_delivery_summary(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
) -> AlertDeliverySummaryResponse:
    summary = (
        get_alert_delivery_operational_summary(
            db,
            organization_id=(
                current_user.organization_id
            ),
        )
    )

    return {
        "data": _serialize_delivery_summary(
            summary
        )
    }


@router.get(
    "/deliveries/{delivery_id}",
    response_model=AlertDeliveryDetailResponse,
)
def get_alert_delivery_detail(
    delivery_id: str,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
) -> AlertDeliveryDetailResponse:
    parsed_id = _parse_delivery_id(
        delivery_id
    )

    try:
        delivery = get_alert_delivery(
            db,
            organization_id=(
                current_user.organization_id
            ),
            delivery_id=parsed_id,
        )

    except AlertDeliveryNotFoundError as exc:
        _raise_delivery_service_error(
            exc
        )

    return {
        "data": _serialize_delivery(
            delivery
        )
    }


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=AlertDeliveryDetailResponse,
)
def retry_delivery(
    delivery_id: str,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        require_roles(
            "admin",
            "user",
        )
    ),
) -> AlertDeliveryDetailResponse:
    require_csrf(
        request
    )

    parsed_id = _parse_delivery_id(
        delivery_id
    )

    try:
        delivery = retry_alert_delivery(
            db,
            organization_id=(
                current_user.organization_id
            ),
            delivery_id=parsed_id,
        )

        db.commit()

        db.refresh(
            delivery
        )

    except (
        AlertDeliveryNotFoundError,
        AlertDeliveryValidationError,
        AlertDeliveryConflictError,
    ) as exc:
        db.rollback()
        _raise_delivery_service_error(
            exc
        )

    return {
        "data": _serialize_delivery(
            delivery
        )
    }

@router.get(
    "/webhooks",
    response_model=WebhookSubscriptionListResponse,
)
def get_webhook_subscriptions(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
) -> WebhookSubscriptionListResponse:
    subscriptions = (
        list_webhook_subscriptions(
            db,
            organization_id=(
                current_user.organization_id
            ),
        )
    )

    return {
        "data": [
            _serialize_subscription(
                subscription
            )
            for subscription in subscriptions
        ]
    }


@router.post(
    "/webhooks",
    status_code=201,
    response_model=WebhookSubscriptionCreateResponse,
)
def create_webhook(
    body: WebhookSubscriptionCreateRequest,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        require_roles(
            "admin",
        )
    ),
) -> WebhookSubscriptionCreateResponse:
    require_csrf(
        request
    )

    try:
        result = (
            create_webhook_subscription(
                db,
                organization_id=(
                    current_user.organization_id
                ),
                url=body.url,
                events=body.events,
            )
        )

        db.commit()

        db.refresh(
            result.subscription
        )

    except (
        WebhookSubscriptionNotFoundError,
        WebhookSubscriptionValidationError,
    ) as exc:
        db.rollback()
        _raise_service_error(
            exc
        )

    return {
        "data": {
            **_serialize_subscription(
                result.subscription
            ),
            "secret_key": result.secret_key,
        }
    }


@router.post(
    "/webhooks/{subscription_id}/rotate-secret",
    response_model=WebhookSecretRotationResponse,
)
def rotate_webhook(
    subscription_id: str,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        require_roles(
            "admin",
        )
    ),
) -> WebhookSecretRotationResponse:
    require_csrf(
        request
    )

    parsed_id = _parse_subscription_id(
        subscription_id
    )

    try:
        secret_key = (
            rotate_webhook_secret(
                db,
                organization_id=(
                    current_user.organization_id
                ),
                subscription_id=parsed_id,
            )
        )

        db.commit()

    except (
        WebhookSubscriptionNotFoundError,
        WebhookSubscriptionValidationError,
    ) as exc:
        db.rollback()
        _raise_service_error(
            exc
        )

    return {
        "data": {
            "id": str(
                parsed_id
            ),
            "secret_key": secret_key,
        }
    }


@router.delete(
    "/webhooks/{subscription_id}",
    response_model=WebhookSubscriptionDetailResponse,
)
def deactivate_webhook(
    subscription_id: str,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        require_roles(
            "admin",
        )
    ),
) -> WebhookSubscriptionDetailResponse:
    require_csrf(
        request
    )

    parsed_id = _parse_subscription_id(
        subscription_id
    )

    try:
        subscription = (
            deactivate_webhook_subscription(
                db,
                organization_id=(
                    current_user.organization_id
                ),
                subscription_id=parsed_id,
            )
        )

        db.commit()

        db.refresh(
            subscription
        )

    except (
        WebhookSubscriptionNotFoundError,
        WebhookSubscriptionValidationError,
    ) as exc:
        db.rollback()
        _raise_service_error(
            exc
        )

    return {
        "data": _serialize_subscription(
            subscription
        )
    }