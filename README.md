# PPT_finder_projector

# Căutare Avansată PowerPoint (Advanced Search)

Acest proiect conține instrumente pentru indexarea, căutarea rapidă și proiecția cântecelor sau prezentărilor PowerPoint (PPT/PPTX). Este special conceput pentru a fi utilizat în biserici sau alte contexte unde se dorește găsirea rapidă a versurilor și proiectarea lor pe ecrane secundare.

## Funcționalități principale

- **Indexare rapidă**: Indexează prezentările PPT și PPTX din folderele configurate.
  - *Faza 1*: Indexare instantă a titlurilor fișierelor.
  - *Faza 2*: Extragere paralelă a textului din interiorul diapozitivelor (slide-urilor).
  - *Faza 3 (rezervă)*: Utilizarea interfeței COM (Microsoft PowerPoint) ca fallback dacă extragerea directă eșuează.
- **Căutare inteligentă**: Căutare cu toleranță la diacritice, căutare parțială și potrivire fuzzy (imprecisă).
- **Aplicație Desktop (Interfață CustomTkinter)**: Interfață vizuală rapidă pentru căutare, vizualizare secvențe de text (snippets) cu marcarea cuvintelor potrivite și deschidere directă.
- **Server Web & Sistem de Proiecție**:
  - Interfață web modernă (accesibilă în rețeaua locală de pe telefoane sau tablete).
  - Integrare cu API-ul **Resurse Creștine** pentru a căuta cântece online și a le descărca/proiecta pe loc.
  - Sistem de control al proiecției locale (fereastră secundară pentru ecranul de proiecție).

## Structura proiectului

- `ppt_finder.py`: Aplicația desktop realizată în Python folosind CustomTkinter.
- `ppt_server.py`: Serverul web Flask cu interfață de control integrată via PyWebView.
- `config.txt`: Fișier de configurare local ce conține căile către folderele cu prezentări.
- `ppt_index.json`: Indexul generat automat după scanarea fișierelor.

## Rulare și utilizare

### Cerințe preliminare

Instalați dependențele necesare:
```bash
pip install flask python-pptx customtkinter pywebview requests
```
*Notă: Pentru utilizarea fallback-ului COM pe Windows, este necesar să aveți Microsoft PowerPoint instalat și pachetul `pywin32`.*

### Pornire Aplicație Desktop
```bash
python ppt_finder.py
```

### Pornire Server Web / Sistem de Proiecție
```bash
python ppt_server.py
```

## Configurare

În fișierul `config.txt` (creat automat la prima rulare dacă nu există), adăugați pe fiecare linie calea către folderele pe care doriți să le indexați. De exemplu:
```text
D:\Prezentari\PPT
D:\Prezentari\AlteFoldere
```
