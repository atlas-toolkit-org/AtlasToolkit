"""Entry script for the frozen app (PyInstaller needs a file, not a package's __main__)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()   # a spawned worker process starts here and runs its job instead of the app
    from atlas_toolkit.__main__ import main
    sys.exit(main())
