import sqlite3
import psycopg2
import os

try:
    from config_local import DATABASE_URL as LOCAL_DATABASE_URL
except ImportError:
    LOCAL_DATABASE_URL = ""

# Fuerza a buscar el archivo en la misma carpeta donde está este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB_PATH = os.path.join(BASE_DIR, "hospital.db")

# Enlace de tu base de datos en la nube
POSTGRES_URL = os.environ.get("DATABASE_URL") or LOCAL_DATABASE_URL

def normalizar_texto(texto):
    """Convierte a minúsculas y quita acentos"""
    t = str(texto).strip().lower()
    return t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')

def clasificar_ars_item(nombre):
    """Clasifica los ítems mágicamente según palabras clave"""
    nombre_norm = normalizar_texto(nombre)
    
    # PALABRAS CLAVE PARA IMÁGENES
    keywords_imagenes = [
        'sonografia', 'radiografia', 'rayos x', 'rx', 'tomografia', 'tac',
        'resonancia', 'electrocardiograma', 'ecg', 'ekg', 'ecografia', 
        'mamografia', 'densitometria', 'placa', 'doppler', 'mapa'
    ]
    
    # PALABRAS CLAVE PARA LABORATORIOS
    keywords_laboratorios = [
        'hemograma', 'orina', 'glicemia', 'glucosa', 'urea', 'creatinina', 
        'colesterol', 'trigliceridos', 'tipificacion', 'vdrl', 'hiv', 'hcv', 
        'hbsag', 'coprologico', 'cultivo', 'ast', 'alt', 'bilirrubina', 
        'acido urico', 'tsh', 't3', 't4', 'pcr', 'embarazo', 'sangre', 
        'exudado', 'prueba', 'perfil', 'antigeno', 'anticuerpo', 'examen', 
        'test', 'hepatitis', 'dengue', 'falce', 'tiempo', 'coagulacion',
        'pt', 'ptt', 'hba1c', 'troponina', 'sodio', 'potasio', 'cloro'
    ]
    
    for kw in keywords_imagenes:
        if kw in nombre_norm: return 'Imágenes'
        
    for kw in keywords_laboratorios:
        if kw in nombre_norm: return 'Laboratorios'
            
    return 'Procedimientos'

def table_exists(cur, table_name):
    """Verifica si una tabla existe en la base de datos vieja SQLite"""
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None

def migrar_catalogo():
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"❌ ERROR: No se encontró el archivo en: {SQLITE_DB_PATH}")
        return

    print(f"📂 Abriendo base de datos local en: {SQLITE_DB_PATH}")
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()
    
    # Diagnóstico inicial
    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tablas = [r[0] for r in sqlite_cur.fetchall()]
    print(f"📌 Tablas encontradas localmente: {', '.join(tablas)}\n")

    if not tablas:
        print("⚠️ El archivo hospital.db está vacío.")
        return

    try:
        pg_conn = psycopg2.connect(POSTGRES_URL)
        pg_cur = pg_conn.cursor()
    except Exception as e:
        print(f"❌ ERROR conectando a la nube: {e}")
        return

    try:
        print("⏳ 1. Rescatando ARS...")
        if table_exists(sqlite_cur, 'ars'):
            sqlite_cur.execute("SELECT * FROM ars")
            for ars in sqlite_cur.fetchall():
                # Evitar error si la tabla vieja de ARS no tiene columna is_active
                is_active = ars['is_active'] if 'is_active' in ars.keys() else 1
                pg_cur.execute("INSERT INTO ars(nombre, sala_emergencia, is_active) VALUES(%s, %s, %s) ON CONFLICT(nombre) DO NOTHING", (ars['nombre'], ars['sala_emergencia'], is_active))

        print("\n⏳ 2. Extrayendo Procedimientos, Laboratorios e Imágenes...")
        items_a_migrar = {} 
        
        # AQUI ESTA LA MAGIA: Buscar en la tabla antigua "procedimientos"
        if table_exists(sqlite_cur, 'procedimientos'):
            print("   -> Detectada tabla antigua 'procedimientos'. Extrayendo datos...")
            sqlite_cur.execute("SELECT a.nombre as ars_nombre, p.nombre as item_nombre, p.precio FROM procedimientos p JOIN ars a ON p.ars_id = a.id")
            for row in sqlite_cur.fetchall():
                items_a_migrar[(row['ars_nombre'], row['item_nombre'])] = row['precio']
                
        if table_exists(sqlite_cur, 'ars_items'):
            print("   -> Detectada tabla nueva 'ars_items'. Extrayendo datos...")
            sqlite_cur.execute("SELECT a.nombre as ars_nombre, ai.nombre as item_nombre, ai.precio FROM ars_items ai JOIN ars a ON ai.ars_id = a.id")
            for row in sqlite_cur.fetchall():
                items_a_migrar[(row['ars_nombre'], row['item_nombre'])] = row['precio']

        contadores = {'Laboratorios': 0, 'Imágenes': 0, 'Procedimientos': 0}
        
        for (ars_nombre, item_nombre), precio in items_a_migrar.items():
            categoria_correcta = clasificar_ars_item(item_nombre)
            contadores[categoria_correcta] += 1
            
            pg_cur.execute("SELECT id FROM ars WHERE nombre = %s", (ars_nombre,))
            res = pg_cur.fetchone()
            if res:
                pg_cur.execute(
                    """
                    INSERT INTO ars_items(ars_id, categoria, nombre, precio, is_active) 
                    VALUES(%s, %s, %s, %s, 1)
                    ON CONFLICT(ars_id, categoria, nombre) DO UPDATE SET precio=EXCLUDED.precio
                    """,
                    (res[0], categoria_correcta, item_nombre, precio)
                )

        print(f"✅ Se rescataron y clasificaron {len(items_a_migrar)} ítems de ARS:")
        print(f"   - 🩸 Laboratorios: {contadores['Laboratorios']}")
        print(f"   - 🩻 Imágenes: {contadores['Imágenes']}")
        print(f"   - 🩺 Procedimientos: {contadores['Procedimientos']}")


        print("\n⏳ 3. Migrando Medicamentos y Materiales (Universales)...")
        univ_migrar = {}
        if table_exists(sqlite_cur, 'universal_items'):
            sqlite_cur.execute("SELECT categoria, nombre, precio FROM universal_items")
            for row in sqlite_cur.fetchall():
                univ_migrar[(row['categoria'], row['nombre'])] = row['precio']
                        
        for (cat, nom), prec in univ_migrar.items():
            pg_cur.execute(
                """
                INSERT INTO universal_items(categoria, nombre, precio, is_active) 
                VALUES(%s, %s, %s, 1)
                ON CONFLICT(categoria, nombre) DO UPDATE SET precio=EXCLUDED.precio
                """,
                (cat, nom, prec)
            )
        print(f"✅ {len(univ_migrar)} Medicamentos y Materiales migrados.")

        pg_conn.commit()
        print("\n🎉 ¡MIGRACIÓN COMPLETADA CON ÉXITO! Ya puedes iniciar tu sistema principal en la nube. 🎉")

    except Exception as e:
        pg_conn.rollback()
        print(f"\n❌ Ocurrió un error en la migración: {e}")

    finally:
        pg_cur.close()
        pg_conn.close()
        sqlite_cur.close()
        sqlite_conn.close()

if __name__ == "__main__":
    migrar_catalogo()
