import os
import shutil
import datetime
from pathlib import Path
from app.core.config import settings
from app.core.logger import logger

def backup_db() -> None:
    """Creates a timestamped backup of the sqlite database and purges backups older than 7 days."""
    # Resolve the physical database file path from connection string
    db_url = settings.DATABASE_URL
    if not db_url.startswith("sqlite:///"):
        logger.warning("Database is not SQLite. Skipping backup.")
        return
        
    db_path = Path(db_url.replace("sqlite:///", ""))
    if not db_path.exists():
        logger.warning(f"Database file not found at {db_path}. Skipping backup.")
        return

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"friday_backup_{timestamp}.db"

    try:
        shutil.copy2(db_path, backup_path)
        # Apply strict 600 file permissions
        os.chmod(backup_path, 0o600)
        logger.info(f"Successfully created database backup: {backup_path.name}")
    except Exception as e:
        logger.error(f"Failed to copy database backup: {e}")
        return

    # Automatically purge backups older than 7 days
    try:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
        for backup_file in backup_dir.glob("friday_backup_*.db"):
            mtime = datetime.datetime.fromtimestamp(backup_file.stat().st_mtime)
            if mtime < cutoff:
                backup_file.unlink()
                logger.info(f"Purged old backup file: {backup_file.name}")
    except Exception as e:
        logger.error(f"Failed to purge old database backups: {e}")

def secure_environment_files() -> None:
    """Applies strict file permissions (chmod 600) to sensitive environment and credential files."""
    sensitive_files = [
        settings.BASE_DIR / ".env",
        settings.BASE_DIR / "token.json",
        settings.BASE_DIR / "credentials.json",
        Path(settings.DATABASE_URL.replace("sqlite:///", ""))
    ]

    for file_path in sensitive_files:
        if file_path.exists():
            try:
                os.chmod(file_path, 0o600)
                logger.debug(f"Applied chmod 600 permission to {file_path.name}")
            except Exception as e:
                logger.error(f"Failed to secure permissions for {file_path.name}: {e}")
