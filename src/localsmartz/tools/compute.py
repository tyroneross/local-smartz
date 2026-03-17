"""Sandboxed Python execution tool for calculations and data processing."""

import ast
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from langchain_core.tools import tool


def _auto_print_last_expr(code: str) -> str:
    """If the last statement is a bare expression, wrap it in print().

    Local models often write REPL-style code (ending with a variable name)
    instead of using print(). This mirrors Jupyter/IPython behavior.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    if not tree.body:
        return code

    last_stmt = tree.body[-1]
    if not isinstance(last_stmt, ast.Expr):
        return code

    # Don't wrap if it's already a print() call
    if isinstance(last_stmt.value, ast.Call):
        func = last_stmt.value.func
        if isinstance(func, ast.Name) and func.id == "print":
            return code

    # Skip if expression shares a line with another statement (semicolons)
    if len(tree.body) > 1:
        prev_stmt = tree.body[-2]
        if prev_stmt.end_lineno == last_stmt.lineno:
            return code

    # Replace the last line with print(last_line)
    lines = code.rstrip().split("\n")
    start_line = last_stmt.lineno - 1  # 0-indexed
    expr_lines = lines[start_line:]
    expr_text = "\n".join(expr_lines).strip()
    indent = len(lines[start_line]) - len(lines[start_line].lstrip())
    lines = lines[:start_line]
    lines.append(" " * indent + f"print({expr_text})")
    return "\n".join(lines)


@tool
def python_exec(code: str, timeout: int = 30) -> str:
    """Execute Python code in a sandboxed subprocess and return output.

    Args:
        code: Python code to execute
        timeout: Maximum execution time in seconds (default: 30, max: 30)

    Returns:
        Combined stdout and stderr from the execution

    Use cases:
        - Mathematical calculations
        - Data processing and transformations
        - Quick algorithm prototyping
        - Statistical analysis

    Security: Code runs in a subprocess without network access to installed packages.
    Scripts are saved in .localsmartz/scripts/ for audit trail.
    """
    # Enforce timeout limit
    timeout = min(timeout, 30)

    # Auto-print last expression (handles REPL-style code from local models)
    code = _auto_print_last_expr(code)

    # Create scripts directory
    scripts_dir = Path.cwd() / ".localsmartz" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # Generate script filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    script_path = scripts_dir / f"calc_{timestamp}.py"

    # Write code to file
    try:
        script_path.write_text(code, encoding="utf-8")
    except Exception as e:
        return f"Error writing script: {e}"

    # Execute in subprocess
    try:
        result = subprocess.run(
            ["python3", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=Path.cwd(),
        )

        # Format output
        output_parts = []

        if result.stdout:
            output_parts.append("=== STDOUT ===")
            output_parts.append(result.stdout.rstrip())

        if result.stderr:
            output_parts.append("=== STDERR ===")
            output_parts.append(result.stderr.rstrip())

        if not output_parts:
            output_parts.append("(no output)")

        # Add execution metadata
        output_parts.append("")
        output_parts.append(f"Exit code: {result.returncode}")
        output_parts.append(f"Script saved: {script_path}")

        return "\n".join(output_parts)

    except subprocess.TimeoutExpired:
        return f"Error: Execution timed out after {timeout} seconds\nScript saved: {script_path}"
    except FileNotFoundError:
        return "Error: python3 not found. Ensure Python 3 is installed and in PATH"
    except Exception as e:
        return f"Error executing script: {e}\nScript saved: {script_path}"
