from fastapi import APIRouter, HTTPException, Request

from app.gateway import SIGNATURE_HEADER
from app.models import PaymentWebhookResult
from app.services import payments as payments_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payment", response_model=PaymentWebhookResult)
async def payment_webhook(request: Request) -> PaymentWebhookResult:
    # The signature covers the exact bytes sent, so read them before any parsing.
    body = await request.body()
    try:
        return await payments_service.handle_payment_webhook(body, request.headers.get(SIGNATURE_HEADER))
    except payments_service.InvalidSignature:
        raise HTTPException(status_code=401, detail="invalid signature")
    except payments_service.InvalidPayload:
        raise HTTPException(status_code=400, detail="invalid payload")
    except payments_service.PaymentIdConflict:
        raise HTTPException(status_code=409, detail="payment id already recorded with different details")
