# AVADA SEO Generator (PySide6)

Desktopowa aplikacja do pracy na szablonach AVADA/Fusion.

## Co zostalo zrobione
- refaktor na wieloplikowy projekt:
  - `avada_seo_app/gui_pyside.py` (GUI)
  - `avada_seo_app/engine.py` (silnik i walidacja)
  - `avada_seo_app/openai_client.py` (API + background polling)
  - `avada_seo_app/parser.py` (skaner AVADA)
  - `avada_seo_app/storage.py` (bezpieczny klucz + draft sesji)
  - `avada_seo_app/models.py`, `avada_seo_app/constants.py`
- start aplikacji bez zmian komendy: `python seo_avada_gui.py`
- GUI na PySide6, zakladki i etapy 1-5 po kolei
- zapisywanie wyborow GUI przed zamknieciem (QSettings + draft placeholderow)
- kolorowe statusy i animacja podczas pracy (PRACA/OK/BLAD)
- checklist support:
  - skanowanie `fusion_li_item`
  - checklist itemy jako edytowalne placeholdery
  - checklist QA panel w GUI z wykrywaniem kontaminacji starego tematu

## Skanowane elementy AVADA
- `fusion_text`
- `fusion_content_box` (`title` + `body`)
- `fusion_li_item` (checklist itemy)
- `fusion_imageframe`
- `fusion_image` (atrybut `image`)

## Uruchomienie
```powershell
cd "I:\Projekt copilot\codexLocal"
python -m pip install -r requirements.txt
python seo_avada_gui.py
```

## Notatka
Klucz API mozna zapisac bezpiecznie (DPAPI na Windows):
- `Zapisz klucz`
- `Wczytaj klucz`
- `Usun klucz`
