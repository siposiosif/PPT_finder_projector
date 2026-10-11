import os
import sys
import socket
import logging
import tempfile
import threading

# Determine base app directory
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIG_FILE = os.path.join(APP_DIR, 'config.txt')
INDEX_FILE = os.path.join(APP_DIR, 'ppt_index.json')
BIBLE_DIR = os.path.join(APP_DIR, 'open-bibles-master')
DEFAULT_BIBLE_FILE = os.path.join(BIBLE_DIR, 'ron-rccv.usfx.xml')
CACHE_DIR = os.path.join(APP_DIR, '.cache')
RESOURCE_DIR = getattr(sys, '_MEIPASS', APP_DIR)
CONFIG_LOCK = threading.RLock()
DEFAULT_UI_LANGUAGE = 'ro'

os.makedirs(CACHE_DIR, exist_ok=True)

logger = logging.getLogger(__name__)


def get_ui_language():
    """Read the shared UI language without treating settings as folder paths."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as config_file:
            for raw_line in config_file:
                key, separator, value = raw_line.strip().partition('=')
                if separator and key.strip().lower() == 'language':
                    language = value.strip().lower()
                    return language if language in {'ro', 'en'} else DEFAULT_UI_LANGUAGE
    except OSError:
        pass
    return DEFAULT_UI_LANGUAGE


def load_config():
    """Load indexed folder paths from config.txt."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                paths = [
                    line.strip() for line in f
                    if line.strip() and not line.strip().lower().startswith('language=')
                ]
                return paths
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            
    default_paths = [os.path.join(APP_DIR, 'Cântări Comun')]
    save_config(default_paths)
    return default_paths


def save_config(paths):
    """Save folder paths to config.txt."""
    try:
        with CONFIG_LOCK:
            with tempfile.NamedTemporaryFile('w', dir=APP_DIR, delete=False, encoding='utf-8') as f:
                temp_path = f.name
                f.write(f'language={get_ui_language()}\n')
                for path in paths:
                    f.write(path + "\n")
            os.replace(temp_path, CONFIG_FILE)
        return True
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        if 'temp_path' in locals():
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        return False


def is_safe_path(path):
    """Validate that a requested file is strictly below an indexed folder."""
    if not path:
        return False
    try:
        norm_target = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        allowed_dirs = [os.path.normcase(os.path.realpath(os.path.abspath(d))) for d in load_config()]
        
        for allowed in allowed_dirs:
            try:
                if os.path.commonpath([norm_target, allowed]) == allowed and norm_target != allowed:
                    return True
            except ValueError:
                continue  # Different drives are not descendants of one another.
        return False
    except Exception:
        return False


def get_local_ip():
    """Resolve the primary local network IPv4 address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        return ip
    except Exception:
        return '127.0.0.1'
