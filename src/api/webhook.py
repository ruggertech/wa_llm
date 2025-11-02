from typing import Annotated

from fastapi import APIRouter, Depends

from api.deps import get_handler
from handler import MessageHandler
from models.webhook import WhatsAppWebhookPayload

# Create router for webhook endpoints
router = APIRouter(tags=["webhook"])


@router.post("/webhook")
async def webhook(
    payload: WhatsAppWebhookPayload,
    handler: Annotated[MessageHandler, Depends(get_handler)],
) -> str:
    """
    WhatsApp webhook endpoint for receiving incoming messages.
    Returns:
        Simple "ok" response to acknowledge receipt
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Log webhook received
    has_text = payload.message and payload.message.text if payload.message else False
    logger.info(f"📥 Webhook received: from={payload.from_}, hasText={bool(has_text)}, text='{payload.message.text[:30] if has_text else 'N/A'}...'")
    
    # Only process messages that have a sender (from_ field)
    if payload.from_:
        logger.info(f"✅ Processing message from {payload.from_}")
        await handler(payload)
    else:
        logger.info(f"⏭️ Skipping webhook (no sender)")

    return "ok"
