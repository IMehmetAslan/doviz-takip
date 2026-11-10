"""
Vercel Serverless Function Handler for Doviz Borc-Alacak Takip.
"""

import sys
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if "VERCEL" not in os.environ:
    os.environ["VERCEL"] = "1"

from src.server import AppApiHandler

# Vercel entrypoint
class handler(AppApiHandler):
    pass

app = handler
application = handler
