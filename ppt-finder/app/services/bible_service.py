import os
import re
import html
import logging
import threading
from collections import defaultdict
from app.core.config import BIBLE_DIR, DEFAULT_BIBLE_FILE
from app.core.search_engine import normalize_text

logger = logging.getLogger(__name__)


class BibleVersion:
    """Represents a single parsed and indexed Bible translation."""

    def __init__(self, file_path, name, lang):
        self.file_path = file_path
        self.name = name
        self.lang = lang
        self.verses = []
        self.ref_map = {}               # (norm_book, chapter_str, verse_int) -> verse_dict
        self.word_index = defaultdict(set)  # norm_word -> set of verse indices
        self.books = []
        self.loaded = False

    def load(self):
        if self.loaded:
            return True
        if not os.path.exists(self.file_path):
            return False

        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            verses = []
            if self.file_path.endswith('.usfx.xml'):
                verses = self._parse_usfx(content)
            elif self.file_path.endswith('.osis.xml'):
                verses = self._parse_osis(content)
            elif self.file_path.endswith('.zefania.xml'):
                verses = self._parse_zefania(content)
            else:
                verses = self._parse_usfx(content)

            self.verses = verses
            self.ref_map.clear()
            self.word_index.clear()
            seen_books = []

            for idx, v in enumerate(self.verses):
                b_name = v['book']
                if b_name not in seen_books:
                    seen_books.append(b_name)
                
                norm_b = normalize_text(b_name)
                norm_b_id = normalize_text(v.get('book_id', ''))
                try:
                    c_int = int(v['chapter'])
                    v_int = int(v['verse'])
                    self.ref_map[(norm_b, c_int, v_int)] = v
                    if norm_b_id:
                        self.ref_map[(norm_b_id, c_int, v_int)] = v
                except ValueError:
                    pass

                for w in v['norm_text'].split():
                    if len(w) > 2:
                        self.word_index[w].add(idx)

            self.books = seen_books
            self.loaded = True
            logger.info(f"Loaded Bible '{self.name}': {len(self.verses)} verses, {len(self.books)} books.")
            return True
        except Exception as e:
            logger.error(f"Failed to parse Bible '{self.file_path}': {e}")
            return False

    def _parse_usfx(self, xml):
        verses = []
        book_pattern = re.compile(r'<book id="([^"]+)"[^>]*>.*?<h>(.*?)</h>(.*?)(?=<book id=|</usfx>)', re.DOTALL)
        event_pattern = re.compile(r'<c id="([^"]+)"\s*/>|<v id="([^"]+)"\s*/>')

        for book_match in book_pattern.finditer(xml):
            book_id, book_name, body = book_match.groups()
            book_name = html.unescape(re.sub(r'<[^>]+>', '', book_name)).strip()
            chapter = None
            events = list(event_pattern.finditer(body))
            for index, event in enumerate(events):
                chapter_id, verse_id = event.groups()
                if chapter_id:
                    chapter = chapter_id
                    continue
                if not verse_id or not chapter:
                    continue
                next_start = events[index + 1].start() if index + 1 < len(events) else len(body)
                text = html.unescape(re.sub(r'<[^>]+>', ' ', body[event.end():next_start]))
                text = ' '.join(text.split())
                if text:
                    verses.append({
                        'book': book_name,
                        'book_id': book_id,
                        'chapter': str(chapter),
                        'verse': str(verse_id),
                        'text': text,
                        'norm_text': normalize_text(text),
                        'ref': f"{book_name} {chapter}:{verse_id}"
                    })
        return verses

    def _parse_osis(self, xml):
        verses = []
        verse_pattern = re.compile(r'<verse osisID="([^"]+)"[^>]*>(.*?)</verse>', re.DOTALL)
        for m in verse_pattern.finditer(xml):
            osis_id, body = m.groups()
            text = html.unescape(re.sub(r'<[^>]+>', ' ', body))
            text = ' '.join(text.split())
            if not text:
                continue

            parts = osis_id.split('.')
            if len(parts) >= 3:
                book_id = parts[0]
                chapter = parts[1]
                verse = parts[2]
                verses.append({
                    'book': book_id,
                    'book_id': book_id,
                    'chapter': str(chapter),
                    'verse': str(verse),
                    'text': text,
                    'norm_text': normalize_text(text),
                    'ref': f"{book_id} {chapter}:{verse}"
                })
        return verses

    def _parse_zefania(self, xml):
        verses = []
        book_pattern = re.compile(r'<BIBLEBOOK\s+bname="([^"]+)"[^>]*>(.*?)</BIBLEBOOK>', re.DOTALL | re.IGNORECASE)
        chap_pattern = re.compile(r'<CHAPTER\s+cnumber="([^"]+)"[^>]*>(.*?)</CHAPTER>', re.DOTALL | re.IGNORECASE)
        verse_pattern = re.compile(r'<VERS\s+vnumber="([^"]+)"[^>]*>(.*?)</VERS>', re.DOTALL | re.IGNORECASE)

        for b_match in book_pattern.finditer(xml):
            b_name, b_body = b_match.groups()
            for c_match in chap_pattern.finditer(b_body):
                c_num, c_body = c_match.groups()
                for v_match in verse_pattern.finditer(c_body):
                    v_num, v_text = v_match.groups()
                    text = html.unescape(re.sub(r'<[^>]+>', ' ', v_text))
                    text = ' '.join(text.split())
                    if text:
                        verses.append({
                            'book': b_name,
                            'book_id': b_name,
                            'chapter': str(c_num),
                            'verse': str(v_num),
                            'text': text,
                            'norm_text': normalize_text(text),
                            'ref': f"{b_name} {c_num}:{v_num}"
                        })
        return verses

    def find_verses(self, query, limit=12):
        query = (query or '').strip()
        if not query or not self.load():
            return []

        # 1. Check direct reference format (e.g. 'Ioan 3:16', '1 Corinteni 13:4-8', 'Gen 1:1')
        ref_match = re.match(r'^(.+?)\s+(\d+)\s*[:.,]\s*(\d+)(?:\s*-\s*(\d+))?$', query)
        if ref_match:
            book_query, chapter_str, first_verse_str, last_verse_str = ref_match.groups()
            c_num = int(chapter_str)
            first_v = int(first_verse_str)
            last_v = int(last_verse_str) if last_verse_str else first_v
            norm_b = normalize_text(book_query)

            # Match exact book or partial book name
            matched_verses = []
            for (bk, ch, vr), verse_obj in self.ref_map.items():
                if ch == c_num and first_v <= vr <= last_v:
                    if bk == norm_b or bk.startswith(norm_b) or norm_b in bk:
                        if verse_obj not in matched_verses:
                            matched_verses.append(verse_obj)

            if matched_verses:
                matched_verses.sort(key=lambda x: (int(x['chapter']), int(x['verse'])))
                return matched_verses[:limit]

        # 2. Fast inverted index text search
        words = [w for w in normalize_text(query).split() if len(w) > 2]
        if not words:
            return []

        # Find verse candidates that have matches
        candidate_indices = set()
        for w in words:
            if w in self.word_index:
                candidate_indices.update(self.word_index[w])
            else:
                # Prefix matching
                for iw, indices in self.word_index.items():
                    if iw.startswith(w) or w in iw:
                        candidate_indices.update(indices)

        scored = []
        norm_q = normalize_text(query)
        for idx in candidate_indices:
            verse = self.verses[idx]
            matches = sum(1 for w in words if w in verse['norm_text'])
            if matches:
                score = matches * 100
                if norm_q in verse['norm_text']:
                    score += 200
                scored.append((score, verse))

        scored.sort(key=lambda item: -item[0])
        return [verse for _, verse in scored[:limit]]

    def chapter_for_reference(self, reference):
        """Return the selected verse's full chapter and its zero-based position."""
        reference = (reference or '').strip()
        if not reference or not self.load():
            return [], 0
        match = re.match(r'^(.+?)\s+(\d+)\s*[:.,]\s*(\d+)', reference)
        if not match:
            return [], 0
        book_query, chapter_text, verse_text = match.groups()
        chapter_number = int(chapter_text)
        verse_number = int(verse_text)
        normalized_book = normalize_text(book_query)

        selected = None
        for (book_key, chapter, verse), verse_obj in self.ref_map.items():
            if chapter == chapter_number and verse == verse_number:
                if book_key == normalized_book or book_key.startswith(normalized_book) or normalized_book in book_key:
                    selected = verse_obj
                    break
        if not selected:
            return [], 0

        chapter_verses = [
            verse for verse in self.verses
            if verse['book'] == selected['book'] and int(verse['chapter']) == chapter_number
        ]
        chapter_verses.sort(key=lambda verse: int(verse['verse']))
        selected_index = next(
            (index for index, verse in enumerate(chapter_verses)
             if int(verse['verse']) == verse_number),
            0,
        )
        return chapter_verses, selected_index


class BibleService:
    """Manages available Bible translations."""

    def __init__(self):
        self.versions = {}
        self.default_version_key = "ron-rccv"
        self.lock = threading.Lock()
        self._discover_versions()

    def _discover_versions(self):
        """Scan open-bibles-master folder for available versions."""
        if not os.path.exists(BIBLE_DIR):
            return

        friendly_names = {
            'ron-rccv': 'Română (Cornilescu RCCV)',
            'eng-kjv': 'English (King James Version)',
            'eng-web': 'English (World English Bible)',
            'eng-bsb': 'English (Berean Standard Bible)',
            'hun-karoli': 'Magyar (Károli Gáspár)',
            'spa-rv1909': 'Español (Reina-Valera 1909)',
            'deu-luther1912': 'Deutsch (Luther 1912)',
            'fra-ostervald': 'Français (Ostervald)',
            'ita-riveduta': 'Italiano (Riveduta)',
            'rus-synodal': 'Русский (Синодальный перевод)',
            'ukr': 'Українська'
        }

        for file_name in os.listdir(BIBLE_DIR):
            if file_name.endswith('.xml'):
                key = file_name.split('.')[0]
                name = friendly_names.get(key, key.replace('-', ' ').title())
                file_path = os.path.join(BIBLE_DIR, file_name)
                self.versions[key] = BibleVersion(file_path, name, key[:3])

        if 'ron-rccv' not in self.versions and os.path.exists(DEFAULT_BIBLE_FILE):
            self.versions['ron-rccv'] = BibleVersion(DEFAULT_BIBLE_FILE, 'Română (Cornilescu RCCV)', 'ron')

    def get_available_versions(self):
        """Return list of available Bible versions."""
        return [{
            "key": key,
            "name": bv.name,
            "lang": bv.lang
        } for key, bv in sorted(self.versions.items(), key=lambda x: (0 if x[0] == 'ron-rccv' else 1, x[1].name))]

    def get_version(self, key=None):
        """Get or lazily load specific Bible version."""
        if not key or key not in self.versions:
            key = self.default_version_key
        
        bv = self.versions.get(key)
        if bv:
            # Requests arriving while a translation loads must never see a
            # partially rebuilt reference/word index.
            with self.lock:
                if not bv.load():
                    return None
            return bv
        return None

    def search(self, query, version_key=None, limit=12):
        bv = self.get_version(version_key)
        if not bv:
            return []
        return bv.find_verses(query, limit=limit)

    def chapter(self, reference, version_key=None):
        bv = self.get_version(version_key)
        if not bv:
            return [], 0
        return bv.chapter_for_reference(reference)


# Singleton Bible service
bible_service = BibleService()
