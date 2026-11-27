import os

# Importing the app requires a DATABASE_URL; unit tests never connect to it.
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost:5432/unused")
