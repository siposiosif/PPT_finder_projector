import re
import html
import unicodedata
from collections import defaultdict


def normalize_text(text):
    """Normalize text by converting to lowercase, stripping diacritics and special characters."""
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
    text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_song_number(query):
    """
    Detect whether query represents a song/hymn number.
    Matches: '124', '#124', 'c 124', 'cantarea 124', 'c. 124', 'nr 124', 'no. 124', etc.
    Returns: integer string if found, else None
    """
    clean = query.strip()
    # Pure digit
    if clean.isdigit():
        return clean

    # Prefix pattern
    match = re.match(r'^(?:#|c\b|c\.|cantarea\b|cantecul\b|imnul\b|song\b|hymn\b|nr\.?|no\.?)\s*(\d+)$', clean, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def levenshtein_distance(s1, s2):
    """Compute Levenshtein edit distance between two strings."""
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
    """Fast heuristic fuzzy matching with early length and character set pruning."""
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
        
    return levenshtein_distance(q_word, target_word) <= max_dist


class SearchIndex:
    """Inverted search index for high-speed presentation querying."""
    
    def __init__(self):
        self.title_index = defaultdict(set)     # word -> set of paths
        self.content_index = defaultdict(set)   # word -> set of paths
        self.title_words_cache = {}             # path -> list of title words
        self.content_words_cache = {}           # path -> set of content words
        self.title_numbers_cache = {}           # path -> set of numbers in title
        self.all_paths = set()

    def clear(self):
        self.title_index.clear()
        self.content_index.clear()
        self.title_words_cache.clear()
        self.content_words_cache.clear()
        self.title_numbers_cache.clear()
        self.all_paths.clear()

    def build_from_documents(self, documents):
        """Rebuild inverted index from a dict of document items."""
        self.clear()
        for path, doc in documents.items():
            self.add_document(path, doc)

    def add_document(self, path, doc):
        """Add or update a single document in the index."""
        norm_title = doc.get("norm_title", "")
        norm_content = doc.get("norm_content", "")
        
        t_words = norm_title.split()
        c_words = set(norm_content.split())
        
        # Extract all numbers from title for instant numeric matching
        numbers_in_title = set(re.findall(r'\b\d+\b', doc.get("title", "") + " " + norm_title))
        
        self.title_words_cache[path] = t_words
        self.content_words_cache[path] = c_words
        self.title_numbers_cache[path] = numbers_in_title
        self.all_paths.add(path)
        
        for w in t_words:
            self.title_index[w].add(path)
        for w in c_words:
            self.content_index[w].add(path)

    def search(self, query, index_data, limit=None):
        """
        Execute ranked query across index.
        Returns: (results_list, is_numeric_search)
        """
        query = query.strip()
        if not query:
            return [], False

        norm_query = normalize_text(query)
        q_words = norm_query.split()
        if not q_words:
            return [], False

        song_num = extract_song_number(query)
        is_numeric = song_num is not None

        if limit is None:
            limit = 20 if is_numeric else 10

        scored_candidates = {}

        if is_numeric:
            # Numeric search: Match presentations with this song number in title or name
            target_num = song_num
            for path, numbers in self.title_numbers_cache.items():
                if target_num in numbers:
                    doc = index_data.get(path)
                    if not doc:
                        continue
                    # Prioritize presentations whose title starts with or equals the number
                    title = doc.get("title", "")
                    score = 5000
                    if re.match(rf'^\D*{target_num}\b', title):
                        score += 5000
                    scored_candidates[path] = {
                        "path": path,
                        "name": doc.get("name", ""),
                        "title": title,
                        "raw_content": doc.get("content", ""),
                        "title_score": score,
                        "content_score": 0,
                        "score": score
                    }
            
            # Sort numeric results
            results = list(scored_candidates.values())
            # Relevance is authoritative. Folder/name are deterministic
            # tie-breakers and must never move a lower-scoring result upward.
            results.sort(key=lambda x: (
                -x["score"], normalize_text(x["title"]), x["path"].casefold()
            ))
            top_results = results[:limit]

            for item in top_results:
                item["snippet"] = get_highlighted_snippet(item["raw_content"], query)
                del item["raw_content"]
                import os
                item["folder"] = os.path.dirname(item["path"])

            return top_results, True

        # Non-numeric text search:
        # 1. Gather candidate documents that match at least one query word
        candidates = set()
        for qw in q_words:
            candidates.update(self.title_index.get(qw, set()))
            candidates.update(self.content_index.get(qw, set()))

        # If candidates are few and words are >= 4 chars, do fuzzy candidate search
        if len(candidates) < 10:
            for qw in q_words:
                if len(qw) >= 4:
                    for indexed_word, paths in self.title_index.items():
                        if is_fuzzy_match(qw, indexed_word):
                            candidates.update(paths)
                    for indexed_word, paths in self.content_index.items():
                        if is_fuzzy_match(qw, indexed_word):
                            candidates.update(paths)

        # 2. Score candidates
        for path in candidates:
            doc = index_data.get(path)
            if not doc:
                continue

            norm_title = doc.get("norm_title", "")
            norm_content = doc.get("norm_content", "")
            t_words = self.title_words_cache.get(path, [])
            c_words = self.content_words_cache.get(path, set())

            title_score = 0
            # Title exact phrase matches
            if norm_query == norm_title:
                title_score += 1500
            elif norm_title.startswith(norm_query):
                title_score += 800
            elif norm_query in norm_title:
                title_score += 400
            else:
                # Word-level title match
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
                    title_score += 200 * (matched_title_words / len(q_words))

            content_score = 0
            if norm_content:
                if norm_query in norm_content:
                    content_score += 200 + (10 * min(norm_content.count(norm_query), 10))
                else:
                    matched_content_words = 0
                    for qw in q_words:
                        if qw in c_words:
                            matched_content_words += 1.0
                            content_score += min(norm_content.count(qw), 8) * 3
                        else:
                            for cw in c_words:
                                if is_fuzzy_match(qw, cw):
                                    matched_content_words += 0.5
                                    break
                    if matched_content_words > 0:
                        content_score += 80 * (matched_content_words / len(q_words))

            total_score = title_score + content_score
            if total_score > 0:
                scored_candidates[path] = {
                    "path": path,
                    "name": doc.get("name", ""),
                    "title": doc.get("title", ""),
                    "raw_content": doc.get("content", ""),
                    "title_score": title_score,
                    "content_score": content_score,
                    "score": total_score
                }

        results = list(scored_candidates.values())
        # Always rank by the highest total relevance score. Component scores
        # only resolve equal totals; title and path keep equal results stable.
        results.sort(key=lambda x: (
            -x["score"],
            -x["title_score"],
            -x["content_score"],
            normalize_text(x["title"]),
            x["path"].casefold(),
        ))
        top_results = results[:limit]

        import os
        for item in top_results:
            item["snippet"] = get_highlighted_snippet(item["raw_content"], query)
            del item["raw_content"]
            item["folder"] = os.path.dirname(item["path"])

        return top_results, False


def get_highlighted_snippet(content, query):
    """Extract and safely HTML-highlight the best matching line."""
    if not content:
        return ""
        
    q_words = normalize_text(query).split()
    if not q_words:
        safe_snippet = html.escape(content[:150])
        return safe_snippet + "..." if len(content) > 150 else safe_snippet
        
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
        escaped_word = html.escape(word)
        if word.isalnum():
            word_norm = normalize_text(word)
            matched = False
            for qw in q_words:
                if qw == word_norm or (len(qw) > 3 and qw in word_norm) or (len(word_norm) > 3 and word_norm in qw) or is_fuzzy_match(qw, word_norm):
                    matched = True
                    break
            if matched:
                highlighted_words.append(f"<mark>{escaped_word}</mark>")
            else:
                highlighted_words.append(escaped_word)
        else:
            highlighted_words.append(escaped_word)
            
    snippet = "".join(highlighted_words)
    if len(snippet) > 200:
        idx = snippet.find("<mark>")
        if idx > 50:
            snippet = "..." + snippet[idx-30:idx+150] + "..."
        else:
            snippet = snippet[:180] + "..."
    return snippet
