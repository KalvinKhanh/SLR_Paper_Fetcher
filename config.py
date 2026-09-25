import os
from pathlib import Path
from dotenv import load_dotenv

# Tự động load file .env từ thư mục hiện tại
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

class Settings:
    # Unpaywall API
    UNPAYWALL_EMAIL: str = os.getenv("UNPAYWALL_EMAIL", "bot@vnulib.edu.vn")
    
    # OpenAthens / VNU Lib
    VNU_OPENATHENS_BASE_URL: str = os.getenv(
        "VNU_OPENATHENS_BASE_URL", 
        "https://go.openathens.net/redirector/vnu.edu.vn?url="
    )
    VNU_LIBRARY_ID: str = os.getenv("VNU_LIBRARY_ID", "")
    VNU_LIBRARY_PASSWORD: str = os.getenv("VNU_LIBRARY_PASSWORD", "")
    
    # Server & UI
    APP_HOST: str = os.getenv("APP_HOST", "127.0.0.1")
    APP_PORT: int = int(os.getenv("APP_PORT", "8543"))
    AUTO_OPEN_BROWSER: bool = os.getenv("AUTO_OPEN_BROWSER", "true").lower() in ("true", "1", "yes")
    
    # Storage
    DOWNLOAD_FOLDER: Path = Path(os.getenv("DOWNLOAD_FOLDER", "./downloads"))

settings = Settings()

# Đảm bảo thư mục lưu trữ tồn tại
settings.DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
