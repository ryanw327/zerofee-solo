"""Validated next-session settings. Importing never touches a device or Core."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

PACING = {'Full - no batch pause': 0, 'Balanced - 50 ms pause': .05,
          'Light - 250 ms pause': .25, 'Custom pause': None}
BATCH_SIZES = (4096, 16384, 65536, 131072, 262144)
THREAD_COUNTS = (64, 128, 256, 512)

def validate_threads(value):
    if type(value) is not int or value not in THREAD_COUNTS:
        raise ValueError('Threads per block must be 64, 128, 256 or 512.')

@dataclass(frozen=True)
class TuningSettings:
    pacing: str = 'Balanced - 50 ms pause'
    custom_pause_ms: float = 50
    batch_size: int = 262144
    threads_per_block: int = 128
    temperature_limit_c: float = 80

    def validate(self):
        if type(self.pacing) is not str or self.pacing not in PACING:
            raise ValueError('Choose a pacing preset or Custom pause.')
        if type(self.custom_pause_ms) not in (int, float) or not math.isfinite(self.custom_pause_ms) or not 0 <= self.custom_pause_ms <= 5000:
            raise ValueError('Custom pause must be 0 through 5000 ms.')
        if type(self.batch_size) is not int or self.batch_size not in BATCH_SIZES:
            raise ValueError('Choose one of the listed hash batch sizes (4096–262144).')
        validate_threads(self.threads_per_block)
        from gpu_monitor import ThermalPolicy
        ThermalPolicy(self.temperature_limit_c).validate()
        return self

    @property
    def pause_seconds(self):
        return self.custom_pause_ms / 1000 if self.pacing == 'Custom pause' else PACING[self.pacing]

def load_settings(path):
    """Corrupt/incompatible files use defaults, with a visible notice; never rewrite."""
    try:
        path = Path(path)
        if path.stat().st_size > 8192:
            raise ValueError('Settings file is too large')
        value = json.loads(path.read_text(encoding='utf-8'))
        if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1 or type(value.get('settings')) is not dict:
            raise ValueError('Unsupported settings format')
        return TuningSettings(**value['settings']).validate(), ''
    except FileNotFoundError:
        return TuningSettings(), ''
    except (OSError, ValueError, TypeError):
        return TuningSettings(), 'Saved GPU settings could not be loaded. Safe defaults are in use; review Advanced Tuning.'

def save_settings(path, settings):
    settings.validate()
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(version=1, settings=asdict(settings)), indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
