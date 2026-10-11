# PPT Finder

Aplicație separată pentru căutarea, deschiderea și proiecția prezentărilor PPT/PPTX.

## Structură

- `templates/` — numai pagini HTML/Jinja (`index`, `projector`, `remote`).
- `static/css/` — CSS separat pentru fiecare pagină.
- `static/js/` — JavaScript separat pentru fiecare pagină.
- `app/` — API-ul Flask, indexarea și controlul proiecției.
- `open-bibles-master/` — traducerile Bibliei folosite de căutare.
- `tools/` — utilitare opționale; nu pornesc odată cu aplicația.
- `legacy/` și `archive/` — versiunile vechi păstrate pentru referință.
- `build/` și `dist/` — artefacte de împachetare existente.

## Pornire individuală

Varianta desktop, locală, deschide PPT/PPTX cu aplicația implicită din Windows:

```powershell
python .\ppt_finder.py
```

Varianta server activează accesul în rețea și controlul PowerPoint COM explicit:

```powershell
python .\ppt_server.py
```

Portul implicit este `8080`. Folderele indexate sunt în `config.txt`, iar indexul persistent este `ppt_index.json`.

Controlul COM nu este folosit de indexare. Astfel, indexarea nu poate atașa sau închide prezentarea care rulează în fața publicului.
