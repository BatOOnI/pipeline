v1.7 fix:
- legacy array parser now converts:
  {"action":"run","path":"TEST/app.py"}
  into
  {"type":"run_cmd","args":{"cmd":["python","TEST/app.py"]}}

Previous versions only handled legacy 'command', so cmd became empty and caused WinError 87.
