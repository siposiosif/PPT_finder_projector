import os
import sys
import time
import json
import re
import unicodedata
import threading
import subprocess
import webbrowser
import socket
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request, Response

# Inițializare Flask
app = Flask(__name__)

# Dezactivare jurnalizare consolă (logging) implicită server de dezvoltare
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Constante
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, 'config.txt')
INDEX_FILE = os.path.join(APP_DIR, 'ppt_index.json')

# Index global și Lock de sincronizare
index_data = {}
index_lock = threading.Lock()

# Stare globală de indexare
indexing_state = {
    "status": "idle",
    "text": "Index idle",
    "progress": 0.0,
    "total_files": 0
}
indexing_state_lock = threading.Lock()

# Cache de cuvinte la rulare (runtime)
words_cache = {}
title_words_cache = {}

# Stare globală de proiecție
projection_state = {
    "text": "",
    "label": "",
    "timestamp": 0
}
projection_state_lock = threading.Lock()

# --- CONFIGURARE CĂI FIȘIERE ---

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                paths = [line.strip() for line in f if line.strip()]
                if paths:
                    return paths
        except Exception as e:
            print(f"Error loading config: {e}")
            
    default_paths = [os.path.join(APP_DIR, 'Cântări Comun')]
    save_config(default_paths)
    return default_paths

def save_config(paths):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            for path in paths:
                f.write(path + "\n")
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

# --- REZOLVARE IP REȚEA ---

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# --- NORMALIZARE TEXT ---

def normalize_text(text):
    if not text:
        return ""
    text = text.lower()
    replacements = {
        'ă': 'a', 'â': 'a', 'î': 'i', 'ș': 's', 'ț': 't',
        'ş': 's', 'ţ': 't'
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    nfkd_form = unicodedata.normalize('NFKD', text)
    text = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# --- POTRIVIRE CĂUTARE IMPRECIZĂ (FUZZY) ---

def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def is_fuzzy_match(q_word, target_word):
    q_len = len(q_word)
    t_len = len(target_word)
    
    if q_len < 4 or t_len < 4:
        return False
        
    max_dist = 2 if q_len > 5 else 1
    if abs(q_len - t_len) > max_dist:
        return False
        
    q_set = set(q_word)
    t_set = set(target_word)
    common_chars = len(q_set.intersection(t_set))
    if common_chars < q_len - max_dist * 2:
        return False
        
    dist = levenshtein_distance(q_word, target_word)
    return dist <= max_dist

# --- EXTRAGERE TEXT ȘI DIAPOZITIVE ---

def extract_pptx_text(file_path):
    return "\n".join(extract_pptx_slides(file_path))

def extract_pptx_slides(file_path):
    try:
        import pptx
        prs = pptx.Presentation(file_path)
        slides_text = []
        for slide in prs.slides:
            slide_runs = []
            def extract_shape_text(shape):
                if shape.has_text_frame:
                    for p in shape.text_frame.paragraphs:
                        for r in p.runs:
                            if r.text.strip():
                                slide_runs.append(r.text.strip())
                if shape.has_table:
                    for row in shape.table.rows:
                        for cell in row.cells:
                            if cell.text.strip():
                                slide_runs.append(cell.text.strip())
                if hasattr(shape, "shapes"):
                    for s in shape.shapes:
                        extract_shape_text(s)
            for shape in slide.shapes:
                extract_shape_text(shape)
            text = "\n".join(slide_runs).strip()
            if text:
                slides_text.append(text)
        return slides_text
    except Exception:
        return []

def extract_ppt_text_carving(file_path):
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
            
        pattern = rb'((?:[\x20-\x7e\xa0-\xff]\x00|[\x00-\xff][\x01-\x04]){4,})'
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
                
        if len(extracted_lines) < 2:
            ascii_pattern = rb'[\x20-\x7e]{6,}'
            for m in re.finditer(ascii_pattern, data):
                try:
                    s = m.group(0).decode('ascii').strip()
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

def extract_ppt_text_com(ppt_app, file_path):
    return "\n".join(extract_ppt_slides_com(ppt_app, file_path))

def extract_ppt_slides_com(ppt_app, file_path):
    pres = None
    try:
        file_path_abs = os.path.abspath(file_path)
        pres = ppt_app.Presentations.Open(file_path_abs, ReadOnly=True, WithWindow=False)
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
                if shape.Type == 6: # msoGroup
                    try:
                        for sub in shape.GroupItems:
                            extract_com_shape_text(sub)
                    except Exception:
                        pass
            for shape in slide.Shapes:
                extract_com_shape_text(shape)
            text = "\n".join(slide_runs).strip()
            if text:
                slides_text.append(text)
        pres.Close()
        return slides_text
    except Exception:
        if pres:
            try: pres.Close()
            except Exception: pass
        return []

# --- EVIDENȚIERE SECVENȚĂ (SNIPPET) ---

def get_highlighted_snippet(content, query):
    if not content:
        return ""
        
    q_words = normalize_text(query).split()
    if not q_words:
        return content[:150] + "..." if len(content) > 150 else content
        
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    best_line = ""
    max_matches = -1
    
    for line in lines:
        line_norm = normalize_text(line)
        matches = sum(1 for qw in q_words if qw in line_norm)
        if matches > max_matches:
            max_matches = matches
            best_line = line
            if matches == len(q_words):
                break
                
    if not best_line:
        best_line = lines[0] if lines else ""
        
    words = re.findall(r'\w+|\W+', best_line)
    highlighted_words = []
    for word in words:
        if word.isalnum():
            word_norm = normalize_text(word)
            matched = False
            for qw in q_words:
                if qw == word_norm or (len(qw) > 3 and qw in word_norm) or (len(word_norm) > 3 and word_norm in qw) or is_fuzzy_match(qw, word_norm):
                    matched = True
                    break
            if matched:
                highlighted_words.append(f"<mark>{word}</mark>")
            else:
                highlighted_words.append(word)
        else:
            highlighted_words.append(word)
            
    snippet = "".join(highlighted_words)
    if len(snippet) > 200:
        idx = snippet.find("<mark>")
        if idx > 50:
            snippet = "..." + snippet[idx-30:idx+150] + "..."
        else:
            snippet = snippet[:180] + "..."
    return snippet

# --- MOTOR DE INDEXARE ȘI LUCRĂTORI DE FUNDAL ---

def load_index():
    global index_data, words_cache, title_words_cache
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
                # Pre-populare cache-uri la rulare
                words_cache.clear()
                title_words_cache.clear()
                for path, doc in index_data.items():
                    title_words_cache[path] = doc.get("norm_title", "").split()
                    words_cache[path] = set(doc.get("norm_content", "").split())
                return
        except Exception:
            pass
    index_data = {}

def save_index():
    global index_data
    try:
        with open(INDEX_FILE, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

def scan_single_folder(folder):
    files = []
    if os.path.exists(folder):
        for root, _, fs in os.walk(folder):
            for file in fs:
                if file.startswith('~$'):
                    continue
                if file.lower().endswith(('.ppt', '.pptx')):
                    files.append(os.path.join(root, file))
    return files

def run_indexing_thread():
    global index_data, words_cache, title_words_cache
    
    def set_state(status, text, progress, total_files=None):
        with indexing_state_lock:
            indexing_state["status"] = status
            indexing_state["text"] = text
            indexing_state["progress"] = progress
            if total_files is not None:
                indexing_state["total_files"] = total_files

    set_state("indexing", "Scanning folders...", 0.0, 0)
    
    folders = load_config()
    all_files = []
    
    with ThreadPoolExecutor(max_workers=max(len(folders), 1)) as executor:
        results = executor.map(scan_single_folder, folders)
        for res in results:
            all_files.extend(res)
            
    total_files = len(all_files)
    if total_files == 0:
        set_state("completed", "Index completed. No presentations found.", 1.0, 0)
        return

    # Faza 1: Indexare instantă a titlurilor
    files_to_process = []
    new_index_data = {}
    
    for file_path in all_files:
        try:
            mtime = os.path.getmtime(file_path)
        except Exception:
            continue
            
        file_name = os.path.basename(file_path)
        title_no_ext, _ = os.path.splitext(file_name)
        
        if file_path in index_data and index_data[file_path].get("mtime") == mtime:
            new_index_data[file_path] = index_data[file_path]
        else:
            existing_content = ""
            existing_norm_content = ""
            existing_slides = []
            if file_path in index_data:
                existing_content = index_data[file_path].get("content", "")
                existing_norm_content = index_data[file_path].get("norm_content", "")
                existing_slides = index_data[file_path].get("slides", [])
                
            new_index_data[file_path] = {
                "path": file_path,
                "name": file_name,
                "title": title_no_ext,
                "norm_title": normalize_text(title_no_ext),
                "mtime": 0,
                "content": existing_content,
                "norm_content": existing_norm_content,
                "slides": existing_slides
            }
            files_to_process.append((file_path, mtime))
            
    with index_lock:
        index_data = new_index_data
        save_index()
        # Reîmprospătare cache-uri la rulare
        words_cache.clear()
        title_words_cache.clear()
        for path, doc in index_data.items():
            title_words_cache[path] = doc.get("norm_title", "").split()
            words_cache[path] = set(doc.get("norm_content", "").split())
        
    processed_count = total_files - len(files_to_process)
    progress_val = processed_count / total_files
    set_state("indexing", f"Indexing: {processed_count}/{total_files} files ({int(progress_val*100)}%)", progress_val, total_files)
    
    if not files_to_process:
        set_state("completed", f"Index completed. Total: {total_files} files.", 1.0, total_files)
        return

    # Faza 2: Extragere conținut diapozitive
    ppt_app_available = False
    try:
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        test_app = win32com.client.Dispatch("PowerPoint.Application")
        test_app.Quit()
        pythoncom.CoUninitialize()
        ppt_app_available = True
    except Exception:
        pass

    com_fallback_queue = []
    fallback_lock = threading.Lock()
    write_lock = threading.Lock()
    
    def process_file_worker(item):
        nonlocal processed_count
        file_path, mtime = item
        file_name = os.path.basename(file_path)
        title_no_ext, _ = os.path.splitext(file_name)
        
        is_pptx = file_path.lower().endswith('.pptx')
        
        # Dacă este .ppt și COM este disponibil, îl redirecționăm către Faza 3 COM
        if not is_pptx and ppt_app_available:
            with fallback_lock:
                com_fallback_queue.append(item)
            return
            
        slides_raw = []
        if is_pptx:
            slides_raw = extract_pptx_slides(file_path)
        else:
            content_carve = extract_ppt_text_carving(file_path)
            slides_raw = [p.strip() for p in re.split(r'\n\n+', content_carve) if p.strip()]
            
        # Adăugare primele 5 cuvinte din fiecare slide la sfârșitul conținutului indexat
        main_content = "\n".join(slides_raw)
        first_5_words = []
        for s in slides_raw:
            w = s.split()
            if w:
                first_5_words.append(" ".join(w[:5]))
        first_5_text = " ".join(first_5_words)
        
        if first_5_text:
            full_content = main_content + "\n\n" + first_5_text
        else:
            full_content = main_content
            
        slides = [{"label": f"Slide {i+1}", "text": s} for i, s in enumerate(slides_raw)]
        
        with write_lock:
            with index_lock:
                norm_title = normalize_text(title_no_ext)
                norm_content = normalize_text(full_content)
                index_data[file_path] = {
                    "path": file_path,
                    "name": file_name,
                    "title": title_no_ext,
                    "norm_title": norm_title,
                    "mtime": mtime,
                    "content": full_content,
                    "norm_content": norm_content,
                    "slides": slides
                }
                # Actualizare cache-uri la rulare
                title_words_cache[file_path] = norm_title.split()
                words_cache[file_path] = set(norm_content.split())
            processed_count += 1
            progress_val = processed_count / total_files
            
            percent_step = max(1, total_files // 100)
            if processed_count % percent_step == 0 or processed_count == total_files:
                set_state("indexing", f"Indexing: {processed_count}/{total_files} files ({int(progress_val*100)}%)", progress_val, total_files)
                
            if processed_count % 250 == 0:
                with index_lock:
                    save_index()

    # Limitare la 4 lucrători concurenți pentru hardware slab, cum ar fi Raspberry Pi
    max_threads = min(4, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        executor.map(process_file_worker, files_to_process)
        
    # Faza 3: Rezervă (fallback) secvențială COM pentru fișierele .ppt mai vechi care au eșuat la extragerea brută sau e preferat COM
    if com_fallback_queue:
        ppt_app = None
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            ppt_app = win32com.client.Dispatch("PowerPoint.Application")
        except Exception as com_err:
            print(f"Failed to start PowerPoint: {com_err}")
            
        for idx, (file_path, mtime) in enumerate(com_fallback_queue):
            file_name = os.path.basename(file_path)
            title_no_ext, _ = os.path.splitext(file_name)
            
            slides_raw = []
            if ppt_app:
                slides_raw = extract_ppt_slides_com(ppt_app, file_path)
                
            if not slides_raw:
                content_carve = extract_ppt_text_carving(file_path)
                slides_raw = [p.strip() for p in re.split(r'\n\n+', content_carve) if p.strip()]
                
            main_content = "\n".join(slides_raw)
            first_5_words = []
            for s in slides_raw:
                w = s.split()
                if w:
                    first_5_words.append(" ".join(w[:5]))
            first_5_text = " ".join(first_5_words)
            
            if first_5_text:
                full_content = main_content + "\n\n" + first_5_text
            else:
                full_content = main_content
                
            slides = [{"label": f"Slide {i+1}", "text": s} for i, s in enumerate(slides_raw)]
            
            with index_lock:
                norm_title = normalize_text(title_no_ext)
                norm_content = normalize_text(full_content)
                index_data[file_path] = {
                    "path": file_path,
                    "name": file_name,
                    "title": title_no_ext,
                    "norm_title": norm_title,
                    "mtime": mtime,
                    "content": full_content,
                    "norm_content": norm_content,
                    "slides": slides
                }
                # Actualizare cache-uri la rulare
                title_words_cache[file_path] = norm_title.split()
                words_cache[file_path] = set(norm_content.split())
                
            processed_count += 1
            progress_val = processed_count / total_files
            
            percent_step = max(1, total_files // 100)
            if processed_count % percent_step == 0 or processed_count == total_files:
                set_state("indexing", f"Indexing: {processed_count}/{total_files} files ({int(progress_val*100)}%)", progress_val, total_files)
            
            if processed_count % 250 == 0:
                with index_lock:
                    save_index()
                    
        if ppt_app:
            try:
                ppt_app.Quit()
            except Exception:
                pass
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

    with index_lock:
        save_index()
        
    set_state("completed", f"Index completed. Total: {total_files} files.", 1.0, total_files)

# --- ENDPOINT-URI FLASK ---

@app.route('/')
def home():
    local_ip = get_local_ip()
    port = request.environ.get('SERVER_PORT', '8080')
    network_url = f"http://{local_ip}:{port}"
    
    html_content = """<!DOCTYPE html>
<html lang="ro">
<head>
  <meta charset="UTF-8">
  <title>PPT Finder Desktop</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-color: #0b0b0f;
      --panel-bg: #12121c;
      --card-bg: #161625;
      --border-color: rgba(255, 255, 255, 0.08);
      --primary-accent: #00b0ff;
      --secondary-accent: #00e5ff;
      --text-main: #f0f0f5;
      --text-muted: #8d8da0;
      --text-sub: #636375;
      --font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    body {
      margin: 0;
      padding: 0;
      background-color: var(--bg-color);
      color: var(--text-main);
      font-family: var(--font-family);
      background-image: 
        radial-gradient(at 0% 0%, rgba(0, 176, 255, 0.08) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(0, 229, 255, 0.05) 0px, transparent 50%);
      background-attachment: fixed;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }

    .container {
      max-width: 1200px;
      margin: 25px auto;
      padding: 0 24px;
      width: 100%;
      box-sizing: border-box;
      flex: 1;
    }

    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 25px;
    }

    .title-group h1 {
      margin: 0;
      font-size: 26px;
      font-weight: 700;
      background: linear-gradient(135deg, var(--primary-accent), var(--secondary-accent));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      letter-spacing: -0.5px;
    }

    .title-group p {
      margin: 5px 0 0 0;
      font-size: 13px;
      color: var(--text-muted);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .network-ip {
      font-size: 12px;
      color: var(--primary-accent);
      background: rgba(0, 176, 255, 0.06);
      border: 1px solid rgba(0, 176, 255, 0.15);
      padding: 6px 12px;
      border-radius: 8px;
      font-weight: 500;
    }

    .settings-btn {
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      padding: 8px 14px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 600;
      transition: all 0.2s ease;
    }

    .settings-btn:hover {
      border-color: var(--primary-accent);
      color: var(--text-main);
      box-shadow: 0 0 10px rgba(0, 176, 255, 0.15);
    }

    /* Settings Panel */
    .settings-panel {
      background: var(--panel-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 25px;
      display: none;
    }

    .settings-panel.active {
      display: block;
      animation: slideDown 0.3s ease;
    }

    .settings-panel h3 {
      margin-top: 0;
      margin-bottom: 15px;
      font-size: 16px;
      color: var(--primary-accent);
    }

    .folder-list {
      list-style: none;
      padding: 0;
      margin: 0 0 15px 0;
    }

    .folder-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 12px;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      margin-bottom: 8px;
      font-size: 13px;
    }

    .folder-item span {
      word-break: break-all;
      padding-right: 10px;
    }

    .delete-folder-btn {
      background: rgba(255, 80, 80, 0.1);
      border: 1px solid rgba(255, 80, 80, 0.2);
      color: #ff6060;
      border-radius: 4px;
      padding: 4px 8px;
      font-size: 11px;
      cursor: pointer;
      font-weight: 600;
      transition: all 0.2s;
    }

    .delete-folder-btn:hover {
      background: rgba(255, 80, 80, 0.25);
      border-color: rgba(255, 80, 80, 0.4);
    }

    .add-folder-group {
      display: flex;
      gap: 10px;
    }

    .add-folder-group input {
      flex: 1;
      background: rgba(0, 0, 0, 0.2);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 13px;
      outline: none;
      transition: border-color 0.2s;
    }

    .add-folder-group input:focus {
      border-color: var(--primary-accent);
    }

    .add-folder-btn {
      background: var(--primary-accent);
      border: none;
      color: #000;
      font-weight: 700;
      padding: 8px 16px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      transition: opacity 0.2s;
    }

    .add-folder-btn:hover {
      opacity: 0.9;
    }

    /* Search Area */
    .search-container {
      position: relative;
      margin-bottom: 25px;
    }

    .search-input {
      width: 100%;
      height: 52px;
      background: var(--panel-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 0 20px 0 50px;
      color: var(--text-main);
      font-size: 16px;
      box-sizing: border-box;
      outline: none;
      transition: all 0.25s ease;
    }

    .search-input:focus {
      border-color: var(--primary-accent);
      box-shadow: 0 0 15px rgba(0, 176, 255, 0.15);
    }

    .search-icon {
      position: absolute;
      left: 18px;
      top: 15px;
      font-size: 20px;
      color: var(--text-muted);
    }

    /* Progress Banner */
    .progress-banner {
      background: rgba(0, 176, 255, 0.05);
      border: 1px solid rgba(0, 176, 255, 0.15);
      border-radius: 10px;
      padding: 15px 20px;
      margin-bottom: 25px;
      display: none;
    }

    .progress-banner.active {
      display: block;
    }

    .progress-header {
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 8px;
      color: var(--primary-accent);
    }

    .progress-track {
      background: rgba(255, 255, 255, 0.05);
      height: 6px;
      border-radius: 3px;
      overflow: hidden;
    }

    .progress-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--primary-accent), var(--secondary-accent));
      width: 0%;
      transition: width 0.3s ease;
    }

    /* Layout Columns */
    .main-columns {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }

    @media (max-width: 850px) {
      .main-columns {
        grid-template-columns: 1fr;
        gap: 20px;
      }
      header {
        flex-direction: column;
        align-items: flex-start;
        gap: 12px;
      }
      .network-ip {
        margin-right: 0;
        width: 100%;
        box-sizing: border-box;
        text-align: center;
      }
      .container {
        margin: 15px auto;
        padding: 0 16px;
      }
    }

    .column-title {
      font-size: 16px;
      font-weight: 700;
      color: var(--text-main);
      margin-bottom: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .column-meta {
      font-size: 11px;
      color: var(--text-muted);
      font-weight: 500;
    }

    .results-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .empty-state {
      text-align: center;
      padding: 60px 15px;
      border: 1px dashed var(--border-color);
      border-radius: 12px;
      color: var(--text-muted);
    }

    .empty-state .icon {
      font-size: 30px;
      margin-bottom: 10px;
      opacity: 0.5;
    }

    .empty-state h4 {
      margin: 0;
      font-size: 14px;
      color: var(--text-main);
    }

    .empty-state p {
      margin: 4px 0 0 0;
      font-size: 12px;
    }

    /* Card styling */
    .card {
      background: var(--panel-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 12px 15px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      transition: all 0.25s ease;
      cursor: pointer;
    }

    .card:hover {
      border-color: rgba(0, 176, 255, 0.3);
      transform: translateY(-2px);
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    }

    .card.selected {
      border-color: var(--primary-accent);
      box-shadow: 0 0 15px rgba(0, 176, 255, 0.25);
      background: rgba(0, 176, 255, 0.04);
      transform: translateY(-2px);
    }

    .card-info {
      flex: 1;
      min-width: 0;
    }

    .card-title-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }

    .card-icon {
      font-size: 16px;
    }

    .card-title {
      font-size: 14px;
      font-weight: 700;
      color: #f0f0f5;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
      min-width: 0;
    }

    .card-author-line {
      font-size: 11px;
      color: var(--secondary-accent);
      margin-bottom: 4px;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      opacity: 0.85;
    }

    .card-path {
      font-size: 10px;
      color: var(--text-muted);
      margin-bottom: 6px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      direction: rtl;
      text-align: left;
    }

    .card-snippet {
      font-size: 12px;
      color: #c5c5d0;
      background: rgba(0, 0, 0, 0.2);
      padding: 6px 10px;
      border-radius: 6px;
      line-height: 1.4;
      word-break: break-all;
    }

    .card-snippet mark {
      background: rgba(0, 176, 255, 0.2);
      color: var(--secondary-accent);
      font-weight: 600;
      border-radius: 3px;
      padding: 0 2px;
      border: 1px solid rgba(0, 176, 255, 0.3);
    }

    .card-actions {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .action-btn {
      width: 88px;
      padding: 6px 0;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      text-align: center;
      transition: all 0.2s;
      box-sizing: border-box;
    }

    .folder-group-header {
      font-size: 12px;
      font-weight: bold;
      color: var(--secondary-accent);
      background: rgba(0, 229, 255, 0.05);
      border-left: 3px solid var(--secondary-accent);
      padding: 8px 12px;
      margin: 15px 0 5px 0;
      border-radius: 4px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .folder-group-header .folder-path {
      font-size: 10px;
      color: var(--text-muted);
      font-weight: normal;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-left: 10px;
    }

    .btn-primary {
      background: var(--primary-accent);
      border: none;
      color: #000;
    }

    .btn-primary:hover {
      opacity: 0.9;
      box-shadow: 0 0 10px rgba(0, 176, 255, 0.3);
    }

    .btn-secondary {
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
    }

    .btn-secondary:hover {
      border-color: var(--text-main);
      color: var(--text-main);
    }

    .btn-success {
      background: #00e676;
      border: none;
      color: #000;
    }

    .btn-success:hover {
      opacity: 0.9;
      box-shadow: 0 0 10px rgba(0, 230, 118, 0.3);
    }

    /* Projection Drawer Panel */
    .projection-drawer {
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      width: 92%;
      max-width: 820px;
      background: rgba(18, 18, 28, 0.96);
      border: 1px solid var(--primary-accent);
      box-shadow: 0 10px 40px rgba(0, 0, 0, 0.9), 0 0 20px rgba(0, 176, 255, 0.2);
      border-radius: 16px;
      padding: 16px 20px;
      display: none;
      z-index: 1000;
      backdrop-filter: blur(12px);
      box-sizing: border-box;
    }

    .projection-drawer.active {
      display: block;
      animation: fadeInUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }

    .drawer-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 8px;
    }

    .drawer-title {
      font-size: 15px;
      font-weight: 700;
      color: var(--primary-accent);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 60%;
    }

    .drawer-actions {
      display: flex;
      gap: 8px;
    }

    .drawer-btn {
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
    }

    .drawer-btn-projector {
      background: transparent;
      border: 1px solid var(--secondary-accent);
      color: var(--secondary-accent);
    }

    .drawer-btn-projector:hover {
      background: var(--secondary-accent);
      color: #000;
    }

    .drawer-btn-clear {
      background: rgba(255, 80, 80, 0.1);
      border: 1px solid #ff6060;
      color: #ff6060;
    }

    .drawer-btn-clear:hover {
      background: #ff6060;
      color: #000;
    }

    .drawer-btn-close {
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
    }

    .drawer-btn-close:hover {
      border-color: var(--text-main);
      color: var(--text-main);
    }

    .slides-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
      gap: 10px;
      max-height: 200px;
      overflow-y: auto;
      padding: 4px;
    }

    .slide-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 8px 10px;
      cursor: pointer;
      text-align: center;
      transition: all 0.2s;
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 50px;
      user-select: none;
    }

    .slide-card:hover {
      border-color: var(--secondary-accent);
      background: rgba(0, 229, 255, 0.03);
    }

    .slide-card.active {
      background: var(--primary-accent);
      color: #000;
      font-weight: 700;
      border-color: var(--primary-accent);
      box-shadow: 0 0 10px rgba(0, 176, 255, 0.3);
    }

    .slide-card-label {
      font-size: 9px;
      opacity: 0.7;
      text-transform: uppercase;
      margin-bottom: 2px;
      font-weight: 700;
    }

    .slide-card-preview {
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* Footer */
    footer {
      background: #06060a;
      border-top: 1px solid var(--border-color);
      padding: 15px 0;
      margin-top: 40px;
    }

    .footer-content {
      max-width: 1000px;
      margin: 0 auto;
      padding: 0 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--text-muted);
    }

    .reindex-btn {
      background: var(--primary-accent);
      border: none;
      color: #000;
      font-weight: 700;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      transition: opacity 0.2s;
    }

    .reindex-btn:hover {
      opacity: 0.9;
    }

    @keyframes slideDown {
      from { opacity: 0; transform: translateY(-10px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @keyframes fadeInUp {
      from { opacity: 0; transform: translate(-50%, 30px); }
      to { opacity: 1; transform: translate(-50%, 0); }
    }
  </style>
</head>
<body>

  <div class="container">
    <header>
      <div class="title-group">
        <h1>PPT Finder Web</h1>
      </div>
      <div class="header-actions">
        <span class="network-ip">Network URL: {network_url}</span>
        <button class="settings-btn" onclick="toggleSettings()">⚙ Folders</button>
      </div>
    </header>

    <!-- Settings Panel -->
    <div class="settings-panel" id="settings-panel">
      <h3>Manage Search Folders</h3>
      <ul class="folder-list" id="folder-list">
        <!-- Rendered dynamically -->
      </ul>
      <div class="add-folder-group">
        <input type="text" id="new-folder-input" placeholder="Enter absolute directory path (e.g. D:\\\\My Presentations)">
        <button class="add-folder-btn" onclick="addFolder()">Add Folder</button>
      </div>
    </div>

    <!-- Search Input -->
    <div class="search-container">
      <span class="search-icon">🔍</span>
      <input type="text" class="search-input" id="search-input" placeholder="Type to search song titles or slide contents..." autofocus>
    </div>

    <!-- Progress Banner -->
    <div class="progress-banner" id="progress-banner">
      <div class="progress-header">
        <span id="progress-status-text">Indexing files...</span>
        <span id="progress-percent">0%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
    </div>

    <!-- Two Columns Layout -->
    <div class="main-columns">
      <!-- Column 1: Local Presentations -->
      <div>
        <div class="column-title">
          <span>Local Presentations</span>
          <span class="column-meta" id="local-count">Enter a query to search</span>
        </div>
        <div class="results-list" id="local-results-list">
          <div class="empty-state">
            <div class="icon">💻</div>
            <h4>Local Files</h4>
          </div>
        </div>
      </div>

      <!-- Column 2: Web Search (Resurse Crestine) -->
      <div>
        <div class="column-title">
          <span>Web Search (Resurse Creștine)</span>
          <span class="column-meta" id="web-count">Enter a query to search</span>
        </div>
        <div class="results-list" id="web-results-list">
          <div class="empty-state">
            <div class="icon">🌐</div>
            <h4>Resurse Creștine</h4>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Float Drawer for Projection Control -->
  <div class="projection-drawer" id="projection-drawer">
    <div class="drawer-header">
      <span class="drawer-title" id="projector-song-title">Now Projecting: None</span>
      <div class="drawer-actions">
        <button class="drawer-btn drawer-btn-projector" onclick="openProjectorWindow()">📺 Open Projector Screen</button>
        <button class="drawer-btn drawer-btn-clear" onclick="clearProjector()">⏹ Clear</button>
        <button class="drawer-btn drawer-btn-close" onclick="closeProjectionDrawer()">✖ Close</button>
      </div>
    </div>
    <div class="slides-grid" id="projector-slides-grid">
      <!-- Clickable slides rendered here -->
    </div>
  </div>

  <footer>
    <div class="footer-content">
      <span id="footer-status">Loading index status...</span>
      <button class="reindex-btn" id="reindex-btn" onclick="triggerReindex()">Reindex Files</button>
    </div>
  </footer>

  <script>
    let searchDebounceTimeout = null;
    let isIndexing = false;
    let selectedResultIndex = -1;
    let activeProjectionSlideIndex = -1;
    let projectionSlides = [];

    // Load initial configuration folders and status
    fetchFolders();
    pollStatus();
    setInterval(pollStatus, 1500); // regular polling for status updates

    function toggleSettings() {
      const panel = document.getElementById('settings-panel');
      panel.classList.toggle('active');
    }

    async function fetchFolders() {
      try {
        const res = await fetch('/api/folders');
        const folders = await res.json();
        const list = document.getElementById('folder-list');
        list.innerHTML = '';
        
        folders.forEach((folder, idx) => {
          const li = document.createElement('li');
          li.className = 'folder-item';
          li.innerHTML = `
            <span>${folder}</span>
            <button class="delete-folder-btn" onclick="deleteFolder(${idx})">Remove</button>
          `;
          list.appendChild(li);
        });
      } catch (err) {
        console.error("Error fetching folders:", err);
      }
    }

    async function addFolder() {
      const input = document.getElementById('new-folder-input');
      const path = input.value.trim();
      if (!path) return;

      try {
        const res = await fetch('/api/folders', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path})
        });
        const status = await res.json();
        if (status.success) {
          input.value = '';
          fetchFolders();
          triggerReindex(); // auto reindex when folder added
        } else {
          alert("Error adding folder: " + status.error);
        }
      } catch (err) {
        alert("Server communication error.");
      }
    }

    async function deleteFolder(index) {
      try {
        const res = await fetch(`/api/folders?index=${index}`, {
          method: 'DELETE'
        });
        const status = await res.json();
        if (status.success) {
          fetchFolders();
          triggerReindex(); // auto reindex on removal
        }
      } catch (err) {
        console.error("Error deleting folder:", err);
      }
    }

    function onSearchInput() {
      // Deprecated in favor of the keydown listener, but kept as a fallback empty handler
    }

    async function performSearch() {
      const query = document.getElementById('search-input').value.trim();
      const localResultsList = document.getElementById('local-results-list');
      const webResultsList = document.getElementById('web-results-list');
      const localCount = document.getElementById('local-count');
      const webCount = document.getElementById('web-count');

      selectedResultIndex = -1;

      if (!query) {
        localCount.innerText = "Enter a query to search";
        webCount.innerText = "Enter a query to search";
        localResultsList.innerHTML = `
          <div class="empty-state">
            <div class="icon">💻</div>
            <h4>Local Files</h4>
          </div>
        `;
        webResultsList.innerHTML = `
          <div class="empty-state">
            <div class="icon">🌐</div>
            <h4>Resurse Creștine</h4>
          </div>
        `;
        return;
      }

      // Parallelize local and web search fetches
      
      // 1. Local Search Execution
      const localSearchPromise = (async () => {
        try {
          const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
          const data = await res.json();
          const results = data.results;
          const isNumeric = data.is_numeric;

          if (results.length === 0) {
            localCount.innerText = "No matches found";
            localResultsList.innerHTML = `
              <div class="empty-state">
                <div class="icon">ℹ️</div>
                <h4>No matches found</h4>
                <p>Try refining your query or click 'Reindex'</p>
              </div>
            `;
          } else {
            localCount.innerText = `Top ${results.length} results`;
            localResultsList.innerHTML = '';

            let lastFolder = null;
            results.forEach((item, index) => {
              // Group headers for numeric searches
              if (isNumeric && item.folder !== lastFolder) {
                const header = document.createElement('div');
                header.className = 'folder-group-header';
                const folderName = item.folder.split(/[\\/]/).pop();
                header.innerHTML = `<span>📁 ${folderName}</span>`;
                localResultsList.appendChild(header);
                lastFolder = item.folder;
              }

              const card = document.createElement('div');
              card.className = 'card';
              card.id = `local-card-${index}`;
              
              card.onclick = () => {
                const prev = document.querySelector('.card.selected');
                if (prev) prev.classList.remove('selected');
                selectedResultIndex = index;
                card.classList.add('selected');
              };

              const isPptx = item.name.toLowerCase().endsWith('.pptx');
              const fileIcon = isPptx ? '📊' : '📄';
              let snippetHtml = item.snippet ? `<div class="card-snippet">${item.snippet}</div>` : '';

              card.innerHTML = `
                <div class="card-info">
                  <div class="card-title-row">
                    <span class="card-icon">${fileIcon}</span>
                    <span class="card-title" title="${item.title}">${item.title}</span>
                  </div>
                  <div class="card-path" title="${item.path}">${item.path}</div>
                  ${snippetHtml}
                </div>
                <div class="card-actions">
                  <button class="action-btn btn-primary" onclick="event.stopPropagation(); openFile('${encodeURIComponent(item.path)}')">▶ Open</button>
                </div>
              `;
              localResultsList.appendChild(card);
            });

            // Auto-select first local result
            const cards = document.querySelectorAll('#local-results-list .card');
            if (cards.length > 0) {
              selectedResultIndex = 0;
              cards[0].classList.add('selected');
            }
          }
        } catch (err) {
          console.error("Local search failed:", err);
          localCount.innerText = "Local search failed";
        }
      })();

      // 2. Web Search Execution
      const webSearchPromise = (async () => {
        webCount.innerText = "Searching web...";
        webResultsList.innerHTML = `
          <div class="empty-state">
            <div class="icon">⏳</div>
            <h4>Searching...</h4>
            <p>Querying resursecrestine.ro web API</p>
          </div>
        `;

        try {
          const res = await fetch(`/api/search/web?q=${encodeURIComponent(query)}`);
          const results = await res.json();

          if (results.length === 0) {
            webCount.innerText = "No web matches";
            webResultsList.innerHTML = `
              <div class="empty-state">
                <div class="icon">ℹ️</div>
                <h4>No matches found on web</h4>
                <p>Check spelling or search local database</p>
              </div>
            `;
          } else {
            webCount.innerText = `Found ${results.length} songs`;
            webResultsList.innerHTML = '';

            results.forEach(item => {
              const card = document.createElement('div');
              card.className = 'card';
              
              card.innerHTML = `
                <div class="card-info">
                  <div class="card-title-row">
                    <span class="card-icon">🎵</span>
                    <span class="card-title" title="${item.title}">${item.title}</span>
                  </div>
                  <div class="card-author-line">de ${item.author || 'Anonim'}</div>
                  <div class="card-path" title="Link">${item.url}</div>
                </div>
                <div class="card-actions">
                  <button class="action-btn btn-success" onclick="projectWeb('${item.id}', '${item.slug}', '${item.title_slug}', '${encodeURIComponent(item.title)}')">📺 Project</button>
                  <button class="action-btn btn-secondary" onclick="window.open('${item.url}', '_blank')">🔗 Web</button>
                </div>
              `;
              webResultsList.appendChild(card);
            });
          }
        } catch (err) {
          webCount.innerText = "Web query failed";
          webResultsList.innerHTML = `
            <div class="empty-state">
              <div class="icon">⚠️</div>
              <h4>Search failed</h4>
              <p>Could not connect to Resurse Creștine API</p>
            </div>
          `;
          console.error("Web search failed:", err);
        }
      })();
    }

    async function openFile(encodedPath) {
      try {
        await fetch(`/api/open?path=${encodedPath}`);
        const searchInput = document.getElementById('search-input');
        if (searchInput) {
          searchInput.focus();
          searchInput.select();
        }
      } catch (err) {
        console.error("Error opening file:", err);
      }
    }

    // --- PROJECTION SYSTEM ---

    async function projectLocal(encodedPath, titleEscaped) {
      const searchInput = document.getElementById('search-input');
      if (searchInput) searchInput.blur();
      
      const title = decodeURIComponent(titleEscaped);
      document.getElementById('projector-song-title').innerText = `Now Projecting: ${title} (Local File)`;
      
      const grid = document.getElementById('projector-slides-grid');
      grid.innerHTML = `
        <div class="empty-state" style="grid-column: 1/-1; padding: 20px;">
          <p>Extracting slides on-the-fly...</p>
        </div>
      `;
      document.getElementById('projection-drawer').classList.add('active');

      try {
        const res = await fetch(`/api/slides/local?path=${encodedPath}`);
        projectionSlides = await res.json();
        renderProjectionGrid();
      } catch (err) {
        grid.innerHTML = `<p style="color:#ff6060; grid-column:1/-1;">Failed to load slides: ${err}</p>`;
      }
    }

    async function projectWeb(id, slug, titleSlug, titleEscaped) {
      const searchInput = document.getElementById('search-input');
      if (searchInput) searchInput.blur();
      
      const title = decodeURIComponent(titleEscaped);
      document.getElementById('projector-song-title').innerText = `Now Projecting: ${title} (Web - Resurse Creștine)`;
      
      const grid = document.getElementById('projector-slides-grid');
      grid.innerHTML = `
        <div class="empty-state" style="grid-column: 1/-1; padding: 20px;">
          <p>Downloading lyrics from Resurse Creștine...</p>
        </div>
      `;
      document.getElementById('projection-drawer').classList.add('active');

      try {
        const res = await fetch(`/api/lyrics/web?id=${id}&slug=${slug}&title_slug=${titleSlug}`);
        projectionSlides = await res.json();
        renderProjectionGrid();
      } catch (err) {
        grid.innerHTML = `<p style="color:#ff6060; grid-column:1/-1;">Failed to fetch lyrics: ${err}</p>`;
      }
    }

    function renderProjectionGrid() {
      const grid = document.getElementById('projector-slides-grid');
      grid.innerHTML = '';
      activeProjectionSlideIndex = -1;

      if (projectionSlides.length === 0) {
        grid.innerHTML = `<p style="color:var(--text-muted); grid-column:1/-1; text-align:center;">No slides found in presentation.</p>`;
        return;
      }

      projectionSlides.forEach((slide, index) => {
        const card = document.createElement('div');
        card.className = 'slide-card';
        card.id = `slide-card-${index}`;
        card.onclick = () => sendSlideToProjector(index);

        card.innerHTML = `
          <span class="slide-card-label">${slide.label}</span>
          <span class="slide-card-preview" title="${slide.text.replace(/\\n/g, ' ')}">${slide.text.replace(/\\n/g, ' ')}</span>
        `;
        grid.appendChild(card);
      });
      
      // Auto-project first slide immediately!
      sendSlideToProjector(0);
    }

    async function sendSlideToProjector(index) {
      if (index < 0 || index >= projectionSlides.length) return;

      // Update UI active card
      if (activeProjectionSlideIndex >= 0) {
        const prev = document.getElementById(`slide-card-${activeProjectionSlideIndex}`);
        if (prev) prev.classList.remove('active');
      }
      
      activeProjectionSlideIndex = index;
      const currentCard = document.getElementById(`slide-card-${index}`);
      if (currentCard) {
        currentCard.classList.add('active');
        currentCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }

      const slide = projectionSlides[index];
      try {
        await fetch('/api/projection/current', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ text: slide.text, label: slide.label })
        });
      } catch (err) {
        console.error("Failed to post slide text:", err);
      }
    }

    async function clearProjector() {
      if (activeProjectionSlideIndex >= 0) {
        const prev = document.getElementById(`slide-card-${activeProjectionSlideIndex}`);
        if (prev) prev.classList.remove('active');
        activeProjectionSlideIndex = -1;
      }
      try {
        await fetch('/api/projection/current', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ text: '', label: '' })
        });
      } catch (err) {
        console.error("Failed to clear projector:", err);
      }
    }

    function openProjectorWindow() {
      window.open('/projector', 'PPTFinderProjector', 'width=1024,height=768,resizable=yes,scrollbars=no');
    }

    function closeProjectionDrawer() {
      clearProjector();
      document.getElementById('projection-drawer').classList.remove('active');
    }

    // --- OTHER UI UTILITIES ---

    async function triggerReindex() {
      try {
        const btn = document.getElementById('reindex-btn');
        btn.disabled = true;
        btn.innerText = "Indexing...";
        await fetch('/api/reindex', { method: 'POST' });
        pollStatus();
      } catch (err) {
        console.error("Reindex trigger failed:", err);
      }
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        const state = await res.json();

        const progressBanner = document.getElementById('progress-banner');
        const fill = document.getElementById('progress-fill');
        const percentText = document.getElementById('progress-percent');
        const statusText = document.getElementById('progress-status-text');
        const reindexBtn = document.getElementById('reindex-btn');
        const footerStatus = document.getElementById('footer-status');

        footerStatus.innerText = `Status: ${state.text} | Total Indexed: ${state.total_files} files`;

        if (state.status === "indexing") {
          isIndexing = true;
          reindexBtn.disabled = true;
          reindexBtn.innerText = "Indexing...";
          progressBanner.classList.add('active');
          
          const percent = Math.round(state.progress * 100);
          fill.style.width = `${percent}%`;
          percentText.innerText = `${percent}%`;
          statusText.innerText = state.text;
        } else {
          progressBanner.classList.remove('active');
          reindexBtn.disabled = false;
          reindexBtn.innerText = "Reindex Files";
          
          if (isIndexing) {
            isIndexing = false;
            performSearch();
          }
        }
      } catch (err) {
        console.error("Status polling failed:", err);
      }
    }

    // Set up search-input key listeners
    window.addEventListener('DOMContentLoaded', () => {
      const searchInput = document.getElementById('search-input');
      if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            if (selectedResultIndex === -1) {
              e.preventDefault();
              clearTimeout(searchDebounceTimeout);
              performSearch();
            }
          } else if (e.key === ' ') {
            clearTimeout(searchDebounceTimeout);
            searchDebounceTimeout = setTimeout(performSearch, 50);
          }
        });
        searchInput.addEventListener('input', (e) => {
          if (!searchInput.value.trim()) {
            clearTimeout(searchDebounceTimeout);
            performSearch();
          }
        });
      }
    });

    // --- GLOBAL KEYBOARD NAVIGATOR ---

    document.addEventListener('keydown', function(e) {
      // 1. Settings folder input overrides
      if (document.activeElement && document.activeElement.id === 'new-folder-input') {
        if (e.key === 'Enter') {
          e.preventDefault();
          addFolder();
        }
        return;
      }
      
      // 2. Control projection slide slides if projection drawer is active
      const drawer = document.getElementById('projection-drawer');
      const isSearchFocused = document.activeElement && document.activeElement.id === 'search-input';
      
      if (drawer.classList.contains('active') && projectionSlides.length > 0) {
        if (e.key === 'ArrowRight' || e.key === 'PageDown' || (!isSearchFocused && (e.key === 'Enter' || e.key === ' '))) {
          e.preventDefault();
          const next = activeProjectionSlideIndex + 1;
          if (next < projectionSlides.length) {
            sendSlideToProjector(next);
          }
          return;
        }
        else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
          e.preventDefault();
          const prev = activeProjectionSlideIndex - 1;
          if (prev >= 0) {
            sendSlideToProjector(prev);
          }
          return;
        }
        else if (e.key === 'Escape') {
          e.preventDefault();
          closeProjectionDrawer();
          return;
        }
      }

      // 3. Search Result Navigation (Left Column Local results navigation)
      const cards = document.querySelectorAll('#local-results-list .card');
      if (cards.length === 0) return;
      
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (selectedResultIndex >= 0 && selectedResultIndex < cards.length) {
          cards[selectedResultIndex].classList.remove('selected');
        }
        selectedResultIndex = (selectedResultIndex + 1) % cards.length;
        cards[selectedResultIndex].classList.add('selected');
        cards[selectedResultIndex].scrollIntoView({ block: 'nearest' });
      } 
      else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (selectedResultIndex >= 0 && selectedResultIndex < cards.length) {
          cards[selectedResultIndex].classList.remove('selected');
        }
        selectedResultIndex = (selectedResultIndex - 1 + cards.length) % cards.length;
        cards[selectedResultIndex].classList.add('selected');
        cards[selectedResultIndex].scrollIntoView({ block: 'nearest' });
      } 
      else if (e.key === 'Enter') {
        if (selectedResultIndex >= 0 && selectedResultIndex < cards.length) {
          e.preventDefault();
          const openBtn = cards[selectedResultIndex].querySelector('.btn-primary');
          if (openBtn) openBtn.click();
        }
      }
    });
  </script>

</body>
</html>
"""
    return Response(html_content.replace('{network_url}', network_url), mimetype='text/html')

@app.route('/projector')
def projector_page():
    html = """<!DOCTYPE html>
<html lang="ro">
<head>
  <meta charset="UTF-8">
  <title>Projector Screen</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      background-color: #000;
      color: #fff;
      font-family: 'Arial', sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      overflow: hidden;
    }
    .slide-text {
      font-size: 7vh;
      font-weight: bold;
      text-align: center;
      line-height: 1.45;
      white-space: pre-wrap;
      max-width: 90%;
      transition: opacity 0.2s ease-in-out;
    }
    .slide-label {
      position: absolute;
      bottom: 25px;
      right: 35px;
      font-size: 3vh;
      color: #444;
      font-weight: 500;
    }
  </style>
</head>
<body>
  <div class="slide-text" id="slide-text"></div>
  <div class="slide-label" id="slide-label"></div>
  
  <script>
    let lastTimestamp = 0;
    
    async function poll() {
      try {
        const res = await fetch('/api/projection/current');
        const data = await res.json();
        
        if (data.timestamp !== lastTimestamp) {
          lastTimestamp = data.timestamp;
          
          const textEl = document.getElementById('slide-text');
          const labelEl = document.getElementById('slide-label');
          
          textEl.style.opacity = 0;
          setTimeout(() => {
            textEl.innerHTML = data.text.replace(/\\n/g, '<br>');
            labelEl.innerText = data.label;
            textEl.style.opacity = 1;
          }, 200);
        }
      } catch (err) {
        console.error("Projector poll failed:", err);
      }
    }
    
    setInterval(poll, 400); // Fast 400ms polling for network latency safety
    poll();
  </script>
</body>
</html>
"""
    return Response(html, mimetype='text/html')

@app.route('/api/folders', methods=['GET', 'POST', 'DELETE'])
def folders_api():
    if request.method == 'GET':
        return jsonify(load_config())
        
    elif request.method == 'POST':
        data = request.json
        new_path = data.get('path', '').strip()
        if not new_path:
            return jsonify({"success": False, "error": "Path is empty"})
        
        current = load_config()
        if new_path in current:
            return jsonify({"success": True})
            
        current.append(new_path)
        if save_config(current):
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Could not save config"})
            
    elif request.method == 'DELETE':
        idx_str = request.args.get('index')
        if idx_str is None:
            return jsonify({"success": False, "error": "Index required"})
            
        try:
            idx = int(idx_str)
            current = load_config()
            if 0 <= idx < len(current):
                current.pop(idx)
                save_config(current)
                return jsonify({"success": True})
            return jsonify({"success": False, "error": "Index out of range"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

@app.route('/api/status')
def status_api():
    with indexing_state_lock:
        with index_lock:
            total_count = len(index_data)
        if indexing_state["status"] != "indexing":
            indexing_state["total_files"] = total_count
            indexing_state["text"] = f"Index idle. Total: {total_count} files."
        return jsonify(indexing_state)

@app.route('/api/reindex', methods=['POST'])
def reindex_api():
    with indexing_state_lock:
        if indexing_state["status"] == "indexing":
            return jsonify({"status": "already indexing"})
            
    t = threading.Thread(target=run_indexing_thread)
    t.daemon = True
    t.start()
    return jsonify({"status": "started"})

@app.route('/api/search')
def search_api():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"results": [], "is_numeric": False})
        
    norm_query = normalize_text(query)
    q_words = norm_query.split()
    if not q_words:
        return jsonify({"results": [], "is_numeric": False})
        
    is_numeric = False
    clean_q = re.sub(r'[\s\-_,.]', '', query)
    if clean_q and clean_q.isdigit():
        is_numeric = True
        
    results = []
    with index_lock:
        for file_path, doc in index_data.items():
            title_score = 0
            
            # 1. Potriviri complete de titlu (Prioritate mare)
            if norm_query == doc["norm_title"]:
                title_score += 1000
            elif doc["norm_title"].startswith(norm_query):
                title_score += 500
            elif norm_query in doc["norm_title"]:
                title_score += 250
            else:
                # Potrivire titlu cuvânt cu cuvânt (cu suport fuzzy)
                t_words = title_words_cache.get(file_path, [])
                matched_title_words = 0
                for qw in q_words:
                    if qw in t_words:
                        matched_title_words += 1.0
                    else:
                        for tw in t_words:
                            if is_fuzzy_match(qw, tw):
                                matched_title_words += 0.5
                                break
                if matched_title_words > 0:
                    title_score += 100 * (matched_title_words / len(q_words))
                    
            # Dacă se caută un număr, ignoră complet contextul (dacă titlul nu se potrivește, se sare peste fișier)
            if is_numeric and title_score == 0:
                continue
                
            content_score = 0
            if not is_numeric:
                # 2. Potriviri de conținut (Prioritate mai mică, cu suport fuzzy)
                if doc.get("norm_content"):
                    if norm_query in doc["norm_content"]:
                        content_score += 150 + (10 * doc["norm_content"].count(norm_query))
                    else:
                        # Potrivire conținut cuvânt cu cuvânt (cu suport fuzzy)
                        c_words = words_cache.get(file_path, set())
                        matched_content_words = 0
                        for qw in q_words:
                            if qw in c_words:
                                matched_content_words += 1.0
                                content_score += min(doc["norm_content"].count(qw), 10) * 2
                            else:
                                for cw in c_words:
                                    if is_fuzzy_match(qw, cw):
                                        matched_content_words += 0.5
                                        break
                        if matched_content_words > 0:
                            content_score += 50 * (matched_content_words / len(q_words))
                            
            score = title_score + content_score
            if score > 0:
                results.append({
                    "path": doc["path"],
                    "name": doc["name"],
                    "title": doc["title"],
                    "raw_content": doc.get("content", ""),
                    "title_score": title_score,
                    "content_score": content_score,
                    "score": score
                })
                
    # Sortare rezultate: Cele care au potrivire în titlu vin primele, apoi restul (după scor descrescător)
    results.sort(key=lambda x: (0 if x["title_score"] > 0 else 1, -x["score"]))
    
    limit = 20 if is_numeric else 5
    top_results = results[:limit]
    
    for item in top_results:
        item["snippet"] = get_highlighted_snippet(item["raw_content"], query)
        del item["raw_content"]
        item["folder"] = os.path.dirname(item["path"])
        
    if is_numeric:
        # Grupăm după folder, iar în interior sortăm după titlu descrescător
        top_results.sort(key=lambda x: (x["folder"], -x["title_score"]))
        
    return jsonify({"results": top_results, "is_numeric": is_numeric})

# --- API CĂUTARE ȘI EXTRAGERE WEB (Resurse Creștine) ---

@app.route('/api/search/web')
def web_search_api():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
        
    # Interogare API căutare Resurse Creștine pentru cântece (ID resursă = 2)
    import urllib.parse
    import urllib.request
    
    url = f"https://www.resursecrestine.ro/web-api-search?search_text={urllib.parse.quote(query)}&search_in=2&search_by=filtru-titlu&output=json2"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=4) as res:
            data = json.loads(res.read().decode('utf-8', errors='ignore'))
            
        results = data.get('Results', [])
        formatted = []
        for item in results[:10]: # Limit to top 10 web results
            slug = item.get('slug', 'cantece')
            song_id = item.get('id', '')
            title_slug = item.get('title_slug', '')
            
            # Construire URL valid pentru site-ul original pentru referințe
            full_url = f"https://www.resursecrestine.ro/{slug}/{song_id}/{title_slug}"
            formatted.append({
                "id": song_id,
                "title": item.get('title', 'Unknown'),
                "title_slug": title_slug,
                "author": item.get('author', 'Anonim'),
                "slug": slug,
                "url": full_url
            })
        return jsonify(formatted)
    except Exception as e:
        print("Web search error:", e)
        return jsonify([])

@app.route('/api/lyrics/web')
def web_lyrics_api():
    song_id = request.args.get('id', '').strip()
    slug = request.args.get('slug', 'cantece').strip()
    title_slug = request.args.get('title_slug', '').strip()
    
    if not song_id:
        return jsonify([])
        
    import urllib.request
    url = f"https://www.resursecrestine.ro/{slug}/{song_id}/{title_slug}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            html = res.read().decode('utf-8', errors='ignore')
            
        # Analizare slide-uri cântece utilizând maparea plată a listelor
        slides = []
        labels = re.findall(r'<div[^>]*class=["\']strofa-label["\'][^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
        texts = re.findall(r'<div[^>]*class=["\']strofa-text["\'][^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
        
        if labels and texts and len(labels) == len(texts):
            for label, text_html in zip(labels, texts):
                label = re.sub(r'<[^>]+>', '', label).strip()
                text = re.sub(r'<br\s*/?>', '\n', text_html, flags=re.IGNORECASE)
                text = re.sub(r'<[^>]+>', '', text).strip()
                slides.append({
                    "label": label,
                    "text": text
                })
                
        # Soluție de rezervă (fallback) la text redimensionat
        if not slides:
            match = re.search(r'<div[^>]*class=["\']resized-text["\'][^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
            if match:
                content_html = match.group(1)
                paragraphs = re.split(r'(?:<br\s*/?>\s*){2,}', content_html, flags=re.IGNORECASE)
                for idx, p in enumerate(paragraphs, 1):
                    text = re.sub(r'<br\s*/?>', '\n', p, flags=re.IGNORECASE)
                    text = re.sub(r'<[^>]+>', '', text).strip()
                    if text:
                        lines = text.split('\n')
                        first_line = lines[0].strip()
                        if re.match(r'^(?:Refren|R\d*|Chorus|Strof\u0103|\d+\.|\d+)\b', first_line, re.IGNORECASE):
                            label = first_line
                            text = "\n".join(lines[1:]).strip()
                        else:
                            label = f"Strofa {idx}"
                        slides.append({
                            "label": label,
                            "text": text
                        })
        return jsonify(slides)
    except Exception as e:
        print("Web lyrics parse error:", e)
        return jsonify([])

# --- RECUPERARE SLIDE-URI LOCALE (stocare cache din mers) ---

@app.route('/api/slides/local')
def local_slides_api():
    encoded_path = request.args.get('path', '')
    if not encoded_path:
        return jsonify([])
    import urllib.parse
    file_path = urllib.parse.unquote(encoded_path)
    
    with index_lock:
        doc = index_data.get(file_path)
        if not doc:
            return jsonify([])
            
        if "slides" in doc and doc["slides"]:
            return jsonify(doc["slides"])
            
    slides = []
    if os.path.exists(file_path):
        if file_path.lower().endswith('.pptx'):
            slides = extract_pptx_slides(file_path)
        elif file_path.lower().endswith('.ppt'):
            carved_text = extract_ppt_text_carving(file_path)
            slides = [p.strip() for p in re.split(r'\n\n+', carved_text) if p.strip()]
            
            if not slides:
                try:
                    import win32com.client
                    import pythoncom
                    pythoncom.CoInitialize()
                    ppt_app = win32com.client.Dispatch("PowerPoint.Application")
                    slides = extract_ppt_slides_com(ppt_app, file_path)
                    ppt_app.Quit()
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
                    
    formatted_slides = []
    for idx, s in enumerate(slides):
        if isinstance(s, dict):
            formatted_slides.append(s)
        else:
            formatted_slides.append({
                "label": f"Slide {idx+1}",
                "text": s
            })
            
    if formatted_slides:
        with index_lock:
            if file_path in index_data:
                index_data[file_path]["slides"] = formatted_slides
                save_index()
                
    return jsonify(formatted_slides)

# --- API CONTROL PROIECȚIE ---

@app.route('/api/projection/current', methods=['GET', 'POST'])
def projection_current_api():
    global projection_state
    if request.method == 'POST':
        data = request.json
        with projection_state_lock:
            projection_state["text"] = data.get("text", "")
            projection_state["label"] = data.get("label", "")
            projection_state["timestamp"] = time.time()
        return jsonify({"success": True})
    else:
        with projection_state_lock:
            return jsonify(projection_state)

# --- UTILITĂȚI SISTEM ---

@app.route('/api/open')
def open_file_api():
    encoded_path = request.args.get('path', '')
    if not encoded_path:
        return jsonify({"success": False, "error": "Path required"})
    import urllib.parse
    path = urllib.parse.unquote(encoded_path)
    if os.path.exists(path):
        try:
            os.startfile(path)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    return jsonify({"success": False, "error": "File not found"})

@app.route('/api/reveal')
def reveal_file_api():
    encoded_path = request.args.get('path', '')
    if not encoded_path:
        return jsonify({"success": False, "error": "Path required"})
    import urllib.parse
    path = urllib.parse.unquote(encoded_path)
    if os.path.exists(path):
        try:
            subprocess.run(['explorer', '/select,', os.path.normpath(path)])
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    return jsonify({"success": False, "error": "File not found"})

# --- INTRAREA PRINCIPALĂ SERVER (MAIN) ---

def get_free_port(start_port=8080):
    port = start_port
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', port))
            s.close()
            return port
        except socket.error:
            port += 1

if __name__ == '__main__':
    load_index()
    
    if not index_data:
        t = threading.Thread(target=run_indexing_thread)
        t.daemon = True
        t.start()
        
    port = get_free_port(8080)
    url = f"http://127.0.0.1:{port}"
    local_ip = get_local_ip()
    network_url = f"http://{local_ip}:{port}"
    
    print("=" * 60)
    print(f"PPT Finder Desktop started.")
    print(f"Local Access: {url}")
    print("=" * 60)
    
    # Rulare Flask pe toate interfețele (0.0.0.0) pentru a fi accesibil în rețea!
    def run_flask():
        app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
        
    t_flask = threading.Thread(target=run_flask)
    t_flask.daemon = True
    t_flask.start()
    
    # Deschidere fereastră locală PyWebView Desktop
    import webview
    time.sleep(0.5)
    webview.create_window(
        title='PPT Finder Desktop',
        url=url,
        width=960,
        height=650,
        resizable=True,
        min_size=(450, 400)
    )
    webview.start()
