"""Application configuration."""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
ENV_PATH = os.path.join(BASE_DIR, ".env")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

MAX_UPLOAD_SIZE = 16 * 1024 * 1024  # 16 MB
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "job-hunter-secret-key-stable")

os.makedirs(DATA_DIR, exist_ok=True)
