import sys
import time

from jarvis.procs import run_capture, run_text


def test_returns_while_background_child_keeps_running():
    # The child starts a grandchild that inherits the output and lives on (like the adb server).
    code = ("import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); print('hi')")
    start = time.time()
    out = run_text([sys.executable, "-c", code], timeout=20)
    assert time.time() - start < 10
    assert out.startswith("exit code 0") and "hi" in out


def test_timeout_stops_the_command():
    start = time.time()
    out = run_text([sys.executable, "-c", "print('x', flush=True); import time; time.sleep(30)"], timeout=1)
    assert time.time() - start < 10
    assert out.startswith("stopped after 1 seconds") and "x" in out


def test_separate_streams_and_exit_code():
    code, out, err = run_capture([sys.executable, "-c", "import sys; print('o'); sys.stderr.write('e'); sys.exit(3)"],
                                 merge=False)
    assert (code, out.strip(), err) == (3, b"o", b"e")
