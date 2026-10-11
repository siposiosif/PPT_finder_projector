(() => {
  'use strict';
  const english = {
    'Transcriere RO Live': 'Romanian Live Transcription',
    'Se verifică sistemul…': 'Checking system…',
    'RECUNOAȘTERE VOCALĂ LOCALĂ': 'LOCAL VOICE RECOGNITION',
    'Vorbește. Găsește versetul.': 'Speak. Find the verse.',
    'Șterge textul': 'Clear text',
    'Pornește microfonul': 'Start microphone',
    'Oprește microfonul': 'Stop microphone',
    'Testează cu un eșantion': 'Test with a sample',
    'Model vocal': 'Voice model',
    'Alege modelul ASR folosit live de aplicația vocală.': 'Choose the ASR model used live by the voice app.',
    'Model vocal': 'Voice model',
    'Folosește modelul': 'Use model',
    'Model Hugging Face sau folder local': 'Hugging Face model or local folder',
    'Hugging Face ID sau cale folder local': 'Hugging Face ID or local folder path',
    'Instalează / adaugă': 'Install / add',
    'Schimbarea modelului se aplică live după confirmare.': 'Model changes apply live after confirmation.',
    'Model selected for live loading. Wait for confirmation before using the microphone.': 'Model selected for live loading. Wait for confirmation before using the microphone.',
    'Modelul curent este activ.': 'The current model is active.',
    'Modelul se încarcă pe GPU. Așteaptă confirmarea.': 'The model is loading on the GPU. Wait for confirmation.',
    'Alege un model și apasă Folosește modelul. Se schimbă live, fără restart.': 'Choose a model and press Use model. It changes live, without restart.',
    'Se încarcă modelul selectat pe GPU. Așteaptă confirmarea înainte de microfon.': 'Loading the selected model on the GPU. Wait for confirmation before using the microphone.',
    'Schimbare model': 'Changing model',
    'Modelul vocal se încarcă live. Microfonul va porni după confirmare.': 'The voice model is loading live. The microphone will start after confirmation.',
    'Setări audio': 'Audio settings',
    'Setările audio nu pot fi citite.': 'Audio settings cannot be read.',
    'Setările audio nu au putut fi salvate.': 'Audio settings could not be saved.',
    'Setările audio au fost salvate pentru microfon live și eșantioane.': 'Audio settings were saved for live microphone and samples.',
    'Lista de modele nu poate fi citită.': 'The model list cannot be read.',
    'Modelul nu a putut fi selectat.': 'The model could not be selected.',
    'Model saved and loaded live.': 'Model saved and loaded live.',
    'Modelul curent este deja activ.': 'The current model is already active.',
    'Se instalează sau se verifică modelul. Poate dura câteva minute la prima descărcare.': 'Installing or checking the model. The first download can take a few minutes.',
    'Modelul nu a putut fi instalat.': 'The model could not be installed.',
    'Model adăugat în listă. Alege-l din meniu și apasă Folosește modelul.': 'Model added to the list. Choose it from the menu and press Use model.',
    'Aplicație vocală independentă. Microfonul pornește numai la cerere.': 'Independent voice app. The microphone starts only when requested.',
    'Alege un fișier': 'Choose a file',
    'Link YouTube': 'YouTube link',
    'Lipește linkul YouTube': 'Paste the YouTube link',
    'Transcrie': 'Transcribe',
    'REDARE EȘANTION': 'SAMPLE PLAYBACK',
    'Fișier audio': 'Audio file',
    'GPU / SEGMENTE PE TĂCERE': 'GPU / SILENCE-BASED SEGMENTS',
    'Maxim:': 'Maximum:',
    'Pauză:': 'Silence:',
    'Prag:': 'Threshold:',
    'Nivel:': 'Level:',
    'Nivel audio redare': 'Playback audio level',
    'Apasă redare pentru transcriere pe măsură ce asculți.': 'Press play to transcribe while listening.',
    'Alege un fișier sau lipește un link YouTube, apoi apasă redare pentru analiză.': 'Choose a file or paste a YouTube link, then press play for analysis.',
    'MICROFON OPRIT': 'MICROPHONE OFF',
    'Apasă „Pornește microfonul”, apoi vorbește în limba română.': 'Press “Start microphone”, then speak Romanian.',
    'Primele 1,2 secunde sunt folosite pentru calibrarea zgomotului.': 'The first 1.2 seconds are used for noise calibration.',
    'Text transcris': 'Transcribed text',
    'Starea transcrierii': 'Transcription status',
    'STARE': 'STATUS',
    'În așteptare': 'Waiting',
    'Modelul se încarcă pe placa video.': 'The model is loading on the GPU.',
    'LATENȚĂ': 'LATENCY',
    'SESIUNE': 'SESSION',
    'BIBLIA CORNILESCU': 'CORNILESCU BIBLE',
    'Potriviri biblice': 'Bible matches',
    'Versetele găsite vor apărea aici.': 'Matching verses will appear here.',
    'Audio procesat local': 'Audio processed locally',
    'Vocea rămâne pe acest calculator. Fragmentele de text sunt trimise la Resurse Creștine pentru căutarea versetelor.': 'Your voice remains on this computer. Text fragments are sent to Resurse Creștine to search for verses.',
    'Nicio potrivire biblică sigură încă.': 'No reliable Bible match yet.',
    'Procesează eșantionul': 'Processing sample',
    'Transcriere în curs…': 'Transcription in progress…',
    'fără cuvinte recunoscute': 'no recognized words',
    'Redare în așteptare': 'Playback waiting',
    'Oprește sesiunea activă și așteaptă ca modelul vocal să fie pregătit.': 'Stop the active session and wait for the voice model to be ready.',
    'Redare indisponibilă': 'Playback unavailable',
    'Sunetul nu poate fi conectat la transcriere.': 'Audio cannot be connected to transcription.',
    'Ascult vocea până la următoarea pauză…': 'Listening for speech until the next pause…',
    'Răspuns invalid': 'Invalid response',
    'Răspuns invalid de la serverul vocal.': 'Invalid response from the voice server.',
    'Redare + transcriere': 'Playback + transcription',
    'Cuvintele se actualizează în timp real…': 'Words update in real time…',
    'Transcrierea redării s-a oprit.': 'Playback transcription stopped.',
    'Pauză · apasă redare pentru a continua cu un segment nou.': 'Paused · press play to continue with a new segment.',
    'Conexiunea vocală s-a închis. Reia redarea pentru a reconecta transcrierea.': 'The voice connection closed. Resume playback to reconnect transcription.',
    'Serverul vocal nu este disponibil. Reia redarea după reconectare.': 'The voice server is unavailable. Resume playback after reconnecting.',
    'Eșantion finalizat': 'Sample complete',
    'Transcriere completă': 'Transcription complete',
    'GPU pregătit': 'GPU ready',
    'Eșantionul nu a putut fi procesat.': 'The sample could not be processed.',
    'Eșantion nereușit': 'Sample failed',
    'Calibrare': 'Calibration',
    'Păstrează liniștea pentru o secundă.': 'Remain quiet for one second.',
    'Ascultă': 'Listening',
    'Vorbește normal în limba română.': 'Speak Romanian normally.',
    'Vocea este procesată pe GPU.': 'Voice is being processed on the GPU.',
    'Continuă să vorbești când ești gata.': 'Continue speaking when ready.',
    'Conectare': 'Connecting',
    'Se solicită accesul la microfon.': 'Requesting microphone access.',
    'GPU și microfon active': 'GPU and microphone active',
    'Finalizează': 'Finishing',
    'Ultimele cuvinte sunt procesate.': 'Processing the final words.',
    'Sesiune oprită': 'Session stopped',
    'Transcriere oprită': 'Transcription stopped',
    'Server indisponibil': 'Server unavailable',
    'Pornește microfonul pentru o sesiune nouă.': 'Start the microphone for a new session.',
    'Model indisponibil': 'Model unavailable',
    'Modelul se încarcă…': 'Model loading…',
    'Pornire nereușită': 'Startup failed',
    'Pregătire GPU': 'Preparing GPU',
    'Așteaptă încărcarea modelului. PPT Finder funcționează independent.': 'Wait for the model to load. PPT Finder works independently.',
    'Pornește microfonul pentru a începe.': 'Start the microphone to begin.',
    'Deconectat': 'Disconnected',
    'Pornește din nou aplicația vocală. Reconectarea se verifică automat.': 'Restart the voice app. Reconnection is checked automatically.',
    'Redarea și transcrierea pe segmente s-au încheiat.': 'Playback and segmented transcription finished.',
    'Redare finalizată': 'Playback complete',
    'Toate segmentele ascultate au fost procesate.': 'All listened segments were processed.',
    'Fișier pregătit': 'File ready',
    'Apasă redare pentru transcriere în timp real.': 'Press play for real-time transcription.',
    'Eșantionul YouTube': 'YouTube sample'
  };
  const patterns = [
    [/^(.+) este transcris pe GPU\.$/, '$1 is being transcribed on the GPU.'],
    [/^Pauză de ([\d.]+) s · prag ([\d.]+)\.$/, 'Silence $1 s · threshold $2.'],
    [/^Model activ: (.+)$/, 'Active model: $1'],
    [/^Microfon live · ([\d.]+)s max · pauză ([\d.]+)s · prag ([\d.]+)\.$/, 'Live microphone · $1s max · silence $2s · threshold $3.']
  ];
  let language = 'ro';
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
  function translateTree(root) {
    if (language !== 'en' || !root) return;
    if (root.nodeType === Node.TEXT_NODE) return translateTextNode(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.TEXT_NODE) translateTextNode(node);
      else for (const attribute of ['title', 'aria-label', 'placeholder']) {
        if (node.hasAttribute?.(attribute)) node.setAttribute(attribute, t(node.getAttribute(attribute)));
      }
    }
    document.title = t(document.title);
    document.documentElement.lang = language;
  }
  window.voiceT = value => t(value);
  window.voiceI18nReady = fetch('/api/ui-config', {cache:'no-store'})
    .then(response => response.ok ? response.json() : {language:'ro'})
    .catch(() => ({language:'ro'}))
    .then(config => {
      language = config.language === 'en' ? 'en' : 'ro';
      window.voiceLanguage = language;
      translateTree(document.body);
      if (language === 'en') new MutationObserver(records => {
        for (const record of records) {
          if (record.type === 'characterData') translateTextNode(record.target);
          for (const node of record.addedNodes) translateTree(node);
        }
      }).observe(document.body, {subtree:true, childList:true, characterData:true});
      return language;
    });
})();
