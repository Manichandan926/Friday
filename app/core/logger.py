import logging
from logging.handlers import RotatingFileHandler
from app.core.config import settings

def setup_logger(name: str = "friday") -> logging.Logger:
    logger = logging.getLogger(name)
    
    # If handlers already configured, return it (avoids duplicate logging)
    if logger.handlers:
        return logger
        
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(log_level)
    
    # Define a clean logging format
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s:%(filename)s:%(lineno)d]: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Rotating File Handler (limits size to 5MB, keeps up to 3 backups)
    log_file = settings.LOGS_DIR / "friday.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

# Primary logger instance for the application
logger = setup_logger()
