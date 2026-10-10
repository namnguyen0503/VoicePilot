"""Keep Google FLAC support without the unused PocketSphinx acoustic models."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("speech_recognition", excludes=["pocketsphinx-data/**"])
