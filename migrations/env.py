"""Alembic migration environment; reads URL only from a private env/Secret."""
import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool
from tts_inference.persistence import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
url = os.environ.get("TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL")
if not url:
    raise RuntimeError("TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL required to run migrations")
if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
