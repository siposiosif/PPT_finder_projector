import copy
import json
import logging
import math
import os
import posixpath
import re
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree

from app.core.config import INDEX_FILE, load_config
from app.core.search_engine import normalize_text, SearchIndex

logger = logging.getLogger(__name__)

# Invalidate older slide caches that omitted blank/image-only slides.
EXTRACTION_VERSION = 2


def _normalize_slide_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\v", "\n")


class IndexManager:
    """Thread-safe file-only indexer that never opens or closes the audience's app."""

    def __init__(self):
        self.index_data = {}
        self.lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.search_index = SearchIndex()
        self.state = {
            "status": "idle",
            "text": "Index idle",
            "progress": 0.0,
            "total_files": 0,
        }
        self.state_lock = threading.Lock()
        self._indexing = False
        self.on_state_change = None

    def set_state(self, status, text, progress, total_files=None):
        with self.state_lock:
            self.state.update(status=status, text=text, progress=progress)
            if total_files is not None:
                self.state["total_files"] = total_files
            current_state = dict(self.state)
        if self.on_state_change:
            try:
                self.on_state_change(current_state)
            except Exception:
                logger.debug("State change callback error", exc_info=True)

    def get_state(self):
        with self.lock:
            total_count = len(self.index_data)
        with self.state_lock:
            state = dict(self.state)
        if state["status"] == "idle":
            state["total_files"] = total_count
            state["text"] = f"Index idle. Total: {total_count} files."
        return state

    def load_index(self):
        """Build a validated replacement before swapping the live search index."""
        if not os.path.isfile(INDEX_FILE):
            return False
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as source:
                raw = json.load(source)
            if not isinstance(raw, dict):
                raise ValueError("Index cache must contain a document mapping")
            documents = {}
            for path, value in raw.items():
                if not isinstance(path, str) or not path or not isinstance(value, dict):
                    logger.warning("Skipping malformed index entry")
                    continue
                name = value.get("name")
                name = name if isinstance(name, str) else os.path.basename(path)
                title = value.get("title")
                title = title if isinstance(title, str) else os.path.splitext(name)[0]
                content = value.get("content", "")
                content = content if isinstance(content, str) else ""
                content = _normalize_slide_text(content)
                mtime = value.get("mtime", 0)
                if not isinstance(mtime, (int, float)) or not math.isfinite(mtime):
                    mtime = 0
                slides = value.get("slides", [])
                version = value.get("extraction_version", 0)
                if version != EXTRACTION_VERSION and path.lower().endswith(".pptx"):
                    # Existing text remains searchable while fresh, correctly
                    # numbered slide previews are generated on demand/reindex.
                    slides = []
                if not isinstance(slides, list) or any(
                    not isinstance(slide, dict) or not isinstance(slide.get("text"), str)
                    or not isinstance(slide.get("label", ""), str) for slide in slides
                ):
                    slides = []
                    version = 0
                slides = [
                    {"label": slide.get("label", f"Slide {idx + 1}"), "text": _normalize_slide_text(slide["text"])}
                    for idx, slide in enumerate(slides)
                ]
                documents[path] = {
                    "path": path, "name": name, "title": title,
                    "norm_title": normalize_text(title), "mtime": mtime,
                    "content": content, "norm_content": normalize_text(content),
                    "slides": slides, "extraction_version": version,
                }
            replacement = SearchIndex()
            replacement.build_from_documents(documents)
            with self.lock:
                self.index_data = documents
                self.search_index = replacement
            logger.info("Loaded %s indexed presentations.", len(documents))
            return True
        except Exception:
            # Do not destroy an already usable in-memory index when a disk cache
            # is damaged or temporarily unavailable.
            logger.exception("Failed to load index file")
            return False

    def save_index(self):
        """Serialize concurrent writers and atomically replace a flushed cache."""
        with self.save_lock:
            # Document objects are replaced rather than mutated after insertion.
            with self.lock:
                snapshot = dict(self.index_data)
            temp_name = None
            try:
                directory = os.path.dirname(os.path.abspath(INDEX_FILE))
                os.makedirs(directory, exist_ok=True)
                with tempfile.NamedTemporaryFile("w", dir=directory, delete=False, encoding="utf-8") as target:
                    temp_name = target.name
                    json.dump(snapshot, target, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temp_name, INDEX_FILE)
                return True
            except Exception:
                logger.exception("Error saving index")
                return False
            finally:
                if temp_name and os.path.exists(temp_name):
                    try:
                        os.remove(temp_name)
                    except OSError:
                        logger.debug("Could not remove temporary index cache", exc_info=True)

    def search(self, query, limit=None):
        with self.lock:
            return self.search_index.search(query, self.index_data, limit=limit)

    def get_document(self, file_path):
        with self.lock:
            return copy.deepcopy(self.index_data.get(file_path))

    def update_document_slides(self, file_path, slides):
        updated = False
        with self.lock:
            existing = self.index_data.get(file_path)
            if existing is not None:
                document = dict(existing)
                document["slides"] = copy.deepcopy(slides)
                document["extraction_version"] = EXTRACTION_VERSION
                self.index_data[file_path] = document
                updated = True
        if updated:
            self.save_index()

    def start_reindex(self):
        """Reserve the worker slot before starting its thread."""
        with self.state_lock:
            if self._indexing:
                return False
            self._indexing = True
            self.state.update(status="indexing", text="Scanning folders...", progress=0.0)
        try:
            threading.Thread(target=self._run_indexing_worker, name="presentation-indexer", daemon=True).start()
            return True
        except Exception:
            with self.state_lock:
                self._indexing = False
            self.set_state("error", "Could not start indexing. The existing index is still available.", 0.0)
            logger.exception("Could not start index worker")
            return False

    def _run_indexing_worker(self):
        try:
            self._index_files()
        except Exception:
            logger.exception("Presentation indexing failed")
            self.set_state("error", "Indexing stopped because of an error. Existing search results remain available; see the log.", 0.0)
        finally:
            with self.state_lock:
                self._indexing = False

    def _index_files(self):
        self.set_state("indexing", "Scanning folders...", 0.0, 0)
        folders = list(dict.fromkeys(os.path.abspath(path) for path in load_config() if isinstance(path, str) and path.strip()))
        inaccessible = []

        def scan_folder(folder):
            files = []
            failures = []
            if not os.path.isdir(folder):
                return files, [folder]
            def scan_error(error):
                failures.append(error.filename or folder)
            for root, directories, names in os.walk(folder, onerror=scan_error, followlinks=False):
                # Windows junctions can form cycles even without symbolic links.
                directories[:] = [name for name in directories if not os.path.islink(os.path.join(root, name))
                                  and not getattr(os.path, "isjunction", lambda _: False)(os.path.join(root, name))]
                for name in names:
                    if not name.startswith("~$") and name.lower().endswith((".ppt", ".pptx")):
                        files.append(os.path.join(root, name))
            return files, failures

        all_files = {}
        with ThreadPoolExecutor(max_workers=min(2, max(len(folders), 1)), thread_name_prefix="presentation-scan") as executor:
            for files, failures in executor.map(scan_folder, folders):
                inaccessible.extend(failures)
                for path in files:
                    all_files.setdefault(os.path.normcase(path), path)

        with self.lock:
            previous = dict(self.index_data)
        new_index_data = {}
        # Preserve cached records from disconnected drives or unreadable folders.
        for path, document in previous.items():
            normalized = os.path.normcase(os.path.abspath(path))
            if any(normalized.startswith(os.path.normcase(os.path.abspath(folder)).rstrip(os.sep) + os.sep)
                   for folder in inaccessible):
                new_index_data[path] = document

        files_to_process = []
        for file_path in all_files.values():
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                if file_path in previous:
                    new_index_data[file_path] = previous[file_path]
                inaccessible.append(file_path)
                continue
            existing = previous.get(file_path)
            legacy_cache = file_path.lower().endswith(".ppt") and existing and (existing.get("slides") or existing.get("content"))
            if existing and existing.get("mtime") == mtime and (existing.get("extraction_version") == EXTRACTION_VERSION or legacy_cache):
                # Existing legacy decks may have accurate slides previously
                # extracted via COM. File carving cannot recreate their order.
                new_index_data[file_path] = existing
                continue
            name = os.path.basename(file_path)
            title = os.path.splitext(name)[0]
            new_index_data[file_path] = {
                "path": file_path, "name": name, "title": title,
                "norm_title": normalize_text(title), "mtime": 0,
                "content": "", "norm_content": "", "slides": [],
                "extraction_version": EXTRACTION_VERSION,
            }
            files_to_process.append((file_path, mtime))

        replacement = SearchIndex()
        replacement.build_from_documents(new_index_data)
        with self.lock:
            self.index_data = new_index_data
            self.search_index = replacement
        persisted = self.save_index()

        total_files = len(new_index_data)
        processed_count = total_files - len(files_to_process)
        failed_files = 0

        def process_file(item):
            file_path, mtime = item
            try:
                if file_path.lower().endswith(".pptx"):
                    slides_raw = extract_pptx_slides(file_path)
                else:
                    # Background indexing must never attach to, open, or quit
                    # PowerPoint while a live presentation may be running.
                    carved = extract_ppt_text_carving(file_path)
                    slides_raw = [part.strip() for part in re.split(r"\n\n+", carved) if part.strip()]
                first_words = " ".join(" ".join(slide.split()[:5]) for slide in slides_raw)
                content = "\n".join(slides_raw)
                if first_words:
                    content += "\n\n" + first_words
                name = os.path.basename(file_path)
                title = os.path.splitext(name)[0]
                return file_path, {
                    "path": file_path, "name": name, "title": title,
                    "norm_title": normalize_text(title), "mtime": mtime if slides_raw else 0,
                    "content": content, "norm_content": normalize_text(content),
                    "slides": [{"label": f"Slide {idx + 1}", "text": text} for idx, text in enumerate(slides_raw)],
                    "extraction_version": EXTRACTION_VERSION,
                }, bool(slides_raw)
            except Exception:
                logger.warning("Could not index %s", file_path, exc_info=True)
                return file_path, None, False

        # Two file readers leave CPU and memory available for the separate speech
        # app. Submit bounded batches so a large library cannot fill a work queue.
        with ThreadPoolExecutor(max_workers=min(2, os.cpu_count() or 1), thread_name_prefix="presentation-read") as executor:
            for offset in range(0, len(files_to_process), 32):
                for file_path, document, readable in executor.map(process_file, files_to_process[offset:offset + 32]):
                    if document is not None:
                        with self.lock:
                            self.index_data[file_path] = document
                            self.search_index.add_document(file_path, document)
                    if not readable:
                        failed_files += 1
                    processed_count += 1
                    progress = processed_count / max(total_files, 1)
                    self.set_state("indexing", f"Indexing: {processed_count}/{total_files} files ({int(progress * 100)}%)", progress, total_files)
                    if processed_count % 200 == 0:
                        persisted = self.save_index()

        persisted = self.save_index()
        notes = []
        if inaccessible:
            notes.append(f"{len(inaccessible)} unavailable folders/files; cached entries retained")
        if failed_files:
            notes.append(f"{failed_files} files have no readable text")
        if not persisted:
            notes.append("disk cache could not be saved; current search remains available")
        message = f"Index completed. Total: {total_files} files."
        if notes:
            message += " " + "; ".join(notes) + "."
        self.set_state("completed" if persisted else "error", message, 1.0, total_files)


# --- TEXT EXTRACTION UTILITIES ---

def extract_pptx_slides(file_path):
    """Read ordered slide XML without loading embedded media into memory."""
    try:
        namespaces = {
            "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }
        slides_text = []
        with zipfile.ZipFile(file_path) as archive:
            def read_xml(name):
                # Typical slide XML is small, even for video-heavy decks.
                # Bound corrupt/hostile archive expansion per XML document.
                if archive.getinfo(name).file_size > 32 * 1024 * 1024:
                    raise ValueError("Presentation XML exceeds the safe preview size")
                return ElementTree.fromstring(archive.read(name))

            presentation = read_xml("ppt/presentation.xml")
            relationships = read_xml("ppt/_rels/presentation.xml.rels")
            targets = {
                item.get("Id"): item.get("Target")
                for item in relationships
                if item.get("TargetMode") != "External" and item.get("Type", "").endswith("/slide")
            }
            for slide_id in presentation.findall("p:sldIdLst/p:sldId", namespaces):
                target = targets.get(slide_id.get("{" + namespaces["r"] + "}id"))
                if not target:
                    raise ValueError("Presentation has a missing slide relationship")
                name = posixpath.normpath(posixpath.join("ppt", target)) if not target.startswith("/") else target.lstrip("/")
                slide_xml = read_xml(name)
                paragraphs = []
                # Paragraph order also covers text inside grouped shapes/tables.
                # Formatting runs are joined before stripping, preserving words.
                for paragraph in slide_xml.findall(".//a:p", namespaces):
                    parts = []
                    for element in paragraph.iter():
                        if element.tag == "{" + namespaces["a"] + "}t":
                            parts.append(element.text or "")
                        elif element.tag == "{" + namespaces["a"] + "}br":
                            parts.append("\n")
                    text = _normalize_slide_text("".join(parts)).strip()
                    if text:
                        paragraphs.append(text)
                # Image-only and blank slides still occupy a real slide number.
                slides_text.append("\n".join(paragraphs))
        return slides_text
    except Exception as e:
        logger.debug(f"pptx extraction error for {file_path}: {e}")
        return []


def extract_ppt_text_carving(file_path):
    """Binary UTF-16LE carving for legacy .ppt files."""
    try:
        # Embedded media can make a legacy deck enormous. Bound memory use
        # during background search indexing while retaining title-only results.
        max_bytes = 64 * 1024 * 1024
        with open(file_path, 'rb') as f:
            data = f.read(max_bytes)
        if os.path.getsize(file_path) > max_bytes:
            logger.warning('Legacy text scan limited to the first 64 MiB: %s', file_path)

        pattern = rb'((?:[\x20-\x7e\xa0-\xff]\x00|[\x00-\xff][\x01-\x04]){4,4096})'
        extracted_lines = []
        seen_lines = set()

        ignore_words = {
            'root entry', 'summaryinformation', 'documentsummaryinformation',
            'powerpoint document', 'current user', 'ksoproductbuildver',
            'utilizator', 'arial', 'simsun', 'wingdings', 'georgia',
            'times new roman', 'microsoft yahei', 'arial unicode ms',
            'calibri', 'segoe ui', 'verdana', 'tahoma', 'courier new',
            'click to edit master', 'second level', 'third level',
            'fourth level', 'fifth level', 'slide master', 'title master'
        }

        for m in re.finditer(pattern, data):
            try:
                s = m.group(1).decode('utf-16-le').strip()
                s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s).strip()
                if len(s) < 4:
                    continue
                s_lower = s.lower()
                if any(w in s_lower for w in ignore_words):
                    continue
                if re.match(r'^(rectangle|text box|freeform|group|oval|line|picture|straight connector)\s*\d*$', s_lower):
                    continue
                if s not in seen_lines:
                    seen_lines.add(s)
                    extracted_lines.append(s)
            except Exception:
                pass

        return "\n".join(extracted_lines)
    except Exception:
        return ""


def extract_ppt_slides_com(ppt_app, file_path):
    """Extract from an explicitly owned COM instance, never an audience deck.

    Callers must initialize COM on their current thread. Background indexing
    deliberately does not call this function.
    """
    pres = None
    owns_presentation = False
    try:
        file_path_abs = os.path.abspath(file_path)
        for existing in ppt_app.Presentations:
            if os.path.normcase(os.path.abspath(existing.FullName)) == os.path.normcase(file_path_abs):
                pres = existing
                break
        if pres is None:
            pres = ppt_app.Presentations.Open(file_path_abs, ReadOnly=True, WithWindow=False)
            owns_presentation = True
        slides_text = []

        for slide in pres.Slides:
            slide_runs = []

            def extract_com_shape_text(shape):
                if shape.HasTextFrame and shape.TextFrame.HasText:
                    slide_runs.append(shape.TextFrame.TextRange.Text)
                if shape.HasTable:
                    table = shape.Table
                    for r in range(1, table.Rows.Count + 1):
                        for c in range(1, table.Columns.Count + 1):
                            cell = table.Cell(r, c)
                            if cell.Shape.HasTextFrame and cell.Shape.TextFrame.HasText:
                                slide_runs.append(cell.Shape.TextFrame.TextRange.Text)
                if shape.Type == 6:  # msoGroup
                    try:
                        for sub in shape.GroupItems:
                            extract_com_shape_text(sub)
                    except Exception:
                        pass

            for shape in slide.Shapes:
                extract_com_shape_text(shape)

            text = _normalize_slide_text("\n".join(slide_runs)).strip()
            slides_text.append(text)

        return slides_text
    except Exception:
        logger.debug('PowerPoint text extraction failed for %s', file_path, exc_info=True)
        return []
    finally:
        if pres is not None and owns_presentation:
            try:
                pres.Close()
            except Exception:
                logger.debug('Could not close the extraction document', exc_info=True)
