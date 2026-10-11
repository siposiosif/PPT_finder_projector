(() => {
  'use strict';
  const language = (document.body?.dataset.language || 'ro').toLowerCase() === 'en' ? 'en' : 'ro';
  const english = {
    'PPT Finder Pro - Căutare Avansată & Proiecție': 'PPT Finder Pro - Advanced Search & Projection',
    'Ecran Proiecție - PPT Finder': 'Projector Screen - PPT Finder',
    'Telecomandă PPT - Remote Control': 'PPT Remote Control',
    'Voce · aplicație separată': 'Voice · separate app',
    'Temă sistem': 'System theme',
    'Mod întunecat': 'Dark mode',
    'Mod luminos': 'Light mode',
    'Telecomandă': 'Remote',
    'Ecran proiecție': 'Projector screen',
    'Setări Foldere': 'Folder settings',
    'Indexare în curs...': 'Indexing…',
    'Foldere Configurate pentru Indexare': 'Folders configured for indexing',
    'Adaugă Folder': 'Add folder',
    'Adaugă cale folder (ex: D:\\Prezentari\\Cantari)...': 'Add folder path (for example D:\\Presentations\\Songs)…',
    'Reindexează Toate Fișierele': 'Reindex all files',
    'CONSOLA OPERATORULUI': 'OPERATOR CONSOLE',
    'Caută. Pregătește. Proiectează.': 'Search. Prepare. Present.',
    'Deschide PPT/PPTX în aplicația implicită sau proiectează textul în fereastra pentru public.': 'Open PPT/PPTX in the default app or project text in the audience window.',
    '↑ ↓ selectează rezultatul local · Enter deschide fișierul · căutările funcționează independent': '↑ ↓ selects a local result · Enter opens the file · searches work independently',
    'Fișiere Locale PPT / PPTX': 'Local PPT / PPTX files',
    'Tastați pentru a căuta': 'Type to search',
    'Caută prezentări, cântece și versete': 'Search presentations, songs, and verses',
    'Titlu, vers, #124 sau Ioan 3:16…': 'Title, lyric, #124, or John 3:16…',
    'Căutare Locală Instantă': 'Instant local search',
    'Scrieți un titlu, număr sau vers din cântec.': 'Enter a title, number, or lyric.',
    'Cântece Online & Cache Offline': 'Online songs & offline cache',
    'Caută și proiectează cântece din baza de date Resurse Creștine.': 'Search and project songs from the Resurse Creștine database.',
    'Biblia Integrată': 'Integrated Bible',
    'Căutați referințe (ex: Ioan 3:16, Genesa 1:1-3) sau cuvinte cheie.': 'Search references (for example John 3:16, Genesis 1:1-3) or keywords.',
    'Proiecție Activă': 'Active projection',
    'Proiecție web · text extras': 'Web projection · extracted text',
    'Pornește slideshow': 'Start slideshow',
    'Ecran Negru': 'Black screen',
    'Ascunde Text': 'Hide text',
    'Arată textul': 'Show text',
    'Oprește': 'Stop',
    '⏹️ Oprește': '⏹️ Stop',
    'PE ECRAN': 'ON SCREEN',
    'TEXT ASCUNS': 'TEXT HIDDEN',
    'ECRAN NEGRU': 'BLACK SCREEN',
    'URMEAZĂ': 'UP NEXT',
    'Toate slide-urile': 'All slides',
    'Înapoi': 'Back',
    '← Înapoi': '← Back',
    '◀ Înapoi': '◀ Back',
    'Următorul': 'Next',
    'Următorul →': 'Next →',
    'Înainte': 'Next',
    'Înainte →': 'Next →',
    'Înainte ▶': 'Next ▶',
    'Conectare…': 'Connecting…',
    'Index pregătit': 'Index ready',
    'Telecomandă Smartphone': 'Smartphone remote',
    'Scanează codul QR sau deschide link-ul pe telefon pentru a controla prezentările și proiecția de la distanță.': 'Scan the QR code or open the link on your phone to control presentations and projection remotely.',
    'Cum funcționează:': 'How it works:',
    'Asigurați-vă că telefonul este pe': 'Make sure the phone is on the',
    'aceeași rețea Wi-Fi': 'same Wi-Fi network',
    'Asigurați-vă că telefonul este pe aceeași rețea Wi-Fi.': 'Make sure the phone is on the same Wi-Fi network.',
    'Scanați codul QR de mai sus cu camera telefonului.': 'Scan the QR code above with the phone camera.',
    'Navigați prin slide-uri folosind swipe sau butoanele tactile.': 'Navigate slides by swiping or using the touch buttons.',
    'Nicio proiecție activă': 'No active projection',
    "Atingeți 'Next' sau glisați stânga/dreapta": "Tap 'Next' or swipe left/right",
    'Glisați pentru Next / Back': 'Swipe for Next / Back',
    'Pornește (F5)': 'Start (F5)',
    'Ecran Negru': 'Black screen',
    'Ascunde Text': 'Hide text',
    'Toate Slide-urile': 'All slides',
    'Verificare...': 'Checking…',
    'Ecran complet · F': 'Fullscreen · F',
    'Conectat': 'Connected',
    'Conexiune întreruptă · se păstrează ultimul slide': 'Connection interrupted · keeping the last slide',
    'Actualizare invalidă · se păstrează ultimul slide': 'Invalid update · keeping the last slide',
    'Reconectare · se păstrează ultimul slide': 'Reconnecting · keeping the last slide',
    'Folosiți F11 pentru ecran complet': 'Use F11 for fullscreen',
    'Comanda nu a fost confirmată': 'The command was not confirmed',
    'Activ': 'Active',
    'Gata de pornire': 'Ready to start',
    'Inactiv': 'Inactive',
    'Prezentare': 'Presentation',
    'Prezentare PowerPoint': 'PowerPoint presentation',
    'Proiecție Web': 'Web projection',
    'Deschide': 'Open',
    'Proiectează text': 'Project text',
    'VERSET DETECTAT DIN VOCE': 'VERSE DETECTED FROM VOICE',
    'Verset detectat': 'Detected verse',
    'Deschide pentru a vizualiza textul complet.': 'Open to view the full text.',
    'Proiectează': 'Project',
    'Printează': 'Print',
    'Închide': 'Close',
    'Arată fișierul': 'Show file',
    'Elimină din index': 'Remove from index',
    'Niciun rezultat local': 'No local results',
    'Niciun cântec online': 'No online songs',
    'Niciun verset': 'No verses',
    'Căutare în curs…': 'Searching…',
    'Caută…': 'Searching…',
    'Căutarea nu a reușit': 'Search failed',
    'Reîncearcă': 'Retry',
    'Gata pentru căutare': 'Ready to search',
    'Tastați': 'Type',
    'Proiecție text': 'Text projection',
    'Sfârșitul prezentării': 'End of presentation',
    'Niciun slide': 'No slide',
    'Ultimul slide este pe ecran.': 'The last slide is on screen.',
    '● Conectat': '● Connected',
    '● Reconectare…': '● Reconnecting…',
    'Mod: Deconectat': 'Mode: Disconnected',
    'Deschideți o prezentare pe computer pentru a începe.': 'Open a presentation on the computer to begin.',
    'Apăsați Pornește pentru slideshow (F5)': 'Press Start for slideshow (F5)',
    'Prezentarea este deschisă. Apăsați butonul „Pornește (F5)”.': 'The presentation is open. Press “Start (F5)”.'
  };
  const patterns = [
    [/^(\d+) rezultate$/, '$1 results'],
    [/^(\d+) versete$/, '$1 verses'],
    [/^(\d+) slide-uri pregătite$/, '$1 slides ready'],
    [/^Slide (\d+) din (\d+)$/, 'Slide $1 of $2'],
    [/^Mod: (.+)$/, 'Mode: $1']
  ];
  function t(value) {
    const text = String(value ?? '');
    if (language !== 'en') return text;
    if (Object.prototype.hasOwnProperty.call(english, text)) return english[text];
    for (const [pattern, replacement] of patterns) if (pattern.test(text)) return text.replace(pattern, replacement);
    return text;
  }
  function translateTextNode(node) {
    const value = node.nodeValue || '', trimmed = value.trim();
    if (!trimmed) return;
    const translated = t(trimmed);
    if (translated !== trimmed) node.nodeValue = value.replace(trimmed, translated);
  }
  function translateElement(element) {
    for (const attribute of ['title', 'aria-label', 'placeholder']) {
      if (element.hasAttribute?.(attribute)) {
        const value = element.getAttribute(attribute), translated = t(value);
        if (translated !== value) element.setAttribute(attribute, translated);
      }
    }
  }
  function translateTree(root) {
    if (language !== 'en' || !root) return;
    if (root.nodeType === Node.TEXT_NODE) return translateTextNode(root);
    if (root.nodeType === Node.ELEMENT_NODE) translateElement(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.TEXT_NODE) translateTextNode(node);
      else if (!['SCRIPT', 'STYLE'].includes(node.tagName)) translateElement(node);
    }
    document.title = t(document.title);
  }
  window.PPT_I18N = {language, t, translateTree};
  translateTree(document.body);
  if (language === 'en') new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'characterData') translateTextNode(record.target);
      for (const node of record.addedNodes) translateTree(node);
    }
  }).observe(document.body, {subtree:true, childList:true, characterData:true});
})();
