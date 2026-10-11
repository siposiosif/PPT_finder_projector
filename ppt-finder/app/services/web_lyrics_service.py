import html
import json
import logging
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from app.core.config import CACHE_DIR

logger = logging.getLogger(__name__)
SONGS_CACHE_DIR = os.path.join(CACHE_DIR, 'songs')
os.makedirs(SONGS_CACHE_DIR, exist_ok=True)


class WebLyricsUnavailable(RuntimeError):
    """The external lyrics service did not return a usable response."""


class WebLyricsService:
    """Bounded Resurse Creștine client with validated, atomic local caching."""

    def __init__(self):
        self.headers = {'User-Agent': 'PPT-Finder/2.0 (+local presentation tool)'}
        self.network_slots = threading.BoundedSemaphore(4)

    def _json_request(self, url, timeout):
        request = urllib.request.Request(url, headers=self.headers)
        try:
            with self.network_slots:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise WebLyricsUnavailable('Răspunsul serviciului online este prea mare.')
            payload = json.loads(raw.decode('utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('response is not an object')
            return payload
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise WebLyricsUnavailable('Serviciul Resurse Creștine nu răspunde corect.') from error

    def search(self, query):
        query = (query or '').strip()
        if not query:
            return []
        params = urllib.parse.urlencode({
            'search_text': query, 'search_in': '2',
            'search_by': 'filtru-titlu', 'output': 'json2',
        })
        payload = self._json_request(
            f'https://www.resursecrestine.ro/web-api-search?{params}', timeout=4
        )
        results = payload.get('Results')
        if not isinstance(results, list):
            raise WebLyricsUnavailable('Serviciul online a trimis un format necunoscut.')
        formatted = []
        for item in results[:12]:
            if not isinstance(item, dict):
                continue
            song_id = str(item.get('id', '')).strip()
            if not song_id.isdigit():
                continue
            slug = self._path_part(item.get('slug', 'cantece'), 'slug')
            title_slug = self._path_part(item.get('title_slug', ''), 'title_slug', allow_empty=True)
            formatted.append({
                'id': song_id,
                'title': html.unescape(str(item.get('title') or 'Necunoscut')),
                'title_slug': title_slug,
                'author': html.unescape(str(item.get('author') or 'Anonim')),
                'slug': slug,
                'url': f'https://www.resursecrestine.ro/{slug}/{song_id}/{title_slug}',
            })
        return formatted

    @staticmethod
    def _path_part(value, field, allow_empty=False):
        value = str(value or '').strip()
        if allow_empty and not value:
            return ''
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', value):
            raise ValueError(f'{field} invalid')
        return value

    @staticmethod
    def _valid_slides(value):
        if not isinstance(value, list) or len(value) > 2000:
            return None
        slides = []
        for item in value:
            if not isinstance(item, dict):
                return None
            label, text = item.get('label'), item.get('text')
            if not isinstance(label, str) or not isinstance(text, str):
                return None
            slides.append({'label': label[:1000], 'text': text[:20000]})
        return slides or None

    def _read_cache(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as source:
                return self._valid_slides(json.load(source))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, path, slides):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                'w', dir=SONGS_CACHE_DIR, delete=False, encoding='utf-8'
            ) as target:
                temporary = target.name
                json.dump(slides, target, ensure_ascii=False, separators=(',', ':'))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        except OSError:
            logger.warning('Lyrics cache could not be saved: %s', path, exc_info=True)
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @staticmethod
    def _clean_fragment(fragment):
        fragment = re.sub(r'<br\s*/?>', '\n', fragment, flags=re.IGNORECASE)
        fragment = re.sub(r'<[^>]+>', '', fragment)
        return html.unescape(fragment).replace('\r', '\n').strip()

    def _parse_lyrics(self, html_body):
        slides = []
        labels = re.findall(
            r'<div[^>]*class=["\']strofa-label["\'][^>]*>(.*?)</div>',
            html_body, re.DOTALL | re.IGNORECASE,
        )
        texts = re.findall(
            r'<div[^>]*class=["\']strofa-text["\'][^>]*>(.*?)</div>',
            html_body, re.DOTALL | re.IGNORECASE,
        )
        if labels and len(labels) == len(texts):
            for label, text in zip(labels, texts):
                slides.append({'label': self._clean_fragment(label), 'text': self._clean_fragment(text)})
        if slides:
            return slides
        match = re.search(
            r'<div[^>]*class=["\']resized-text["\'][^>]*>(.*?)</div>',
            html_body, re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return []
        for index, part in enumerate(
            re.split(r'(?:<br\s*/?>\s*){2,}', match.group(1), flags=re.IGNORECASE), 1
        ):
            text = self._clean_fragment(part)
            if not text:
                continue
            lines = text.split('\n')
            first = lines[0].strip()
            if re.match(r'^(?:Refren|R\d*|Chorus|Strofă|\d+\.|\d+)\b', first, re.IGNORECASE):
                label, text = first, '\n'.join(lines[1:]).strip()
            else:
                label = f'Strofa {index}'
            slides.append({'label': label, 'text': text})
        return slides

    def get_lyrics(self, song_id, slug='cantece', title_slug=''):
        song_id = str(song_id or '').strip()
        if not song_id.isdigit():
            raise ValueError('id invalid')
        slug = self._path_part(slug, 'slug')
        title_slug = self._path_part(title_slug, 'title_slug', allow_empty=True)
        cache_file = os.path.join(SONGS_CACHE_DIR, f'{song_id}.json')
        cached = self._read_cache(cache_file)
        if cached:
            return cached
        path = '/'.join(urllib.parse.quote(part, safe='') for part in (slug, song_id, title_slug))
        request = urllib.request.Request(f'https://www.resursecrestine.ro/{path}', headers=self.headers)
        try:
            with self.network_slots:
                with urllib.request.urlopen(request, timeout=5) as response:
                    raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise WebLyricsUnavailable('Pagina cântecului este prea mare.')
            body = raw.decode('utf-8')
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as error:
            raise WebLyricsUnavailable('Versurile nu sunt disponibile online și nu există în cache.') from error
        slides = self._valid_slides(self._parse_lyrics(body))
        if not slides:
            raise WebLyricsUnavailable('Pagina online nu conține versuri într-un format recunoscut.')
        self._write_cache(cache_file, slides)
        return slides


web_lyrics_service = WebLyricsService()
