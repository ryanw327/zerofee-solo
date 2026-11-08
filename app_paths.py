"""Windows vendor locations derived from the current environment, never a developer profile."""
import os
from pathlib import Path

DEFAULT_DATADIR = str(Path(os.environ.get('APPDATA', str(Path.home() / 'AppData' / 'Roaming'))) / 'Bitcoin')
