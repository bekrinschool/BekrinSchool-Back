"""
Safe execution of student Python code against test cases.
Uses subprocess with timeout. stdin = input_data, compare stdout to expected.
Blocks dangerous imports: exec, eval, os, subprocess, socket, requests, etc.
"""
import subprocess
import tempfile
import os
import sys
import time
import re

# Dangerous patterns to block (simple string scan)
_DANGEROUS_PATTERNS = [
    r'\bexec\s*\(',
    r'\beval\s*\(',
    r'\b__import__\s*\(',
    r'\bcompile\s*\(',
    r'\bglobals\s*\(',
    r'\blocals\s*\(',
    r'\bgetattr\s*\([^)]*["\']__',
    r'\bopen\s*\([^)]*["\'][rw]',
    r'\bimport\s+os\b',
    r'\bfrom\s+os\s+import',
    r'\bimport\s+subprocess\b',
    r'\bfrom\s+subprocess\s+import',
    r'\bimport\s+socket\b',
    r'\bfrom\s+socket\s+import',
    r'\bimport\s+requests\b',
    r'\bfrom\s+requests\s+import',
    r'\bimport\s+sys\b',
    r'\bimport\s+os\.path\b',
    r'\bfile\s*\(',  # Python 2 legacy
]
_DANGEROUS_RE = re.compile('|'.join(_DANGEROUS_PATTERNS), re.IGNORECASE)


def validate_code_safe(code: str):
    """Check code for dangerous patterns. Returns (ok, error_message)."""
    if not code or not code.strip():
        return False, 'Kod boş ola bilməz'
    m = _DANGEROUS_RE.search(code)
    if m:
        return False, f'Təhlükəli əmrlər icazə verilmir: {m.group(0).strip()}'
    return True, ''


def run_python_code(code: str, stdin_input: str, timeout_seconds: int = 5):
    """
    Run Python code with stdin_input as stdin.
    Returns (stdout, stderr, return_code).
    Does NOT validate code - caller must call validate_code_safe first.
    """
    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.py',
        delete=False,
        encoding='utf-8',
    ) as f:
        f.write(code)
        path = f.name
    try:
        result = subprocess.run(
            [sys.executable, path],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=os.path.dirname(path),
        )
        return (result.stdout or '', result.stderr or '', result.returncode)
    except subprocess.TimeoutExpired:
        return ('', 'Timeout', -1)
    except Exception as e:
        return ('', str(e)[:500], -1)
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def normalize_output(s: str) -> str:
    """Normalize for comparison: strip, collapse newlines."""
    if s is None:
        return ''
    return s.strip().replace('\r\n', '\n').replace('\r', '\n')


def check_output_match(got: str, expected: str) -> bool:
    """Compare normalized stdout to expected."""
    return normalize_output(got) == normalize_output(expected)


def run_python_code_timed(code: str, stdin_input: str, timeout_seconds: int = 5):
    """
    Run Python code, return (stdout, stderr, return_code, execution_time_ms).
    """
    start = time.perf_counter()
    stdout, stderr, return_code = run_python_code(code, stdin_input, timeout_seconds)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return (stdout, stderr, return_code, elapsed_ms)
