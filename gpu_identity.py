"""Fail-closed PCI identity binding for CUDA and the fixed telemetry GPU 0.

This checks one physical board; it does not provide multi-GPU selection/support.
Queries are read-only and bounded. No kernel is dispatched by this module.
"""
import ctypes as C
import re
import subprocess


def pci_identity(value):
    if type(value) is not str:
        raise ValueError('Invalid GPU PCI identity')
    match = re.fullmatch(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])', value.strip())
    if not match:
        raise ValueError('Invalid GPU PCI identity')
    identity = tuple(int(part, 16) for part in match.groups())
    if identity[2] > 31:
        raise ValueError('Invalid GPU PCI identity')
    return identity


def verify_identity(gpu):
    """Bind telemetry index 0 to the exact device used to create this CUDA context."""
    from gpu_monitor import nvidia_smi_path
    try:
        reading = subprocess.run([str(nvidia_smi_path()), '-i=0', '--query-gpu=pci.bus_id',
                                  '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)
        if reading.returncode:
            raise ValueError('Unavailable telemetry identity')
        monitored = pci_identity(reading.stdout)
        device = gpu.device  # Saved from cuDeviceGet and passed to cuCtxCreate.
        if type(device) is not int or device < 0:
            raise ValueError('Unavailable CUDA context device')
        query = gpu.driver.cuDeviceGetPCIBusId
        query.argtypes = [C.c_void_p, C.c_int, C.c_int]
        query.restype = C.c_int
        value = C.create_string_buffer(32)
        gpu.check(query(value, len(value), device), 'CUDA PCI identity')
        actual = pci_identity(value.value.decode('ascii'))
    except Exception as exc:
        raise RuntimeError('Cannot verify CUDA/telemetry GPU identity; no kernels permitted') from exc
    if actual != monitored:
        raise RuntimeError('CUDA device differs from telemetry GPU 0; no kernels permitted')
    return actual
