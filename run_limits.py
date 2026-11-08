"""Shared explicit test/session limits; no device or node access."""
import math

MAX_SESSION_SECONDS = 7 * 24 * 3600
SESSION_BATCH_SIZE = 262144
TEST_BATCH_SIZE = 4096

def validate_limits(seconds, hashes, blocks=1, batch_size=4096, profile='test',
                    pause_seconds=0, can_stop=False):
    if profile not in ('test', 'session'):
        raise ValueError('Choose a test or mining session.')
    if not isinstance(pause_seconds, (int, float)) or not math.isfinite(pause_seconds) or not 0 <= pause_seconds <= 5:
        raise ValueError('Batch pause must be 0 through 5 seconds.')
    batch_limit=SESSION_BATCH_SIZE if profile=='session' else TEST_BATCH_SIZE
    if type(blocks) is not int or not 1 <= blocks <= 10 or type(batch_size) is not int or not 1 <= batch_size <= batch_limit:
        raise ValueError('Invalid block/batch bounds.')
    if profile == 'test':
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= 60:
            raise ValueError('Test time must be greater than zero and at most 60 seconds.')
        if type(hashes) is not int or not 1 <= hashes <= 10_000_000:
            raise ValueError('Test hash limit must be 1 through 10,000,000.')
    else:
        if not can_stop:
            raise ValueError('A mining session requires a cooperative Stop control.')
        if seconds is not None and (not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
                                    or not 0 < seconds <= MAX_SESSION_SECONDS):
            raise ValueError('Choose a positive duration up to seven days, or Until Stop.')
        if hashes is not None or blocks != 1:
            raise ValueError('Mining sessions have no hash cap and stop at one accepted block.')
