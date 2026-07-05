import os
import sys
import time
import json
import re
import unicodedata
import threading
import subprocess
import queue
from concurrent.futures import ThreadPoolExecutor

# Biblioteca GUI
import customtkinter as ctk
import tkinter as tk

# Configurare aspect și temă
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Constante
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.txt')
INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ppt_index.json')

# Index global și Lock de sincronizare
index_data = {}
index_lock = threading.Lock()

# Coadă de comunicare UI sigură pentru execuție pe fire (thread-safe)
ui_queue = queue.Queue()

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
            
    # Cale implicită dacă fișierul de configurare lipsește sau este invalid
    default_paths = [r"d:\Advanced search\Cântări Comun"]
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

# --- NORMALIZARE TEXT ---

def normalize_text(text):
    if not text:
        return ""
    text = text.lower()
    # Mapare explicită pentru caracterele speciale românești (diacritice)
    replacements = {
        'ă': 'a', 'â': 'a', 'î': 'i', 'ș': 's', 'ț': 't',
        'ş': 's', 'ţ': 't', 'ă': 'a', 'â': 'a', 'î': 'i',
        'ș': 's', 'ț': 't'
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Eliminare generică a diacriticelor Unicode
    nfkd_form = unicodedata.normalize('NFKD', text)
    text = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    # Eliminare semne de punctuație și caractere speciale, păstrare cuvinte și spații
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    # Reducere spații albe multiple la un singur spațiu
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# --- EXTRAGERE TEXT ---

def extract_pptx_text(file_path):
    try:
        import pptx
        prs = pptx.Presentation(file_path)
        text_runs = []
        
        def extract_text_from_shape(shape):
            # Text din formă (shape)
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        if run.text.strip():
                            text_runs.append(run.text.strip())
            # Text din tabel
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            text_runs.append(cell.text.strip())
            # Text din grupuri de forme recursiv
            if hasattr(shape, "shapes"):
                for sub_shape in shape.shapes:
                    extract_text_from_shape(sub_shape)
                    
        for slide in prs.slides:
            for shape in slide.shapes:
                extract_text_from_shape(shape)
                
        return "\n".join(text_runs)
    except Exception:
        return ""

def extract_ppt_text_carving(file_path):
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
            
        # Șablon pentru șiruri UTF-16-LE care includ blocuri latine de bază și extinse
        pattern = rb'((?:[\x20-\x7e\xa0-\xff]\x00|[\x00-\xff][\x01-\x04]){4,})'
        extracted_lines = []
        seen_lines = set()
        
        # Cuvinte de sistem/formatare de filtrat
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
                
        # Soluție de rezervă (fallback) la șiruri ASCII dacă nu se găsesc șiruri Unicode
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
    pres = None
    try:
        file_path_abs = os.path.abspath(file_path)
        pres = ppt_app.Presentations.Open(file_path_abs, ReadOnly=True, WithWindow=False)
        text_runs = []
        
        def extract_text_from_com_shape(shape):
            # Conține text
            if shape.HasTextFrame and shape.TextFrame.HasText:
                text_runs.append(shape.TextFrame.TextRange.Text)
            # Conține tabel
            if shape.HasTable:
                table = shape.Table
                for r_idx in range(1, table.Rows.Count + 1):
                    for c_idx in range(1, table.Columns.Count + 1):
                        cell = table.Cell(r_idx, c_idx)
                        if cell.Shape.HasTextFrame and cell.Shape.TextFrame.HasText:
                            text_runs.append(cell.Shape.TextFrame.TextRange.Text)
            # Grup de forme
            if shape.Type == 6: # msoGroup
                try:
                    for sub_shape in shape.GroupItems:
                        extract_text_from_com_shape(sub_shape)
                except Exception:
                    pass

        for slide in pres.Slides:
            for shape in slide.Shapes:
                extract_text_from_com_shape(shape)
                
        try:
            pres.Close()
        except Exception:
            pass
        return "\n".join(text_runs)
    except Exception:
        if pres:
            try:
                pres.Close()
            except Exception:
                pass
        return ""

# --- EVIDENȚIERE SECVENȚĂ (SNIPPET) ---

def get_highlighted_snippet(content, query):
    if not content:
        return ""
        
    q_words = normalize_text(query).split()
    if not q_words:
        return content[:150] + "..." if len(content) > 150 else content
        
    # Împărțire conținut în paragrafe/linii
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    best_line = ""
    max_matches = -1
    
    # Găsire linie cu cele mai multe potriviri de cuvinte
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
        
    # Evidențiere potriviri păstrând scrierea originală (majuscule/minuscule) și diacriticele
    words = re.findall(r'\w+|\W+', best_line)
    highlighted_words = []
    for word in words:
        if word.isalnum():
            word_norm = normalize_text(word)
            matched = False
            for qw in q_words:
                if qw == word_norm or (len(qw) > 3 and qw in word_norm) or (len(word_norm) > 3 and word_norm in qw):
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
    global index_data
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
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
    global index_data
    
    ui_queue.put({"type": "status", "status": "indexing", "text": "Scanning folders...", "progress": 0.0})
    
    folders = load_config()
    all_files = []
    
    # Căutare recursivă multi-threaded în foldere (un fir pe director de căutare)
    with ThreadPoolExecutor(max_workers=max(len(folders), 1)) as executor:
        results = executor.map(scan_single_folder, folders)
        for res in results:
            all_files.extend(res)
            
    total_files = len(all_files)
    if total_files == 0:
        ui_queue.put({"type": "status", "status": "completed", "text": "Index completed. No presentations found.", "progress": 1.0})
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
        
        # Dacă fișierul este deja indexat complet și nemodificat, păstrăm cache-ul
        if file_path in index_data and index_data[file_path].get("mtime") == mtime:
            new_index_data[file_path] = index_data[file_path]
        else:
            # Păstrare imediată a posibilității de căutare la nivel de titlu
            existing_content = ""
            existing_norm_content = ""
            if file_path in index_data:
                existing_content = index_data[file_path].get("content", "")
                existing_norm_content = index_data[file_path].get("norm_content", "")
                
            new_index_data[file_path] = {
                "path": file_path,
                "name": file_name,
                "title": title_no_ext,
                "norm_title": normalize_text(title_no_ext),
                "mtime": 0,
                "content": existing_content,
                "norm_content": existing_norm_content
            }
            files_to_process.append((file_path, mtime))
            
    # Salvare imediată a rezultatelor Fazei 1 pentru a fi căutate după titlu
    with index_lock:
        index_data = new_index_data
        save_index()
        
    processed_count = total_files - len(files_to_process)
    progress_val = processed_count / total_files
    ui_queue.put({
        "type": "status",
        "status": "indexing",
        "text": f"Indexing: {processed_count}/{total_files} files ({int(progress_val*100)}%)",
        "progress": progress_val
    })
    
    if not files_to_process:
        ui_queue.put({
            "type": "status",
            "status": "completed",
            "text": f"Index completed. Total: {total_files} files.",
            "progress": 1.0
        })
        return

    # Faza 2: Extragere conținut diapozitive (slide-uri)
    # Procesăm concurent atât .pptx (XML nativ în zip) cât și .ppt (extragere brută - carving) într-un ThreadPool
    com_fallback_queue = []
    fallback_lock = threading.Lock()
    write_lock = threading.Lock()
    
    def process_file_worker(item):
        nonlocal processed_count
        file_path, mtime = item
        file_name = os.path.basename(file_path)
        title_no_ext, _ = os.path.splitext(file_name)
        
        content = ""
        is_pptx = file_path.lower().endswith('.pptx')
        
        if is_pptx:
            content = extract_pptx_text(file_path)
        else:
            content = extract_ppt_text_carving(file_path)
            
        # Dacă este un fișier PPT și extragerea brută nu a întors nimic, îl punem în coadă pentru rezerva COM
        if not is_pptx and not content.strip():
            with fallback_lock:
                com_fallback_queue.append(item)
            return
            
        with write_lock:
            with index_lock:
                index_data[file_path] = {
                    "path": file_path,
                    "name": file_name,
                    "title": title_no_ext,
                    "norm_title": normalize_text(title_no_ext),
                    "mtime": mtime,
                    "content": content,
                    "norm_content": normalize_text(content)
                }
            processed_count += 1
            progress_val = processed_count / total_files
            
            percent_step = max(1, total_files // 100)
            if processed_count % percent_step == 0 or processed_count == total_files:
                ui_queue.put({
                    "type": "status",
                    "status": "indexing",
                    "text": f"Indexing: {processed_count}/{total_files} files ({int(progress_val*100)}%)",
                    "progress": progress_val
                })
                
            if processed_count % 250 == 0:
                with index_lock:
                    save_index()

    # Pornire extragere paralelă (ajustată la nucleele CPU, limitată la 4 lucrători pentru hardware slab)
    max_threads = min(4, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        executor.map(process_file_worker, files_to_process)
        
    # Faza 3: Rezervă (fallback) secvențială COM pentru fișierele unde extragerea brută a eșuat
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
            
            content = ""
            if ppt_app:
                content = extract_ppt_text_com(ppt_app, file_path)
                
            with index_lock:
                index_data[file_path] = {
                    "path": file_path,
                    "name": file_name,
                    "title": title_no_ext,
                    "norm_title": normalize_text(title_no_ext),
                    "mtime": mtime,
                    "content": content,
                    "norm_content": normalize_text(content)
                }
                
            processed_count += 1
            progress_val = processed_count / total_files
            
            percent_step = max(1, total_files // 100)
            if processed_count % percent_step == 0 or processed_count == total_files:
                ui_queue.put({
                    "type": "status",
                    "status": "indexing",
                    "text": f"Indexing: {processed_count}/{total_files} files ({int(progress_val*100)}%)",
                    "progress": progress_val
                })
            
            # Salvare periodică a indexului (frecvență redusă pentru protejarea hardware-ului slab)
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

    # Salvare finală și finalizare
    with index_lock:
        save_index()
        
    ui_queue.put({
        "type": "status",
        "status": "completed",
        "text": f"Index completed. Total: {total_files} files.",
        "progress": 1.0
    })

# --- APLICATIE DESKTOP GUI ---

class PPTFinderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Configurare fereastră
        self.title("PPT Finder")
        self.geometry("850x680")
        self.minsize(750, 550)
        
        # Stare de selecție pentru navigare din tastatură
        self.current_results = []
        self.selected_idx = -1
        self.card_widgets = []
        self.search_debounce_job = None
        
        # Configurare grilă (grid)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # -- RÂNDUL 0: Antet Titlu --
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=25, pady=(20, 10))
        self.header_frame.grid_columnconfigure(0, weight=1)
        
        self.title_label = ctk.CTkLabel(
            self.header_frame, 
            text="PPT Finder", 
            font=ctk.CTkFont(family="Inter", size=24, weight="bold"),
            text_color="#00b0ff"
        )
        self.title_label.grid(row=0, column=0, sticky="w")
        
        # -- RÂNDUL 1: Casetă Căutare --
        self.search_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.search_frame.grid(row=1, column=0, sticky="ew", padx=25, pady=10)
        self.search_frame.grid_columnconfigure(0, weight=1)
        
        self.search_entry = ctk.CTkEntry(
            self.search_frame,
            placeholder_text="Type to search song titles or slide contents...",
            font=ctk.CTkFont(family="Inter", size=15),
            height=48,
            border_width=1,
            corner_radius=10
        )
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.search_entry.bind("<KeyRelease>", self.on_search_key_release)
        
        # -- RÂNDUL 2: Panou Afișare Rezultate --
        # Banner indicații (tip)
        self.tip_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.tip_frame.grid(row=2, column=0, sticky="nsew", padx=25, pady=(5, 10))
        self.tip_frame.grid_rowconfigure(1, weight=1)
        self.tip_frame.grid_columnconfigure(0, weight=1)
        
        self.result_count_label = ctk.CTkLabel(
            self.tip_frame,
            text="Type to search",
            font=ctk.CTkFont(family="Inter", size=12, weight="bold"),
            text_color="#8d8da0"
        )
        self.result_count_label.grid(row=0, column=0, sticky="w")
        
        self.nav_tip_label = ctk.CTkLabel(
            self.tip_frame,
            text="Use ↑ / ↓ arrow keys to select, press Enter to open",
            font=ctk.CTkFont(family="Inter", size=11),
            text_color="#636375"
        )
        self.nav_tip_label.grid(row=0, column=0, sticky="e")
        self.nav_tip_label.grid_remove() # hide initially
        
        # Container scrollabil pentru rezultate
        self.scroll_frame = ctk.CTkScrollableFrame(
            self.tip_frame,
            fg_color="transparent",
            border_width=0
        )
        self.scroll_frame.grid(row=1, column=0, sticky="nsew", pady=(5, 0))
        
        # Afișare stare implicită în containerul scrollabil
        self.render_empty_state()
        
        # -- RÂNDUL 3: Subsol Stare --
        self.footer_frame = ctk.CTkFrame(self, height=50, corner_radius=0, fg_color="#12121e")
        self.footer_frame.grid(row=3, column=0, sticky="ew")
        self.footer_frame.grid_columnconfigure(0, weight=1)
        self.footer_frame.grid_propagate(False)
        
        self.status_label = ctk.CTkLabel(
            self.footer_frame,
            text="Status: Loading index...",
            font=ctk.CTkFont(family="Inter", size=12),
            text_color="#8d8da0"
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=25, pady=12)
        
        self.progress_bar = ctk.CTkProgressBar(
            self.footer_frame,
            width=200,
            height=6,
            corner_radius=3,
            progress_color="#00b0ff"
        )
        self.progress_bar.grid(row=0, column=1, padx=(0, 20), pady=12)
        self.progress_bar.set(0.0)
        self.progress_bar.grid_remove() # Hide initially
        
        self.reindex_button = ctk.CTkButton(
            self.footer_frame,
            text="Reindex Files",
            font=ctk.CTkFont(family="Inter", size=12, weight="bold"),
            width=110,
            height=28,
            corner_radius=6,
            command=self.start_reindexing
        )
        self.reindex_button.grid(row=0, column=2, padx=20, pady=11)
        
        # Legare taste pentru navigare globală din tastatură
        self.bind("<Down>", self.navigate_selection_down)
        self.bind("<Up>", self.navigate_selection_up)
        self.bind("<Return>", self.open_selected_result)
        
        # Încărcare index și inițializare interogare periodică (polling) a stării
        load_index()
        self.update_status_bar()
        self.check_queue()
        
        # Dacă indexul este gol, pornește automat indexarea
        if not index_data:
            self.start_reindexing()

    # --- VERIFICATOR COADĂ UI (Thread Safe) ---

    def check_queue(self):
        latest_msg = None
        try:
            while True:
                msg = ui_queue.get_nowait()
                latest_msg = msg
        except queue.Empty:
            pass
            
        if latest_msg:
            if latest_msg["type"] == "status":
                self.status_label.configure(text=f"Status: {latest_msg['text']}")
                if latest_msg["status"] == "indexing":
                    self.progress_bar.grid()
                    self.progress_bar.set(latest_msg["progress"])
                    self.reindex_button.configure(state="disabled", text="Indexing...")
                else:  # completed / error
                    self.progress_bar.grid_remove()
                    self.reindex_button.configure(state="normal", text="Reindex Files")
                    self.perform_search()
                    
        self.after(100, self.check_queue)

    def update_status_bar(self):
        with index_lock:
            count = len(index_data)
        self.status_label.configure(text=f"Status: Index idle. Total: {count} files.")

    def start_reindexing(self):
        t = threading.Thread(target=run_indexing_thread)
        t.daemon = True
        t.start()

    # --- IMPLEMENTARE CĂUTARE ---

    def on_search_key_release(self, event):
        # Ignorare taste modificatoare / taste săgeți
        if event.keysym in ("Up", "Down", "Return", "Left", "Right", "Shift_L", "Shift_R", "Control_L", "Control_R"):
            return
            
        # Debounce la căutare pentru a preveni sacadarea UI-ului în timp ce se tastează
        if self.search_debounce_job:
            self.after_cancel(self.search_debounce_job)
        self.search_debounce_job = self.after(250, self.perform_search)

    def perform_search(self):
        query = self.search_entry.get().strip()
        if not query:
            self.render_empty_state()
            return
            
        norm_query = normalize_text(query)
        q_words = norm_query.split()
        if not q_words:
            self.render_empty_state()
            return
            
        results = []
        with index_lock:
            for file_path, doc in index_data.items():
                score = 0
                # Potrivire exactă titlu
                if norm_query == doc["norm_title"]:
                    score += 1000
                # Titlul începe cu termenul căutat
                elif doc["norm_title"].startswith(norm_query):
                    score += 500
                # Titlul conține termenul căutat
                elif norm_query in doc["norm_title"]:
                    score += 250
                # Cuvinte individuale în titlu
                else:
                    title_words = doc["norm_title"].split()
                    matched_title_words = sum(1 for w in q_words if w in title_words)
                    if matched_title_words > 0:
                        score += 100 * (matched_title_words / len(q_words))
                        
                # Potriviri de conținut
                if doc["norm_content"]:
                    # Conținutul conține exact termenul căutat
                    if norm_query in doc["norm_content"]:
                        score += 150 + (10 * doc["norm_content"].count(norm_query))
                    # Cuvinte individuale în conținut
                    content_words_matched = sum(1 for w in q_words if w in doc["norm_content"])
                    if content_words_matched > 0:
                        score += 50 * (content_words_matched / len(q_words))
                        for w in q_words:
                            score += min(doc["norm_content"].count(w), 10) * 2
                            
                if score > 0:
                    results.append({
                        "path": doc["path"],
                        "name": doc["name"],
                        "title": doc["title"],
                        "raw_content": doc["content"],
                        "score": score
                    })
                    
        # Sortare și selectare primele 5
        results.sort(key=lambda x: x["score"], reverse=True)
        top_5 = results[:5]
        
        # Calculare evidențieri secvențe doar pentru primele 5 rezultate sortate
        for item in top_5:
            item["snippet"] = get_highlighted_snippet(item["raw_content"], query)
            del item["raw_content"]
            
        self.current_results = top_5
        self.selected_idx = -1
        
        self.render_results()

    # --- RENDERING INTERFAȚĂ (UI) ---

    def render_empty_state(self):
        self.current_results = []
        self.selected_idx = -1
        self.card_widgets = []
        self.result_count_label.configure(text="Enter a query to search")
        self.nav_tip_label.grid_remove()
        
        # Curățare container scrollabil
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
            
        # Banner implicit
        empty_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        empty_frame.pack(fill="both", expand=True, pady=60)
        
        icon_label = ctk.CTkLabel(
            empty_frame, 
            text="🔍", 
            font=ctk.CTkFont(size=48)
        )
        icon_label.pack(pady=10)
        
        msg_label = ctk.CTkLabel(
            empty_frame,
            text="Type above to search presentations by title and slide content",
            font=ctk.CTkFont(family="Inter", size=14, weight="bold"),
            text_color="#8d8da0"
        )
        msg_label.pack(pady=5)
        
        submsg_label = ctk.CTkLabel(
            empty_frame,
            text="The app automatically ignores Romanian characters (ă, â, î, ș, ț) during search.",
            font=ctk.CTkFont(family="Inter", size=12),
            text_color="#636375"
        )
        submsg_label.pack(pady=2)

    def render_results(self):
        # Curățare widgeturi anterioare
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        self.card_widgets = []
        
        if not self.current_results:
            self.result_count_label.configure(text="No presentations found")
            self.nav_tip_label.grid_remove()
            
            no_results_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
            no_results_frame.pack(fill="both", expand=True, pady=60)
            
            icon_label = ctk.CTkLabel(no_results_frame, text="ℹ️", font=ctk.CTkFont(size=40))
            icon_label.pack(pady=10)
            
            msg_label = ctk.CTkLabel(
                no_results_frame,
                text="No matches found",
                font=ctk.CTkFont(family="Inter", size=14, weight="bold"),
                text_color="#8d8da0"
            )
            msg_label.pack(pady=5)
            
            submsg_label = ctk.CTkLabel(
                no_results_frame,
                text="Try refining your query or click 'Reindex Files' to update folders.",
                font=ctk.CTkFont(family="Inter", size=12),
                text_color="#636375"
            )
            submsg_label.pack(pady=2)
            return

        self.result_count_label.configure(text=f"Top {len(self.current_results)} results")
        self.nav_tip_label.grid()
        
        for idx, item in enumerate(self.current_results):
            # Cadru card exterior
            card = ctk.CTkFrame(
                self.scroll_frame, 
                fg_color="#12121e", 
                border_width=1, 
                border_color="rgba(255, 255, 255, 0.08)",
                corner_radius=10
            )
            card.pack(fill="x", pady=5, padx=2)
            self.card_widgets.append(card)
            
            # Aspect conținut stânga
            info_frame = ctk.CTkFrame(card, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True, padx=15, pady=12)
            
            title_row = ctk.CTkFrame(info_frame, fg_color="transparent")
            title_row.pack(fill="x", anchor="w")
            
            file_icon = "📊" if item["name"].lower().endswith('.pptx') else "📄"
            title_lbl = ctk.CTkLabel(
                title_row,
                text=f"{file_icon}  {item['title']}",
                font=ctk.CTkFont(family="Inter", size=14, weight="bold"),
                text_color="#f0f0f5",
                anchor="w"
            )
            title_lbl.pack(side="left", anchor="w")
            
            path_lbl = ctk.CTkLabel(
                info_frame,
                text=item["path"],
                font=ctk.CTkFont(family="Inter", size=11),
                text_color="#8d8da0",
                anchor="w"
            )
            path_lbl.pack(fill="x", anchor="w", pady=(2, 4))
            
            # Widget text secvență (snippet)
            if item["snippet"]:
                snippet_text = tk.Text(
                    info_frame,
                    font=("Inter", 10),
                    bg="#0a0a0f",
                    fg="#d1d1e0",
                    height=2,
                    relief="flat",
                    borderwidth=0,
                    highlightthickness=0,
                    padx=8,
                    pady=6,
                    wrap="word"
                )
                snippet_text.pack(fill="x", anchor="w", pady=(2, 0))
                self.insert_highlighted_text(snippet_text, item["snippet"])
                
            actions_frame = ctk.CTkFrame(card, fg_color="transparent")
            actions_frame.pack(side="right", padx=15, pady=12, fill="y")
            
            open_btn = ctk.CTkButton(
                actions_frame,
                text="▶ Open",
                font=ctk.CTkFont(family="Inter", size=11, weight="bold"),
                width=80,
                height=28,
                corner_radius=5,
                command=lambda p=item["path"]: self.open_presentation(p)
            )
            open_btn.pack(pady=2)
            
            reveal_btn = ctk.CTkButton(
                actions_frame,
                text="📁 Reveal",
                font=ctk.CTkFont(family="Inter", size=11),
                width=80,
                height=28,
                corner_radius=5,
                fg_color="#1b1b2f",
                hover_color="#2b2b3f",
                command=lambda p=item["path"]: self.reveal_in_explorer(p)
            )
            reveal_btn.pack(pady=2)
            
            # Asocieri clic mouse pentru selecție
            # Asocieri clic pe etichete și fundal card pentru a selecta acel index
            def make_select_callback(index):
                return lambda e: self.select_card_by_index(index)
                
            card.bind("<Button-1>", make_select_callback(idx))
            info_frame.bind("<Button-1>", make_select_callback(idx))
            title_lbl.bind("<Button-1>", make_select_callback(idx))
            path_lbl.bind("<Button-1>", make_select_callback(idx))
            
            # Dublu clic pentru a deschide prezentarea direct
            def make_open_callback(path):
                return lambda e: self.open_presentation(path)
                
            card.bind("<Double-Button-1>", make_open_callback(item["path"]))
            info_frame.bind("<Double-Button-1>", make_open_callback(item["path"]))
            title_lbl.bind("<Double-Button-1>", make_open_callback(item["path"]))

    def insert_highlighted_text(self, text_widget, raw_text):
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")
        
        # Regex pentru localizarea etichetelor <mark>...</mark>
        pattern = re.compile(r'(.*?)<mark>(.*?)</mark>', re.IGNORECASE | re.DOTALL)
        last_end = 0
        
        for match in pattern.finditer(raw_text):
            pre_text = match.group(1)
            mark_text = match.group(2)
            
            if pre_text:
                text_widget.insert("end", pre_text)
            
            start_index = text_widget.index("end-1c")
            text_widget.insert("end", mark_text)
            end_index = text_widget.index("end-1c")
            text_widget.tag_add("mark", start_index, end_index)
            
            last_end = match.end()
            
        post_text = raw_text[last_end:]
        if post_text:
            text_widget.insert("end", post_text)
            
        # Stilare etichete evidențiate cu mark
        text_widget.tag_config("mark", background="rgba(0, 176, 255, 0.25)", foreground="#00e5ff", font=("Inter", 10, "bold"))
        text_widget.configure(state="disabled")

    # --- SELECȚIE ȘI NAVIGARE CARDURI ---

    def select_card_by_index(self, index):
        if not self.card_widgets:
            return
            
        # Resetare bordură selecție anterioară
        if self.selected_idx >= 0 and self.selected_idx < len(self.card_widgets):
            self.card_widgets[self.selected_idx].configure(
                fg_color="#12121e", 
                border_color="rgba(255, 255, 255, 0.08)"
            )
            
        self.selected_idx = index
        if self.selected_idx < 0:
            self.selected_idx = 0
        if self.selected_idx >= len(self.card_widgets):
            self.selected_idx = len(self.card_widgets) - 1
            
        # Evidențiere card selectat
        selected_card = self.card_widgets[self.selected_idx]
        selected_card.configure(
            fg_color="#1b1b2f", 
            border_color="#00b0ff"
        )
        
        # Derulare cadru scrollabil pentru a afișa cardul selectat dacă este trunchiat
        # CTkScrollableFrame nu are scroll_to, dar putem face o solicitare vizuală
        # Prin metodele standard Tkinter: selected_card.focus() sau yview
        pass

    def navigate_selection_down(self, event):
        if not self.current_results:
            return
        new_idx = self.selected_idx + 1
        if new_idx >= len(self.current_results):
            new_idx = len(self.current_results) - 1
        self.select_card_by_index(new_idx)

    def navigate_selection_up(self, event):
        if not self.current_results:
            return
        new_idx = self.selected_idx - 1
        if new_idx < 0:
            new_idx = 0
        self.select_card_by_index(new_idx)

    def open_selected_result(self, event):
        if self.selected_idx >= 0 and self.selected_idx < len(self.current_results):
            self.open_presentation(self.current_results[self.selected_idx]["path"])

    # --- API ACȚIUNI ---

    def open_presentation(self, file_path):
        if not file_path or not os.path.exists(file_path):
            tk.messagebox.showerror("Error", f"File not found:\n{file_path}")
            return
        try:
            os.startfile(file_path)
        except Exception as e:
            tk.messagebox.showerror("Error", f"Failed to start presentation:\n{e}")

    def reveal_in_explorer(self, file_path):
        if not file_path or not os.path.exists(file_path):
            tk.messagebox.showerror("Error", f"File not found:\n{file_path}")
            return
        try:
            norm_path = os.path.normpath(file_path)
            subprocess.run(['explorer', '/select,', norm_path])
        except Exception as e:
            tk.messagebox.showerror("Error", f"Failed to open Explorer:\n{e}")

# --- EXECUTABIL PRINCIPAL (MAIN) ---

if __name__ == "__main__":
    # Asigurare existență fișier configurare
    load_config()
    
    # Rulare aplicație desktop
    app = PPTFinderApp()
    app.mainloop()
