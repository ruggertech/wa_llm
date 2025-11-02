from datetime import datetime
import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from models import Group, BaseGroup, Sender, BaseSender, upsert
from .client import WhatsAppClient


async def gather_groups(db_engine: AsyncEngine, client: WhatsAppClient):
    import logging
    from httpx import HTTPStatusError

    logger = logging.getLogger(__name__)
    
    try:
        groups = await client.get_user_groups()
    except HTTPStatusError as e:
        if e.response.status_code == 401:
            logger.warning("WhatsApp not authenticated yet. Please log in via http://localhost:3000")
            return
        raise

    async with AsyncSession(db_engine) as session:
        try:
            if groups is None or groups.results is None:
                logger.warning("No groups data returned from WhatsApp API")
                return
            
            if not groups.results.data:
                logger.info("No groups found in WhatsApp account")
                return
            
            logger.info(f"Processing {len(groups.results.data)} groups from WhatsApp")
            saved_count = 0
            skipped_count = 0
            
            for g in groups.results.data:
                # Skip groups with invalid JID
                if not g.JID or not g.JID.strip():
                    logger.warning(f"Skipping group with invalid JID: {g.Name}")
                    skipped_count += 1
                    continue
                
                try:
                    ownerUsr = g.OwnerPN or g.OwnerJID or None
                    if (await session.get(Sender, ownerUsr)) is None and ownerUsr:
                        owner = Sender(
                            **BaseSender(
                                jid=ownerUsr,
                            ).model_dump()
                        )
                        await upsert(session, owner)

                    # Only query for existing group if JID is valid
                    og = None
                    if g.JID:
                        try:
                            og = await session.get(Group, g.JID)
                        except Exception:
                            # If query fails, continue without existing group data
                            og = None

                    group = Group(
                        **BaseGroup(
                            group_jid=g.JID,
                            group_name=g.Name,
                            group_topic=g.Topic,
                            owner_jid=ownerUsr,
                            managed=og.managed if og else False,
                            community_keys=og.community_keys if og else None,
                            last_ingest=og.last_ingest if og else datetime.now(),
                            last_summary_sync=og.last_summary_sync
                            if og
                            else datetime.now(),
                            forward_url=og.forward_url if og else None,
                            send_summary_to_self=og.send_summary_to_self if og else False,
                            notify_on_spam=og.notify_on_spam if og else False,
                        ).model_dump()
                    )
                    await upsert(session, group)
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Error processing group {g.JID} ({g.Name}): {e}")
                    skipped_count += 1
                    continue
            
            # Flush to ensure all upserts are executed before commit
            await session.flush()
            await session.commit()
            logger.info(f"Successfully saved {saved_count} groups, skipped {skipped_count} groups")
            
            # Verify the commit worked using a new session
            async with AsyncSession(db_engine) as verify_session:
                verify_count = await verify_session.exec(select(Group))
                actual_count = len(list(verify_count.all()))
                logger.info(f"Verification: {actual_count} groups now in database")
        except Exception as e:
            await session.rollback()
            logger.error(f"Error in gather_groups: {e}", exc_info=True)
            raise
