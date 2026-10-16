from alembic import context

from tiktok_api.db import Base, engine
from tiktok_api import models  # noqa: F401


def run():
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            render_as_batch=engine.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


run()
