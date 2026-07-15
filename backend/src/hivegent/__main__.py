from hivegent.cli import app

# Guarded so a spawned worker process (multiprocessing re-imports this module as
# ``__mp_main__``) does not relaunch the CLI when the process pool starts.
if __name__ == "__main__":
    app()
