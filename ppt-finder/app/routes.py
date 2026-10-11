"""Operator, projector and remote APIs. Presentation changes are serialized."""

import json
import logging
import math
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from functools import wraps

from flask import Blueprint, Response, abort, jsonify, render_template, request
from werkzeug.exceptions import BadRequest

from app.core.config import CONFIG_LOCK, get_local_ip, get_ui_language, is_safe_path, load_config, save_config
from app.core.indexer import IndexManager, extract_ppt_text_carving, extract_pptx_slides
from app.core.ppt_controller import control_powerpoint_slideshow, get_powerpoint_slideshow_status, open_powerpoint_presentation
from app.services.bible_service import bible_service
from app.services.events_service import event_bus
from app.services.web_lyrics_service import WebLyricsUnavailable, web_lyrics_service

logger = logging.getLogger(__name__)
main_bp = Blueprint('main', __name__)
indexer = IndexManager()
indexer.on_state_change = lambda state: event_bus.publish('indexing_state', state)
projection_lock = threading.RLock()
projection_state = {
    'text': '', 'label': '', 'font_size': 7.0, 'blackout': False,
    'clear_text': False, 'timestamp': time.time(), 'revision': 0,
}
projection_session = {
    'active': False, 'title': '', 'current_slide_index': 0, 'slides': [], 'revision': 0,
}
REMOTE_ACCESS_CODE = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))


def com_enabled():
    return os.name == 'nt' and os.environ.get('PPT_FINDER_COM_ENABLED', '1') == '1'


def is_local_request():
    return request.remote_addr in {'127.0.0.1', '::1'}


def has_access_code():
    supplied = request.headers.get('X-PPT-Access-Code') or request.args.get('code', '')
    return bool(supplied) and secrets.compare_digest(supplied, REMOTE_ACCESS_CODE)


def requires_presentation_access(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not (is_local_request() or has_access_code()):
            abort(403, description='Cod de acces necesar.')
        return view(*args, **kwargs)
    return guarded


def json_object():
    data = request.get_json()
    if not isinstance(data, dict):
        raise BadRequest('Corpul cererii trebuie să fie un obiect JSON.')
    return data


def text_value(value, field, max_length=20000):
    if not isinstance(value, str) or len(value) > max_length:
        raise BadRequest(f'{field}: text invalid sau prea lung.')
    return value


def number_value(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BadRequest(f'{field}: număr invalid.')
    return float(value)


def integer_value(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest(f'{field}: număr întreg necesar.')
    return value


def query_value(key='q'):
    return text_value(request.args.get(key, ''), key, 500).strip()


def validated_file(path):
    path = text_value(path, 'path', 32767)
    if not path:
        abort(400, description='Calea fișierului este necesară.')
    # Flask already decoded query parameters. A second unquote corrupts % in filenames.
    if not is_safe_path(path):
        abort(403, description='Fișierul nu aparține unui folder configurat.')
    if not path.lower().endswith(('.ppt', '.pptx')):
        abort(400, description='Selectați un fișier PPT sau PPTX.')
    if not os.path.isfile(path):
        abort(404, description='Fișierul nu mai este disponibil. Verificați folderul sau unitatea.')
    return os.path.abspath(path)


def web_status_locked():
    active = projection_session['active']
    return {
        'running': active, 'slideshow_active': active,
        'mode': 'projection' if active else 'none', 'title': projection_session['title'],
        'total_slides': len(projection_session['slides']),
        'current_slide_index': projection_session['current_slide_index'] + 1 if active else 0,
        'slides': list(projection_session['slides']), 'font_size': projection_state['font_size'],
        'blackout': projection_state['blackout'], 'clear_text': projection_state['clear_text'],
        'revision': projection_state['revision'],
    }


def publish_projection_locked():
    projection_state['revision'] += 1
    projection_state['timestamp'] = time.time()
    projection_session['revision'] = projection_state['revision']
    event_bus.publish('projection_state', dict(projection_state))
    event_bus.publish('projection_session', dict(projection_session))
    # Never wait for Office while holding the web projector lock.
    event_bus.publish('remote_status', web_status_locked())


def select_slide_locked(index):
    slide = projection_session['slides'][index]
    projection_session['current_slide_index'] = index
    projection_state.update(text=slide['text'], label=slide['label'])
    # Keep blackout/hidden state until the operator explicitly restores it.
    publish_projection_locked()


def clear_projection_locked():
    projection_session.update(active=False, title='', current_slide_index=0, slides=[])
    projection_state.update(text='', label='', blackout=False, clear_text=False)
    publish_projection_locked()


@main_bp.route('/')
def home():
    if not is_local_request():
        abort(403)
    port = request.environ.get('SERVER_PORT', '8080')
    local_only = os.environ.get('PPT_FINDER_BIND_HOST', '0.0.0.0') in ('127.0.0.1', 'localhost')
    host = '127.0.0.1' if local_only else get_local_ip()
    network_url = f'http://{host}:{port}'
    ui_language = get_ui_language()
    return render_template(
        'index.html', access_label='Local URL' if local_only else 'Network URL',
        network_url=network_url, local_ip=host, port=port, access_code=REMOTE_ACCESS_CODE,
        remote_url=f'{network_url}/remote?code={REMOTE_ACCESS_CODE}',
        projector_url=f'{network_url}/projector?code={REMOTE_ACCESS_CODE}',
        bible_versions=bible_service.get_available_versions(), com_enabled=com_enabled(), ui_language=ui_language,
        voice_url=os.environ.get('PPT_FINDER_VOICE_URL', 'http://127.0.0.1:8765'),
    )


@main_bp.route('/projector')
@requires_presentation_access
def projector_page():
    return render_template('projector.html', access_code=REMOTE_ACCESS_CODE, ui_language=get_ui_language())


@main_bp.route('/remote')
@requires_presentation_access
def remote_page():
    return render_template('remote.html', access_code=REMOTE_ACCESS_CODE, ui_language=get_ui_language())


@main_bp.route('/api/health')
def health_api():
    return jsonify(service='ppt-finder', ready=True, mode='server' if com_enabled() else 'desktop')


@main_bp.route('/api/events')
@requires_presentation_access
def sse_events():
    def event_stream():
        with projection_lock:
            listener = event_bus.subscribe()
            initial = [('projection_state', dict(projection_state)),
                       ('projection_session', dict(projection_session)),
                       ('remote_status', web_status_locked()),
                       ('indexing_state', indexer.get_state())]
        try:
            for kind, data in initial:
                payload = json.dumps({'type': kind, 'data': data, 'timestamp': time.time()}, ensure_ascii=False)
                yield f'event: {kind}\ndata: {payload}\n\n'
            while True:
                try:
                    message = listener.get(timeout=15)
                    if message is None:
                        return
                    yield message
                except queue.Empty:
                    yield ': keepalive\n\n'
        finally:
            event_bus.unsubscribe(listener)
    return Response(event_stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@main_bp.route('/api/folders', methods=['GET', 'POST', 'DELETE'])
@requires_presentation_access
def folders_api():
    with CONFIG_LOCK:
        current = load_config()
        if request.method == 'GET':
            return jsonify(current)
        if request.method == 'POST':
            new_path = text_value(json_object().get('path'), 'path', 32767).strip()
            if not new_path or '\n' in new_path or '\r' in new_path or not os.path.isdir(new_path):
                abort(400, description='Folderul nu există sau nu este accesibil.')
            new_path = os.path.abspath(new_path)
            if os.path.normcase(new_path) not in {os.path.normcase(p) for p in current}:
                current.append(new_path)
        else:
            try:
                index = int(request.args.get('index', ''))
            except ValueError:
                abort(400, description='Index de folder invalid.')
            if not 0 <= index < len(current):
                abort(400, description='Folderul selectat nu mai există.')
            current.pop(index)
        if not save_config(current):
            abort(500, description='Configurația nu a putut fi salvată.')
        return jsonify(success=True)


@main_bp.route('/api/status')
@requires_presentation_access
def status_api():
    return jsonify(indexer.get_state())


@main_bp.route('/api/reindex', methods=['POST'])
@requires_presentation_access
def reindex_api():
    started = indexer.start_reindex()
    return jsonify(success=True, status='started' if started else 'already indexing')


@main_bp.route('/api/search')
@requires_presentation_access
def search_api():
    results, is_numeric = indexer.search(query_value(), limit=100)
    return jsonify(results=[item for item in results if is_safe_path(item.get('path'))], is_numeric=is_numeric)


@main_bp.route('/api/bible/versions')
@requires_presentation_access
def bible_versions_api():
    return jsonify(bible_service.get_available_versions())


@main_bp.route('/api/bible/search')
@requires_presentation_access
def bible_search_api():
    results = bible_service.search(query_value(), version_key=request.args.get('version'), limit=12)
    return jsonify([{'ref': verse['ref'], 'text': verse['text']} for verse in results])


@main_bp.route('/api/bible/verses')
@requires_presentation_access
def bible_verses_api():
    results = bible_service.search(query_value('ref'), version_key=request.args.get('version'), limit=12)
    return jsonify([{'label': verse['ref'], 'text': verse['text']} for verse in results])


@main_bp.route('/api/voice/verse-detected', methods=['POST'])
def voice_verse_detected_api():
    if not is_local_request():
        abort(403, description='Acceptat doar de pe acest calculator.')
    data = json_object()
    reference = text_value(data.get('reference', ''), 'reference', 500).strip()
    text = text_value(data.get('text', ''), 'text', 10000).strip()
    url = text_value(data.get('url', ''), 'url', 1000).strip()
    if not reference:
        abort(400, description='Referința este necesară.')

    if not text:
        local_results = bible_service.search(reference, limit=1)
        if local_results:
            text = local_results[0].get('text', '')

    payload = {
        'reference': reference,
        'text': text,
        'url': url,
        'timestamp': data.get('timestamp', time.time()),
        'source': 'voice',
    }
    event_bus.publish('verse_popup', payload)
    return jsonify(success=True)


@main_bp.route('/api/bible/chapter')
@requires_presentation_access
def bible_chapter_api():
    verses, selected_index = bible_service.chapter(
        query_value('ref'), version_key=request.args.get('version')
    )
    if not verses:
        abort(404, description='Capitolul biblic nu a fost găsit.')
    return jsonify(
        slides=[{'label': verse['ref'], 'text': verse['text']} for verse in verses],
        current_slide_index=selected_index,
    )


@main_bp.route('/api/search/web')
@requires_presentation_access
def web_search_api():
    try:
        return jsonify(web_lyrics_service.search(query_value()))
    except WebLyricsUnavailable as error:
        abort(503, description=str(error))


@main_bp.route('/api/lyrics/web')
@requires_presentation_access
def web_lyrics_api():
    try:
        slides = web_lyrics_service.get_lyrics(
            query_value('id'), request.args.get('slug', 'cantece'), query_value('title_slug')
        )
    except ValueError:
        abort(400, description='Identificator de cântec invalid.')
    except WebLyricsUnavailable as error:
        abort(503, description=str(error))
    if not slides:
        abort(502, description='Versurile nu sunt disponibile online sau în cache. Proiecția curentă a fost păstrată.')
    return jsonify(slides)


@main_bp.route('/api/slides/local')
@requires_presentation_access
def local_slides_api():
    path = validated_file(request.args.get('path', ''))
    doc = indexer.get_document(path)
    if doc and doc.get('slides') and doc.get('mtime') == os.path.getmtime(path):
        return jsonify(doc['slides'])
    if path.lower().endswith('.pptx'):
        slides = extract_pptx_slides(path)
    else:
        slides = [part.strip() for part in extract_ppt_text_carving(path).split('\n\n') if part.strip()]
    formatted = [slide if isinstance(slide, dict) else {'label': f'Slide {i + 1}', 'text': slide}
                 for i, slide in enumerate(slides)]
    if not formatted:
        abort(422, description='Nu există text extractibil. Deschideți prezentarea în aplicația implicită pentru aspectul original.')
    return jsonify(formatted)


@main_bp.route('/api/projection/current', methods=['GET', 'POST'])
@requires_presentation_access
def projection_current_api():
    if request.method == 'GET':
        with projection_lock:
            return jsonify(dict(projection_state))
    data = json_object()
    updates = {}
    for key in ('text', 'label'):
        if key in data:
            updates[key] = text_value(data[key], key)
    if 'font_size' in data:
        updates['font_size'] = max(3.0, min(15.0, number_value(data['font_size'], 'font_size')))
    for key in ('blackout', 'clear_text'):
        if key in data:
            if not isinstance(data[key], bool):
                raise BadRequest(f'{key}: valoare booleană necesară.')
            updates[key] = data[key]
    with projection_lock:
        projection_state.update(updates)
        if 'text' in updates or 'label' in updates:
            projection_session.update(active=True, title=projection_state['label'], current_slide_index=0,
                                      slides=[{'label': projection_state['label'], 'text': projection_state['text']}])
        publish_projection_locked()
        return jsonify(success=True, state=dict(projection_state))


@main_bp.route('/api/projection/session', methods=['GET', 'POST'])
@requires_presentation_access
def projection_session_api():
    if request.method == 'GET':
        with projection_lock:
            return jsonify(dict(projection_session))
    data = json_object()
    title = text_value(data.get('title', 'Proiecție'), 'title', 1000)
    initial_index = integer_value(data.get('current_slide_index', 0), 'current_slide_index')
    raw_slides = data.get('slides')
    if not isinstance(raw_slides, list) or not 1 <= len(raw_slides) <= 2000:
        raise BadRequest('Prezentarea trebuie să conțină între 1 și 2000 de diapozitive.')
    slides = []
    for i, slide in enumerate(raw_slides):
        if not isinstance(slide, dict):
            raise BadRequest('Diapozitiv invalid.')
        slides.append({'label': text_value(slide.get('label', f'Slide {i + 1}'), 'label', 1000),
                       'text': text_value(slide.get('text', ''), 'text')})
    if not 0 <= initial_index < len(slides):
        raise BadRequest('Poziția inițială a diapozitivului este invalidă.')
    with projection_lock:
        projection_session.update(active=True, title=title, current_slide_index=initial_index, slides=slides)
        select_slide_locked(initial_index)
        return jsonify(success=True, session=dict(projection_session))


@main_bp.route('/api/projection/session/current', methods=['POST'])
@requires_presentation_access
def projection_session_current_api():
    index = integer_value(json_object().get('current_slide_index'), 'current_slide_index')
    with projection_lock:
        if not projection_session['active']:
            abort(409, description='Nicio proiecție web activă.')
        if not 0 <= index < len(projection_session['slides']):
            abort(400, description='Diapozitiv inexistent.')
        select_slide_locked(index)
        return jsonify(success=True, current_slide_index=index, revision=projection_state['revision'])


@main_bp.route('/api/projection/session/clear', methods=['POST'])
@requires_presentation_access
def projection_session_clear_api():
    with projection_lock:
        clear_projection_locked()
    return jsonify(success=True)


@main_bp.route('/api/open', methods=['GET', 'POST'])
@requires_presentation_access
def open_file_api():
    data = json_object() if request.method == 'POST' else request.args
    path = validated_file(data.get('path', ''))
    mode = data.get('mode', 'default')
    if mode == 'powerpoint':
        if not com_enabled():
            abort(409, description='Controlul COM este disponibil în varianta server pentru Windows.')
        success, message = open_powerpoint_presentation(path)
        if success:
            # Remote controls must follow the explicitly selected native deck,
            # rather than a previously prepared web projection.
            with projection_lock:
                if projection_session['active']:
                    clear_projection_locked()
        return jsonify(success=success, message=message if success else None,
                       error=message if not success else None, mode=mode), 200 if success else 503
    if mode != 'default':
        abort(400, description='Mod de deschidere invalid.')
    try:
        if os.name == 'nt':
            os.startfile(path)
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        logger.exception('Could not open presentation in its default application')
        abort(503, description='Fișierul nu poate fi deschis. Verificați aplicația implicită pentru PPT/PPTX.')
    return jsonify(success=True, mode='default', message='Cererea de deschidere a fost trimisă aplicației implicite.')


@main_bp.route('/api/reveal', methods=['GET', 'POST'])
@requires_presentation_access
def reveal_file_api():
    data = json_object() if request.method == 'POST' else request.args
    path = validated_file(data.get('path', ''))
    try:
        if os.name == 'nt':
            subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', os.path.dirname(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        abort(503, description='Folderul nu poate fi deschis.')
    return jsonify(success=True)


def get_unified_remote_status():
    with projection_lock:
        status = web_status_locked()
    if status['running'] or not com_enabled():
        return status
    native = get_powerpoint_slideshow_status()
    # COM may have been slow; a newly started web session always takes priority.
    with projection_lock:
        if projection_session['active']:
            return web_status_locked()
    if native.get('running'):
        native.update(mode='powerpoint', font_size=7.0, clear_text=False)
        return native
    if native.get('error'):
        status['native_error'] = native['error']
    return status


@main_bp.route('/api/remote/status')
@requires_presentation_access
def remote_status_api():
    return jsonify(get_unified_remote_status())


@main_bp.route('/api/remote/control', methods=['POST'])
@requires_presentation_access
def remote_control_api():
    data = json_object()
    action = data.get('action')
    allowed = ('next', 'back', 'prev', 'goto', 'stop', 'start', 'close', 'blackout_toggle', 'clear_toggle', 'font_adjust')
    if action not in allowed:
        raise BadRequest('Acțiune necunoscută.')
    slide_index = integer_value(data.get('slide_index'), 'slide_index') if action == 'goto' else None
    delta = number_value(data.get('delta', 0.5), 'delta') if action == 'font_adjust' else None
    with projection_lock:
        if projection_session['active']:
            if action in ('blackout_toggle', 'clear_toggle', 'font_adjust'):
                if action == 'font_adjust':
                    projection_state['font_size'] = max(3.0, min(15.0, projection_state['font_size'] + delta))
                else:
                    key = 'blackout' if action == 'blackout_toggle' else 'clear_text'
                    projection_state[key] = not projection_state[key]
                publish_projection_locked()
                return jsonify(success=True, **dict(projection_state))
            if action in ('stop', 'close'):
                clear_projection_locked()
                return jsonify(success=True)
            if action == 'start':
                return jsonify(success=True)
            current = projection_session['current_slide_index']
            index = slide_index - 1 if action == 'goto' else current + (1 if action == 'next' else -1)
            if not 0 <= index < len(projection_session['slides']):
                abort(409, description='Ați ajuns la începutul sau sfârșitul prezentării.')
            select_slide_locked(index)
            return jsonify(success=True, current_slide_index=index + 1, revision=projection_state['revision'])
    if not com_enabled():
        abort(409, description='Nicio proiecție web activă.')
    if action in ('clear_toggle', 'font_adjust'):
        abort(409, description='Această comandă este disponibilă numai pentru proiecția text.')
    success, message = control_powerpoint_slideshow(action, slide_index)
    if success:
        event_bus.publish('remote_status', get_unified_remote_status())
    return jsonify(success=success, message=message if success else None,
                   error=message if not success else None), 200 if success else 503
