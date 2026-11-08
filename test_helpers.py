"""Helpers for mocked owned-process cleanup tests; no process is created here."""
def stop_owned_test_node(process,rpc):
    rpc.deadline=None
    if process.poll() is not None:return False
    try:
        rpc.timeout=5
        rpc.call('stop')
        process.wait(timeout=20)
        return False
    except Exception:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        return True
