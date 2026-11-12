"""Break-glass admin tool, run on the VPS when nobody can log in:

    docker compose exec backend python -m app.cli list-users
    docker compose exec backend python -m app.cli reset-password <username>     # prints a one-time temporary password
    docker compose exec backend python -m app.cli unlock <username>
    docker compose exec backend python -m app.cli reset-2fa <username>          # lost phone + no backup codes

Needs shell access to the server, which is the point: it is the recovery path that does not depend on the web login.
Every use is written to the audit log with actor "cli".
"""
from __future__ import annotations
import sys
from .api_admin import temp_password
from .auth import audit, hash_pw
from .db import Base, SessionLocal, engine, ensure_columns
from .models import User, utcnow


def main(argv: list[str]) -> int:
    Base.metadata.create_all(engine); ensure_columns()
    cmd = argv[0] if argv else ""
    with SessionLocal() as db:
        if cmd == "list-users":
            for u in db.query(User).order_by(User.id):
                print(f"{u.id:>3}  {u.username:<20} {u.role:<7} {'active' if u.active else 'DISABLED':<9}"
                      f"{' must-change' if u.must_change else ''}{' LOCKED' if u.locked_until else ''}")
            return 0
        if cmd == "reset-2fa" and len(argv) == 2:
            from . import mfa
            u = db.query(User).filter_by(username=argv[1]).first()
            if not u:
                print(f"no such user: {argv[1]}", file=sys.stderr); return 1
            mfa.clear(u); u.token_ver = (u.token_ver or 0) + 1
            audit(db, "cli", "2fa_reset", u.username); db.commit()
            print(f"2FA cleared for {u.username}. If the policy requires 2FA they will enrol again at the next sign-in.")
            return 0
        if cmd in ("reset-password", "unlock") and len(argv) == 2:
            u = db.query(User).filter_by(username=argv[1]).first()
            if not u:
                print(f"no such user: {argv[1]}", file=sys.stderr); return 1
            u.failed_logins, u.locked_until = 0, None
            if cmd == "reset-password":
                pw = temp_password()
                u.password_hash, u.must_change, u.active, u.password_changed_at = hash_pw(pw), True, True, utcnow()
                u.token_ver = (u.token_ver or 0) + 1
                audit(db, "cli", "password_reset", u.username)
                db.commit()
                print(f"Temporary password for {u.username}: {pw}\nThey must choose a new password at the next login.")
            else:
                audit(db, "cli", "user_unlock", u.username); db.commit(); print(f"{u.username} unlocked")
            return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
