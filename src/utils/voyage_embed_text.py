import logging
from typing import List

from voyageai.client_async import AsyncClient

logger = logging.getLogger(__name__)


async def voyage_embed_text(
    embedding_client: AsyncClient, input: List[str]
) -> List[List[float]]:
    model_name = "voyage-3"
    batch_size = 128
    embeddings = []
    total_tokens = 0

    logger.info(f"🔵 Voyage AI: Starting embedding for {len(input)} text(s)")
    
    for i in range(0, len(input), batch_size):
        batch = input[i : i + batch_size]
        logger.info(f"🔵 Voyage AI: Calling embed API for batch {i//batch_size + 1} ({len(batch)} texts)")
        res = await embedding_client.embed(
            batch, model=model_name, input_type="document"
        )
        embeddings += res.embeddings
        total_tokens += res.total_tokens
        logger.info(f"🔵 Voyage AI: Batch complete. Total tokens: {total_tokens}, Embeddings: {len(embeddings)}")
    
    logger.info(f"🔵 Voyage AI: Completed. Total embeddings: {len(embeddings)}, Total tokens: {total_tokens}")
    return embeddings
