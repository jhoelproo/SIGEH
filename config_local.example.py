"""Copia este archivo como ``config_local.py`` y completa la URL local.

``config_local.py`` esta ignorado por Git y se incorpora solamente al build
local. Nunca publiques credenciales reales en el repositorio.
"""

# Entorno de pruebas reservado: qnlathnvnkscjuzulrge.supabase.co
# Pega aquí la cadena Postgres del proyecto de pruebas (Session pooler 5432
# para esta aplicación de escritorio). Nunca la subas al repositorio.
DATABASE_URL = "postgresql://USUARIO:CONTRASENA@HOST_POOLER:5432/postgres"

