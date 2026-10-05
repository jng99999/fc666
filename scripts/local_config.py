"""Generate local-only credentials once. Never overwrite existing settings."""
from pathlib import Path
import os
import secrets

root = Path(__file__).resolve().parents[1]
path = root / ".env"
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing .env preserved")
else:
    password = secrets.token_hex(24)
    with os.fdopen(fd, "w") as file:
        file.write(f"POSTGRES_PASSWORD={password}\nDATABASE_URL=postgresql+psycopg://fc666:{password}@127.0.0.1:5432/fc666\nREDIS_URL=redis://127.0.0.1:6379/0\nLIVE_TRADING=false\nTRADING_MODE=PAPER\n")
    print("Created local .env (credentials omitted)")
