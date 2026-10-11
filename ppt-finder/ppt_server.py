"""PPT Finder server/native-window launcher; voice recognition runs independently."""

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time
import webbrowser

from werkzeug.serving import make_server

from app import create_app
from app.core.config import APP_DIR, CACHE_DIR, get_local_ip
from app.routes import REMOTE_ACCESS_CODE, indexer

logger = logging.getLogger(__name__)


class DesktopBridge:
    """Open one independent audience window without taking over the operator window."""

    def __init__(self, url):
        self.url = url
        self.projector = None
        self.lock = threading.Lock()

    def open_projector(self):
        import webview
        with self.lock:
            if self.projector is None:
                window = webview.create_window(
                    'PPT Finder — Proiecție', self.url,
                    width=1280, height=720, min_size=(640, 360),
                    background_color='#000000', js_api=ProjectorBridge(),
                )
                window.events.closed += self._projector_closed
                self.projector = window
            else:
                self.projector.show()
        return {'success': True}

    def _projector_closed(self):
        with self.lock:
            self.projector = None


class ProjectorBridge:
    def toggle_fullscreen(self):
        import webview
        for window in webview.windows:
            if window.title == 'PPT Finder — Proiecție':
                window.toggle_fullscreen()
                return {'success': True}
        return {'success': False}


def configure_logging():
    handler = RotatingFileHandler(os.path.join(CACHE_DIR, 'ppt-finder.log'),
                                  maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def open_default_browser(url):
    """Open a URL through the Windows default browser association."""
    try:
        if os.name == 'nt':
            os.startfile(url)
            return True
        return bool(webbrowser.open_new(url))
    except Exception:
        logger.exception('Could not open the default browser at %s', url)
        return False


def open_browser_windows(operator_url, projector_url):
    """Open independent operator and audience views without popup blocking."""
    operator_opened = open_default_browser(operator_url)
    time.sleep(0.75)
    projector_opened = open_default_browser(projector_url)
    if operator_opened and projector_opened:
        logger.info('Opened operator and projector pages in the default browser.')
    elif operator_opened:
        logger.warning('Operator page opened, but the projector page did not.')
    else:
        logger.warning('The default browser did not accept the PPT Finder pages.')


def main():
    configure_logging()
    bind_host = os.environ.get('PPT_FINDER_BIND_HOST', '0.0.0.0')
    os.environ.setdefault('PPT_FINDER_COM_ENABLED', '1')
    try:
        port = int(os.environ.get('PPT_FINDER_PORT', '8080'))
        if not 1024 <= port <= 65535:
            raise ValueError('PPT_FINDER_PORT must be between 1024 and 65535.')
        flask_app = create_app()
        # Bind the real listener before creating a window; no port-selection race.
        server = make_server(bind_host, port, flask_app, threaded=True)
    except (Exception, SystemExit):
        logger.exception('PPT Finder could not start. Check the port and dependencies.')
        raise

    if not indexer.index_data:
        indexer.start_reindex()

    url = f'http://127.0.0.1:{port}'
    projector_url = f'{url}/projector?code={REMOTE_ACCESS_CODE}'
    logger.info('PPT Finder: %s (workspace %s)', url, APP_DIR)
    if bind_host not in ('127.0.0.1', 'localhost'):
        logger.info('Network: http://%s:%s', get_local_ip(), port)
    serving = threading.Thread(target=server.serve_forever, name='ppt-http', daemon=True)
    serving.start()

    try:
        if '--headless' in sys.argv:
            serving.join()
            return
        if '--native-window' not in sys.argv:
            threading.Thread(
                target=open_browser_windows,
                args=(url, projector_url),
                name='ppt-browser-opener',
                daemon=True,
            ).start()
            serving.join()
            return
        try:
            import webview
            bridge = DesktopBridge(projector_url)
            webview.create_window('PPT Finder', url, width=1440, height=960,
                                  resizable=True, min_size=(1000, 720), js_api=bridge)
            webview.start()
        except Exception:
            logger.exception('Native window unavailable; opening the operator console in a browser.')
            webbrowser.open(url)
            serving.join()
    except KeyboardInterrupt:
        logger.info('Server shutdown requested.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
