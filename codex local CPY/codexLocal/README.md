# AVADA SEO Placeholder Generator

Aplikacja desktop (Tkinter) do podmiany treści w szablonach AVADA/Fusion bez psucia shortcode.

## Co skanuje teraz
- `fusion_text` (treść bloku)
- `fusion_content_box`:
  - `title` (atrybut)
  - `body` (zawartość)
- `fusion_imageframe` (URL wewnątrz tagu)
- `fusion_image` (atrybut `image` w galeriach `fusion_images`)

## Najważniejsze zmiany architektury
- Podmiana działa po **zakresach pozycji (start/end)** w oryginalnym tekście, a nie przez globalne `str.replace`.
- Dzięki temu nie zostają „wiszące” markery przez błędne podmiany.
- Raport skanowania pokazuje, jakie typy bloków zostały wykryte i ile.
- Generacja działa w 2 etapach (page-first):
  1. `Etap 1: Generuj strategie` (globalny JSON: temat, SEO goal, tone, keywords, plan sekcji z `section_id`)
  2. `Generuj SEO dla wszystkich` (jedno globalne żądanie JSON dla całej strony, potem mapowanie do placeholderów + fallback dla braków)
- GUI ma `Podglad strategii` (sekcje inferred, strategy JSON, generated JSON, mapping report).

## Walidacja po generacji
Aplikacja wykrywa:
- niepodmienione markery (`TEXT_001`, `IMG_001`, także wersje `__TEXT_001__`)
- stare frazy tematyczne przy zmianie tematu (np. garden room vs bathroom)
- mieszanie języków
- niezmienione `fusion_content_box` (title/body)
- podejrzany mismatch nagłówek -> treść

Po `Generuj finalny plik` zapisuje się też raport:
- `<output_name>_validation_report.txt`
- `<output_name>_generation_report.txt`

## Uruchomienie
```powershell
cd "I:\Projekt copilot\codexLocal"
python -m pip install -r requirements.txt
python seo_avada_gui.py
```

## Bezpieczny klucz API
- W GUI masz przyciski: `Zapisz klucz`, `Wczytaj klucz`, `Usun klucz`.
- Na Windows klucz jest zapisywany zaszyfrowany przez DPAPI (powiązany z Twoim kontem systemowym).
- Przy starcie aplikacja automatycznie próbuje wczytać zapisany klucz.

## Dodatkowe UX
- Przycisk `Aktualizuj modele` pobiera aktualną listę modeli z OpenAI API i odświeża listę wyboru.
- Prompt globalny ma większe pole edycji oraz pionowy scrollbar.
- `Podglad strategii` ma pionowy scrollbar dla wygodnego przeglądania dużego JSON.
- Kroki pracy są oznaczone jako Etap 1/2/3/4/5.
- Tryby pracy:
  - `text-only` / `text+image`
  - `auto` / `english-only` / `polish-only`
  - `strict-html-internal` (wewnętrzne linki jako `<a href="...">...</a>`)
  - `avada-strict` (lepsze zachowanie layoutu heading + pusta linia + body)
- Dla długich zapytań API pojawiają się okienka `Czekaj` / `Kill`.
- `Czekaj` kontynuuje i ponownie pyta po czasie; `Kill` oznacza przerwanie przy najbliższej bezpiecznej okazji.
- Gdy odpowiedź przyjdzie, okno oczekiwania zamyka się automatycznie.

## Szybki workflow
1. `Wczytaj szablon`
2. Uzupełnij prompt globalny + API key
3. `Etap 1: Generuj strategie`
4. `Generuj SEO dla wszystkich`
4. (Opcjonalnie) `Generuj metadane wszystkich obrazów`
5. `Generuj finalny plik`
6. Sprawdź raport walidacji
