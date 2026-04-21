import sys


def _run() -> int:
    try:
        from avada_seo_app.gui_pyside import run_app
    except ModuleNotFoundError as exc:
        if "PySide6" in str(exc):
            msg = (
                "Brak biblioteki PySide6.\n\n"
                "W tym srodowisku (Python 3.14) PySide6 moze byc niedostepny.\n"
                "Uruchom aplikacje na Python 3.12/3.13 i wykonaj:\n"
                "python -m pip install -r requirements.txt\n"
            )
            print(msg)
            return 1
        raise
    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
