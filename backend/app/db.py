import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://tracker:tracker@localhost:5432/tracker")
if not DATABASE_URL.startswith("postgresql"):
    raise RuntimeError("This application requires PostgreSQL; SQLite is not supported.")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_timeout=3, connect_args={"connect_timeout": 3})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session
