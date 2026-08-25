from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.db.checkpointer import close_connection, get_checkpointer
from app.db.main import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Running lifespan before the application startup!")
    await init_db()

    # Initialize LangGraph Postgres checkpointer
    # and expose it through FastAPI app.state
    app.state.checkpointer = await get_checkpointer()
    yield
    logger.info("Running lifespan after the application shutdown!")
    await close_connection()
