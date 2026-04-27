#!/usr/bin/env python3
"""
Quick local preview — renders the email to data/last_email_preview.html
without sending anything. Open the file in your browser to review.

Usage:
    python run_local.py              # dry run, save HTML preview
    python run_local.py --send       # actually send the email
"""

import sys
from main import main

if __name__ == "__main__":
    if "--send" in sys.argv:
        main(dry_run=False)
    else:
        main(dry_run=True)
