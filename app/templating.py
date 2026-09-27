# app/templating.py
"""Közös Jinja2 sablonkezelő; a sablonokban használt globális függvényeket
(pl. is_admin_user) az app/auth.py regisztrálja."""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
