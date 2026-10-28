from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
elif settings.DATABASE_URL.startswith("postgresql"):
    # libpq's default connect_timeout is INFINITE — a black-holed or wedged
    # Postgres would hang every connecting request (incl. the unauthenticated
    # readiness probe) indefinitely, pinning threads and pool slots.
    connect_args["connect_timeout"] = 3

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False,
)


@event.listens_for(Engine, "connect")
def _on_connect(dbapi_connection, _record) -> None:
    # SQLite has no time-zone support; local-day buckets use a Python UDF.
    from app.services.buckets import register_sqlite_functions

    register_sqlite_functions(dbapi_connection)


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
