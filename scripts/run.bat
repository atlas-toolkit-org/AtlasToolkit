@echo off
rem Start Atlas Toolkit from source (files dropped on this .bat open in the app).
"%~dp0..\.venv\Scripts\pythonw.exe" -m atlas_toolkit %*
