#!/usr/bin/env python3
"""JDS6600 Controller (GUI)

Tkinter GUI entrypoint.

Ubuntu/Debian GUI dependency:
  sudo apt update && sudo apt install -y python3-tk
"""

from jds_controller.gui.app import main


if __name__ == "__main__":
    main()
