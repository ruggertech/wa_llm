import asyncio
from contextlib import asynccontextmanager
from warnings import warn

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
import logging
# import logfire

from api import load_new_kbtopics_api, status, summarize_and_send_to_group_api, webhook
import models  # noqa
from config import Settings
from whatsapp import WhatsAppClient
from whatsapp.init_groups import gather_groups
from voyageai.client_async import AsyncClient

settings = Settings()  # pyright: ignore [reportCallIssue]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings
    # Create and configure logger
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=settings.log_level,
    )

    app.state.settings = settings

    app.state.whatsapp = WhatsAppClient(
        settings.whatsapp_host,
        settings.whatsapp_basic_auth_user,
        settings.whatsapp_basic_auth_password,
    )

    if settings.db_uri.startswith("postgresql://"):
        warn("use 'postgresql+asyncpg://' instead of 'postgresql://' in db_uri")
    engine = create_async_engine(
        settings.db_uri,
        pool_size=20,
        max_overflow=40,
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=600,
        future=True,
    )
    # logfire.instrument_sqlalchemy(engine)
    async_session = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    # Start group gathering task (with retry logic)
    async def gather_groups_with_retry():
        import asyncio
        from httpx import HTTPStatusError
        
        max_retries = 3
        retry_delay = 10  # seconds
        
        for attempt in range(max_retries):
            try:
                await gather_groups(engine, app.state.whatsapp)
                break  # Success, exit retry loop
            except HTTPStatusError as e:
                if e.response.status_code == 401:
                    if attempt < max_retries - 1:
                        logging.warning(f"WhatsApp not authenticated (attempt {attempt + 1}/{max_retries}). Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                    else:
                        logging.warning("WhatsApp not authenticated after retries. Please log in via http://localhost:3000")
                else:
                    raise  # Re-raise non-401 errors
            except Exception as e:
                logging.error(f"Error gathering groups: {e}")
                break
    
    asyncio.create_task(gather_groups_with_retry())
    
    # Keep-alive task: periodically ping WhatsApp to maintain session
    async def whatsapp_keepalive():
        """Periodically ping WhatsApp API to keep session alive - pings every 90 seconds to ensure 15+ minute session"""
        import asyncio
        from httpx import HTTPStatusError
        from datetime import datetime
        
        keepalive_interval = 45  # Ping every 45 seconds (more frequent to prevent REMOTE_LOGOUT)
        initial_delay = 10  # Start first ping after 10 seconds
        
        logger = logging.getLogger(__name__)
        logger.info(f"WhatsApp keep-alive task started (will ping every {keepalive_interval}s)")
        print(f"WhatsApp keep-alive task started (will ping every {keepalive_interval}s)")  # Also print to ensure visibility
        
        # Verify WhatsApp client is available
        if not hasattr(app.state, 'whatsapp'):
            logger.error("WhatsApp client not available in app.state!")
            print("ERROR: WhatsApp client not available!")
            return
        
        # Wait initial delay before first ping
        await asyncio.sleep(initial_delay)
        
        ping_count = 0
        session_start_time = datetime.now()
        last_successful_ping = datetime.now()
        devices_count = None
        
        while True:
            try:
                ping_count += 1
                # Ping - get devices list (lightweight call)
                devices_response = await app.state.whatsapp.get_devices()
                devices_count = len(devices_response.results) if devices_response.results else 0
                last_successful_ping = datetime.now()
                logger.info(f"WhatsApp keep-alive ping #{ping_count} successful (next ping in {keepalive_interval}s)")
                print(f"WhatsApp keep-alive ping #{ping_count} successful")  # Also print for visibility
                await asyncio.sleep(keepalive_interval)
            except HTTPStatusError as e:
                if e.response.status_code == 401:
                    session_duration = (datetime.now() - session_start_time).total_seconds()
                    logger.warning(f"WhatsApp session expired during keep-alive ping #{ping_count} - REMOTE_LOGOUT detected")
                    logger.warning("⚠️ REMOTE_LOGOUT: User must have logged out from phone or linked device elsewhere")
                    logger.warning("   Action required: Log in again at http://localhost:3000")
                    logger.warning(f"📊 Logout Details:")
                    logger.warning(f"   - Session duration: {session_duration:.1f} seconds ({session_duration/60:.1f} minutes)")
                    logger.warning(f"   - Total pings before logout: {ping_count}")
                    logger.warning(f"   - Last successful ping: {last_successful_ping.isoformat() if last_successful_ping else 'N/A'}")
                    logger.warning(f"   - Devices before logout: {devices_count}")
                    if last_successful_ping:
                        logger.warning(f"   - Time since last ping: {(datetime.now() - last_successful_ping).total_seconds():.1f}s")
                    print(f"⚠️ REMOTE_LOGOUT detected on ping #{ping_count}")
                    print("   User must log in again at http://localhost:3000")
                    # Reset session tracking
                    session_start_time = datetime.now()
                    last_successful_ping = None
                    # Wait longer before retrying - session is definitely logged out
                    await asyncio.sleep(60)
                else:
                    logger.error(f"WhatsApp keep-alive failed on ping #{ping_count}: {e}")
                    await asyncio.sleep(keepalive_interval)
            except Exception as e:
                logger.error(f"Error in WhatsApp keep-alive on ping #{ping_count}: {e}", exc_info=True)
                logger.error(f"📊 Keep-alive Error Details:")
                logger.error(f"   - Ping count: {ping_count}")
                logger.error(f"   - Last successful ping: {last_successful_ping.isoformat() if last_successful_ping else 'N/A'}")
                logger.error(f"   - Devices: {devices_count}")
                logger.error(f"   - Error type: {type(e).__name__}")
                logger.error(f"   - Session duration: {(datetime.now() - session_start_time).total_seconds():.1f}s")
                print(f"❌ Keep-alive error on ping #{ping_count}: {e}")  # Also print
                await asyncio.sleep(keepalive_interval)
    
    # Start keep-alive task and ensure it runs
    keepalive_task = asyncio.create_task(whatsapp_keepalive())
    logging.info("Keep-alive task created and scheduled")
    print("Keep-alive task created and scheduled")  # Also print for visibility
    
    # Store task reference to prevent garbage collection
    app.state.keepalive_task = keepalive_task

    app.state.db_engine = engine
    app.state.async_session = async_session
    app.state.embedding_client = AsyncClient(
        api_key=settings.voyage_api_key, max_retries=settings.voyage_max_retries
    )
    try:
        yield
    finally:
        await engine.dispose()


# Initialize FastAPI app
app = FastAPI(title="Webhook API", lifespan=lifespan)

# logfire.configure()
# logfire.instrument_pydantic_ai()
# logfire.instrument_fastapi(app)
# logfire.instrument_httpx(capture_all=True)
# logfire.instrument_system_metrics()

app.include_router(webhook.router)
app.include_router(status.router)
app.include_router(summarize_and_send_to_group_api.router)
app.include_router(load_new_kbtopics_api.router)

if __name__ == "__main__":
    import uvicorn

    print(f"Running on {settings.host}:{settings.port}")

    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
