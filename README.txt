v1.8 CLI run and logging fix

Changes:
- Interactive CLI apps are now run with empty stdin + timeout, so they can exit on EOF instead of hanging forever.
- Prompt explicitly tells model to include run_cmd when the user asks to run the program.
- All console output is saved to pipeline_log.txt.
- run.bat ends with pause again, so the window stays open.
