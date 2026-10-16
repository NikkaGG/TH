import argparse
import time

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import Account, Mutex
from .security import get_vault


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init-db", "worker-local", "rotate-encryption"])
    args = parser.parse_args()
    if args.command == "init-db":
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            if not db.get(Mutex, "accounts"):
                db.add(Mutex(name="accounts"))
            db.commit()
    elif args.command == "rotate-encryption":
        vault = get_vault()
        with SessionLocal() as db:
            for account in db.scalars(select(Account).where(Account.status != "disconnected").with_for_update()):
                account.access_token = vault.encrypt(vault.decrypt(account.access_token))
                account.refresh_token = vault.encrypt(vault.decrypt(account.refresh_token))
            db.commit()
        print("Active account tokens re-encrypted with the first configured key.")
    else:
        from .config import get_settings
        from .jobs import tick_local

        if get_settings().queue_mode != "local":
            parser.error("worker-local requires LH_QUEUE_MODE=local")
        print("Local worker started; use one process only. Ctrl+C to stop.")
        while True:
            tick_local()
            time.sleep(5)


if __name__ == "__main__":
    main()
