#!/usr/bin/env python3
"""Create or promote an Atlastra admin account.

    python tools/make_admin.py <username> [password]

If the user exists, they're flagged admin (and their password reset when one is
given). If they don't exist, an admin account is created. With no password on the
command line you'll be prompted (input hidden). The admin can then sign in on the
site and the "Admin → Dashboard" link appears in the sidebar.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp import auth  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    username = sys.argv[1].strip()
    password = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("Password: ")
    if len(password or "") < 6:
        print("Password must be at least 6 characters.", file=sys.stderr)
        return 1
    user = auth.ensure_admin(username, password)
    print(f"✓ {user['username']} is now an admin (id={user['id']}). "
          f"Sign in at /admin.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
