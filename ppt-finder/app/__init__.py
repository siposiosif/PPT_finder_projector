import os
import logging
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from app.core.config import RESOURCE_DIR
from app.routes import main_bp, indexer


def create_app():
    """Application factory for PPT Finder Flask server."""
    # Setup template and static folders
    template_dir = os.path.join(RESOURCE_DIR, 'templates')
    static_dir = os.path.join(RESOURCE_DIR, 'static')

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
        static_url_path='/static'
    )
    
    # Silence default verbose werkzeug access logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    app.register_blueprint(main_bp)
    app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024
    app.json.ensure_ascii = False

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            return jsonify(success=False, error=error.description), error.code
        return error

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception('Request failed: %s', request.path)
        if request.path.startswith('/api/'):
            return jsonify(success=False, error='Operația nu a reușit. Reîncercați; proiecția curentă a fost păstrată.'), 500
        return 'Pagina nu poate fi încărcată. Consultați jurnalul aplicației.', 500

    @app.after_request
    def no_stale_state(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    # Initial index load
    indexer.load_index()

    return app
