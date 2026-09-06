"""
Default configuration values for KAGWeb.
"""

from kagweb.runtime.home import get_runtime_home

_project_root = get_runtime_home()

# Default configuration
DEFAULTS = {
    "llm": {"model": "gpt-4o-mini", "provider": "openai"},
    "paths": {
        "user_data_dir": str(_project_root / "data" / "user"),
        "user_log_dir": str(_project_root / "data" / "user" / "logs"),
    },
}
