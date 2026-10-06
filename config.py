import os
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

# Tự động load file .env từ thư mục hiện tại
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
_API_FIELDS = ("ELSEVIER_API_KEY", "ELSEVIER_INST_TOKEN", "OPENALEX_API_KEY")
_API_ENV_DEFAULTS = {key: os.getenv(key, "") for key in _API_FIELDS}
load_dotenv(dotenv_path=ENV_PATH)

class Settings:
    ELSEVIER_API_KEY: str = os.getenv("ELSEVIER_API_KEY", "")
    ELSEVIER_INST_TOKEN: str = os.getenv("ELSEVIER_INST_TOKEN", "")
    OPENALEX_API_KEY: str = os.getenv("OPENALEX_API_KEY", "")

    def refresh_api_credentials(self):
        """Apply .env API-key edits without retaining a previously loaded key."""
        values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
        for key in _API_FIELDS:
            setattr(self, key, (values.get(key, _API_ENV_DEFAULTS[key]) or "").strip())
    # Unpaywall API
    UNPAYWALL_EMAIL: str = os.getenv("UNPAYWALL_EMAIL", "bot@vnulib.edu.vn")
    
    # OpenAthens / VNU Lib
    VNU_OPENATHENS_BASE_URL: str = os.getenv(
        "VNU_OPENATHENS_BASE_URL", 
        "https://go.openathens.net/redirector/vnu.edu.vn?url="
    )
    VNU_LIBRARY_ID: str = os.getenv("VNU_LIBRARY_ID", "")
    VNU_LIBRARY_PASSWORD: str = os.getenv("VNU_LIBRARY_PASSWORD", "")
    # Keep the selected browser stable. On Windows, default to Edge unless the
    # user explicitly chooses Chrome or bundled Chromium in .env.
    VNU_BROWSER_CHANNEL: str = (
        os.getenv("VNU_BROWSER_CHANNEL", "msedge" if os.name == "nt" else "chromium").strip().lower()
        or ("msedge" if os.name == "nt" else "chromium")
    )
    VNU_BROWSER_HEADLESS: bool = os.getenv("VNU_BROWSER_HEADLESS", "false" if os.name == "nt" else "true").lower() in ("true", "1", "yes")
    VNU_BROWSER_STATE_FILE: Path = BASE_DIR / ".vnu-browser-state.json"
    VNU_BROWSER_PROFILE_DIR: Path = BASE_DIR / ".vnu-browser-profile"
    VNU_ALLOW_MANUAL_VERIFICATION: bool = os.getenv("VNU_ALLOW_MANUAL_VERIFICATION", "true").lower() in ("true", "1", "yes")
    VNU_VERIFICATION_WAIT_SECONDS: int = max(0, int(os.getenv("VNU_VERIFICATION_WAIT_SECONDS", "180")))

    # Server & UI
    APP_HOST: str = os.getenv("APP_HOST", "127.0.0.1")
    APP_PORT: int = int(os.getenv("APP_PORT", "8543"))
    AUTO_OPEN_BROWSER: bool = os.getenv("AUTO_OPEN_BROWSER", "true").lower() in ("true", "1", "yes")
    
    # Storage
    DOWNLOAD_FOLDER: Path = Path(os.getenv("DOWNLOAD_FOLDER", "./downloads"))
    if not DOWNLOAD_FOLDER.is_absolute():
        DOWNLOAD_FOLDER = BASE_DIR / DOWNLOAD_FOLDER
    DB_PATH: Path = BASE_DIR / "data" / "papers.sqlite3"

settings = Settings()

# Đảm bảo thư mục lưu trữ tồn tại
settings.DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
