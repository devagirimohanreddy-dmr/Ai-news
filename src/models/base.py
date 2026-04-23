from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.config.settings import settings


class Base(DeclarativeBase):
    pass


_engine = None
_async_session = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.DATABASE_URL, echo=False)
    return _engine


def get_session_factory():
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _async_session


async def get_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session
