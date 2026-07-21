import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: F401  (Vercel's Python runtime picks up this ASGI `app`)
