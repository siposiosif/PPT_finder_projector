"""Start the full PPT Finder application for this computer only."""

import os
import runpy


if __name__ == "__main__":
    os.environ["PPT_FINDER_BIND_HOST"] = "127.0.0.1"
    os.environ["PPT_FINDER_COM_ENABLED"] = "0"
    runpy.run_module("ppt_server", run_name="__main__")
