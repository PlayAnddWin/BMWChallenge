"""Start the workbench with canonical .env settings applied before Streamlit initializes.

Run: python -m ui [Streamlit options] [-- --fixtures | --snapshot | --mock]
"""
import os
from pathlib import Path
import sys

from config import get_settings


def main():
    settings = get_settings()
    os.environ["STREAMLIT_SERVER_ADDRESS"] = settings.streamlit_server_address
    os.environ["STREAMLIT_SERVER_PORT"] = str(settings.streamlit_server_port)
    from streamlit.web import cli

    script = Path(__file__).resolve().parents[1] / "pipeline" / "08_streamlit_ui.py"
    sys.argv = ["streamlit", "run", str(script), *sys.argv[1:]]
    cli.main()


if __name__ == "__main__":
    main()
