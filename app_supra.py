import streamlit as st
import mysql.connector
import pandas as pd
from datetime import datetime
import io
import re

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="SUPRA | Gestión de Planta BRODA PRO", layout="wide")

# Estilos PRO
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    div[data-testid="stMetric"] {
        background-color: #1e1e1e !important;
        padding: 20px !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        border: 1px solid #333 !important;
    }
    div[data-testid="stMetric"] label { color: #ffffff !important; font-size: 1.1rem !important; }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] { color: #00ff00 !important; font-weight: bold !important; }
    .stButton>button { width: 100%; border-radius: 8px; height: 3.5em; font-weight: bold; background-color: #007bff; color: white; }
    .stDataFrame { border-radius: 12px; overflow: hidden; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNCIONES DE SOPORTE ---
def get_db_connection():
    try:
        return mysql.connector.connect(
            host=st.secrets["DB_HOST"],
            user=st.secrets["DB_USER"],
            password=st.secrets["DB_PASS"],
            database=st.secrets["DB_NAME"]
        )
    except Exception as e:
        st.error(f"Error de conexión: {e}")
        return None

def recalcular_costos_cascada():
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    try:
        # 1. Actualización de Componentes
        cursor.execute("""
            UPDATE componentes_maestro 
            SET costo_total_calculado = (
                SELECT COALESCE(SUM(d.cantidad_bruta * i.costo_unitario), 0)
                FROM componentes_detalle d
                JOIN ingredientes_supra i ON d.codigo_hijo = i.codigo_ingrediente
                WHERE d.codigo_padre = codigo_componente
            )
            WHERE codigo_componente IS NOT NULL
        """)
        # 2. Actualización de Platos (Costo y Peso)
        cursor.execute("""
            UPDATE platos_maestro 
            SET 
                costo_total_calculado = (
                    SELECT COALESCE(SUM(d.cantidad_inicial * COALESCE(i.costo_unitario, c.costo_total_calculado, 0)), 0)
                    FROM platos_detalle d
                    LEFT JOIN ingredientes_supra i ON d.codigo_hijo = i.codigo_ingrediente
                    LEFT JOIN componentes_maestro c ON d.codigo_hijo = c.codigo_componente
                    WHERE d.codigo_plato_padre = codigo_plato_supra
                ),
                peso_total_gramos = (
                    SELECT COALESCE(NULLIF(SUM(cantidad_inicial), 0), 1) 
                    FROM platos_detalle 
                    WHERE codigo_plato_padre = codigo_plato_supra
                )
            WHERE codigo_plato_supra IS NOT NULL
        """)
        conn.commit()
    except Exception as e:
        st.error(f"Error en cascada: {e}")
    finally:
        conn.close()

def descargar_excel(df, nombre_archivo="datos.xlsx"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Datos')
    return output.getvalue()

def get_next_code(prefix, tabla, columna):
    try:
        conn = get_db_connection()
        query = f"SELECT {columna} FROM {tabla} WHERE {columna} LIKE '{prefix}%' ORDER BY {columna} DESC LIMIT 1"
        df = pd.read_sql(query, conn)
        conn.close()
        if not df.empty:
            last_code = int(df.iloc[0][columna])
            return str(last_code + 1)
        return f"{prefix}001"
    except:
        return f"{prefix}001"

def get_item_cost(codigo):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        codigo_str = str(codigo)
        if codigo_str.startswith('3'): 
            cursor.execute("SELECT costo_unitario FROM ingredientes_supra WHERE codigo_ingrediente = %s", (codigo,))
            res = cursor.fetchone()
            return float(res[0]) if res else 0.0
        elif codigo_str.startswith('2'): 
            cursor.execute("SELECT costo_total_calculado FROM componentes_maestro WHERE codigo_componente = %s", (codigo,))
            res = cursor.fetchone()
            return float(res[0]) if res and res[0] else 0.0
        conn.close()
    except:
        return 0.0
    return 0.0

# --- NAVEGACIÓN ---
st.sidebar.title("SUPRA Planta")
menu = st.sidebar.radio("GESTIÓN PRINCIPAL", ["📊 Dashboard", "📦 Insumos (30...)", "🍳 Componentes (20...)", "🍽️ Platos Finales (10...)"])

# --- MODULO 0: DASHBOARD ---
if menu == "📊 Dashboard":
    st.header("Dashboard de Gestión de Recetario")
    conn = get_db_connection()
    if conn:
        c_i = pd.read_sql("SELECT COUNT(*) as t FROM ingredientes_supra", conn).iloc[0]['t']
        c_p = pd.read_sql("SELECT COUNT(*) as t FROM platos_maestro", conn).iloc[0]['t']
        
        k1, k2, k3 = st.columns([1, 1, 1])
        k1.metric("Insumos Base (30)", c_i)
        k2.metric("Platos Finales (10)", c_p)
        with k3:
            if st.button("🔄 RECALCULAR TODO"):
                with st.spinner("Sincronizando costos..."):
                    recalcular_costos_cascada()
                st.rerun()
        
        st.divider()
        st.subheader("Catálogo con Costos en Tiempo Real")
        
        df_d = pd.read_sql("""
            SELECT 
                codigo_plato_supra as 'Código', 
                nombre_plato as 'Nombre', 
                peso_total_gramos as 'Gramaje (g)', 
                costo_total_calculado as 'Costo Total ($)',
                ROUND(costo_total_calculado / NULLIF(peso_total_gramos/1000, 0), 2) as 'Costo x KG ($)'
            FROM platos_maestro 
            ORDER BY codigo_plato_supra DESC
        """, conn)
        
        st.dataframe(df_d.style.format({
            'Costo Total ($)': '{:,.2f}',
            'Costo x KG ($)': '{:,.2f}',
            'Gramaje (g)': '{:,.0f}'
        }), use_container_width=True, hide_index=True)
        conn.close()

# --- MODULO 1: INSUMOS ---
elif menu == "📦 Insumos (30...)":
    st.header("Gestión de Insumos")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        with st.expander("➕ Cargar Nuevo Ingrediente Individual"):
            with st.form("new_ing"):
                conn = get_db_connection()
                # Nuevo selector basado en la tabla estructurada
                df_cls = pd.read_sql("SELECT codigo, tipo, sub_division FROM clasificacion_supra WHERE id_gran_familia = 3", conn)
                conn.close()
                
                c1, c2, c3 = st.columns(3)
                with c1:
                    ops_ins = df_cls.apply(lambda x: f"{x['codigo']} - {x['tipo']} ({x['sub_division']})", axis=1).tolist()
                    fam_sel = st.selectbox("Categoría Insumo", ops_ins)
                    pre = fam_sel.split(" - ")[0]
                    desc = st.text_input("Nombre Insumo")
                with c2:
                    um = st.selectbox("UM", ["KG", "LT", "UN", "GR"])
                    cant_e = st.number_input("Cantidad Envase", min_value=0.001, value=1.0)
                    cost_e = st.number_input("Precio Envase ($)", min_value=0.0)
                with c3:
                    prov = st.text_input("Proveedor")
                
                if st.form_submit_button("REGISTRAR"):
                    nuevo_id = get_next_code(pre, "ingredientes_supra", "codigo_ingrediente")
                    conn = get_db_connection(); cursor = conn.cursor()
                    u_c = cost_e / cant_e if cant_e > 0 else 0
                    cursor.execute("INSERT INTO ingredientes_supra (codigo_ingrediente, descripcion, um, cantidad_envase, costo_total_envase, costo_unitario, proveedor) VALUES (%s,%s,%s,%s,%s,%s,%s)", (nuevo_id, desc, um, cant_e, cost_e, u_c, prov))
                    conn.commit()
                    recalcular_costos_cascada()
                    conn.close(); st.success(f"Guardado como {nuevo_id}"); st.rerun()

    with col_f2:
        with st.expander("📥 Importación Masiva (Subir Excel)"):
            st.write("Subí tu archivo editado con los códigos de 5 dígitos (ej: 30401).")
            archivo_subido = st.file_uploader("Elegir archivo .xlsx", type=['xlsx'])
            
            if archivo_subido and st.button("🚀 INICIAR IMPORTACIÓN"):
                try:
                    df_migrar = pd.read_excel(archivo_subido)
                    conn = get_db_connection(); cursor = conn.cursor()
                    offsets_familia = {}; nuevos = 0; actualizados = 0
                    
                    for i, row in df_migrar.iterrows():
                        raw_val = str(row['codigo_ingrediente']).strip()
                        cod_clean = re.sub(r'\D', '', raw_val)
                        if not cod_clean: continue
                        if len(cod_clean) <= 5:
                            if cod_clean not in offsets_familia:
                                ultimo_db = get_next_code(cod_clean, "ingredientes_supra", "codigo_ingrediente")
                                offsets_familia[cod_clean] = int(ultimo_db)
                            final_id = str(offsets_familia[cod_clean])
                            offsets_familia[cod_clean] += 1
                            nuevos += 1
                        else:
                            final_id = cod_clean
                            actualizados += 1
                        
                        c_total = float(row.get('costo_total_envase', 0))
                        c_cant = float(row.get('cantidad_envase', 1))
                        u_cost = c_total / c_cant if c_cant > 0 else 0
                        
                        sql = """INSERT INTO ingredientes_supra (codigo_ingrediente, descripcion, costo_total_envase, cantidad_envase, costo_unitario, proveedor)
                                 VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE 
                                 descripcion=VALUES(descripcion), costo_total_envase=VALUES(costo_total_envase), 
                                 cantidad_envase=VALUES(cantidad_envase), costo_unitario=VALUES(costo_unitario), proveedor=VALUES(proveedor)"""
                        cursor.execute(sql, (final_id, str(row['descripcion']).upper().strip(), c_total, c_cant, u_cost, str(row.get('proveedor', '')).strip()))
                    
                    conn.commit()
                    recalcular_costos_cascada()
                    conn.close()
                    st.success(f"✅ ¡Éxito! {nuevos} nuevos y {actualizados} actualizados.")
                    st.rerun()
                except Exception as e: st.error(f"❌ Error: {e}")

    st.divider()
    conn = get_db_connection()
    df_l = pd.read_sql("SELECT codigo_ingrediente, descripcion, costo_total_envase, cantidad_envase, costo_unitario, proveedor FROM ingredientes_supra ORDER BY codigo_ingrediente DESC", conn)
    conn.close()
    ed_df = st.data_editor(df_l, use_container_width=True, hide_index=True, key="ed_ing")
    
    c_btn1, c_btn2 = st.columns(2)
    with c_btn1:
        if st.button("💾 GUARDAR CAMBIOS DE EDICIÓN"):
            conn = get_db_connection(); cursor = conn.cursor()
            for _, r in ed_df.iterrows():
                new_u = float(r['costo_total_envase']) / float(r['cantidad_envase']) if float(r['cantidad_envase']) > 0 else 0
                cursor.execute("UPDATE ingredientes_supra SET descripcion=%s, costo_total_envase=%s, cantidad_envase=%s, costo_unitario=%s, proveedor=%s WHERE codigo_ingrediente=%s", (r['descripcion'], r['costo_total_envase'], r['cantidad_envase'], new_u, r['proveedor'], r['codigo_ingrediente']))
            conn.commit()
            recalcular_costos_cascada()
            conn.close(); st.success("Sincronizado"); st.rerun()
    with c_btn2:
        excel_ins = descargar_excel(df_l)
        st.download_button("📥 Exportar Insumos (Excel)", data=excel_ins, file_name="insumos_supra.xlsx")

# --- MODULO 2: COMPONENTES ---
elif menu == "🍳 Componentes (20...)":
    st.header("Elaboración de Componentes")
    with st.expander("➕ Crear Nuevo Componente"):
        if 'rows_c' not in st.session_state: st.session_state.rows_c = []
        conn = get_db_connection()
        df_cls_c = pd.read_sql("SELECT codigo, tipo, sub_division FROM clasificacion_supra WHERE id_gran_familia = 2", conn)
        
        c1, c2 = st.columns(2)
        nom_c = c1.text_input("Nombre de la Sub-receta")
        ops_comp = df_cls_c.apply(lambda x: f"{x['codigo']} - {x['tipo']} ({x['sub_division']})", axis=1).tolist()
        fam_c = c1.selectbox("Familia Componente", ops_comp)
        tot_c_placeholder = c2.empty()

        df_i = pd.read_sql("SELECT codigo_ingrediente as id, descripcion as n FROM ingredientes_supra", conn); conn.close()
        ops_c = df_i.apply(lambda x: f"{x['id']} - {x['n']}", axis=1).tolist()

        if st.button("➕ Añadir Insumo"): st.session_state.rows_c.append({"id": "", "cant": 0.0})

        acum_c = 0.0
        for i, row in enumerate(st.session_state.rows_c):
            cols = st.columns([3, 1, 1])
            st.session_state.rows_c[i]['id'] = cols[0].selectbox(f"Insumo {i}", [""] + ops_c, key=f"c_s_{i}")
            st.session_state.rows_c[i]['cant'] = cols[1].number_input("Cant.", key=f"c_c_{i}", format="%.4f")
            if st.session_state.rows_c[i]['id']:
                p = get_item_cost(st.session_state.rows_c[i]['id'].split(" - ")[0])
                sub = p * st.session_state.rows_c[i]['cant']; acum_c += sub
                cols[2].write(f"${sub:.2f}")

        tot_c_placeholder.metric("COSTO ESTIMADO", f"$ {acum_c:.2f}")

        if st.button("💾 GUARDAR COMPONENTE"):
            conn = get_db_connection(); cursor = conn.cursor()
            pre = fam_c.split(" - ")[0]
            nc = get_next_code(pre, "componentes_maestro", "codigo_componente")
            cursor.execute("INSERT INTO componentes_maestro (codigo_componente, nombre_receta) VALUES (%s,%s)", (nc, nom_c))
            for r in st.session_state.rows_c:
                if r['id']:
                    cursor.execute("INSERT INTO componentes_detalle (codigo_padre, codigo_hijo, cantidad_bruta) VALUES (%s,%s,%s)", (nc, r['id'].split(" - ")[0], r['cant']))
            conn.commit()
            recalcular_costos_cascada()
            conn.close(); st.success(f"Componente {nc} guardado."); st.session_state.rows_c = []; st.rerun()

    st.divider()
    conn = get_db_connection()
    df_comp = pd.read_sql("SELECT codigo_componente, nombre_receta, costo_total_calculado FROM componentes_maestro ORDER BY codigo_componente DESC", conn)
    conn.close()
    st.data_editor(df_comp, use_container_width=True, hide_index=True)

# --- MODULO 3: PLATOS ---
elif menu == "🍽️ Platos Finales (10...)":
    st.header("Maestro de Recetas Finales")
    sub_tab = st.radio("Acción:", ["✨ CREAR", "✏️ EDITAR"], horizontal=True)

    if sub_tab == "✨ CREAR":
        if 'rows_p' not in st.session_state: st.session_state.rows_p = []
        conn = get_db_connection()
        
        # CAMBIO CLAVE: Usamos LIKE '1%' para capturar toda la Serie 10 (Platos)
        df_cls_p = pd.read_sql("SELECT codigo, tipo, sub_division FROM clasificacion_supra WHERE codigo_final LIKE '1%'", conn)
        
        c1, c2 = st.columns(2)
        p_nom = c1.text_input("Nombre del Plato")
        
        # Verificamos si hay data para el selector
        if not df_cls_p.empty:
            ops_plat = df_cls_p.apply(lambda x: f"{x['codigo']} - {x['tipo']} ({x['sub_division']})", axis=1).tolist()
            p_fam = c1.selectbox("Categoría Plato", ops_plat)
        else:
            st.error("⚠️ No hay categorías cargadas en la tabla clasificacion_supra para Platos (Serie 10).")
            p_fam = None
            
        p_gr = c2.number_input("Gramaje Final (g)", value=0)
        p_tot_view = c2.empty()

        q_all = "SELECT codigo_ingrediente as id, descripcion as n FROM ingredientes_supra UNION SELECT codigo_componente, nombre_receta FROM componentes_maestro"
        df_all = pd.read_sql(q_all, conn); conn.close()
        ops_p = df_all.apply(lambda x: f"{x['id']} - {x['n']}", axis=1).tolist()

        if st.button("➕ Añadir Item"): st.session_state.rows_p.append({"id": "", "cant": 0.0})

        acum_p = 0.0
        for i, row in enumerate(st.session_state.rows_p):
            cols = st.columns([3, 1, 1])
            st.session_state.rows_p[i]['id'] = cols[0].selectbox(f"Item {i}", [""] + ops_p, key=f"p_s_{i}")
            st.session_state.rows_p[i]['cant'] = cols[1].number_input("Cant.", key=f"p_c_{i}", format="%.4f")
            if st.session_state.rows_p[i]['id']:
                val = get_item_cost(st.session_state.rows_p[i]['id'].split(" - ")[0]) * st.session_state.rows_p[i]['cant']
                acum_p += val; cols[2].write(f"${val:.2f}")
        
        p_tot_view.metric("COSTO TOTAL PLATO", f"$ {acum_p:.2f}")

        if st.button("💾 GUARDAR PLATO"):
            if p_fam: # El "paracaídas" para que no tire AttributeError
                conn = get_db_connection()
                cursor = conn.cursor()
                pre = p_fam.split(" - ")[0]
                
                # Buscamos el id_clasificacion real
                cursor.execute("SELECT codigo_final FROM clasificacion_supra WHERE codigo = %s LIMIT 1", (pre,))
                res_cls = cursor.fetchone()
                
                if res_cls:
                    id_clasificacion_real = res_cls[0]
                    cid = get_next_code(pre, "platos_maestro", "codigo_plato_supra")
                    
                    cursor.execute("""
                        INSERT INTO platos_maestro (codigo_plato_supra, nombre_plato, id_clasificacion, peso_total_gramos) 
                        VALUES (%s,%s,%s,%s)
                    """, (cid, p_nom, id_clasificacion_real, p_gr))
                    
                    for r in st.session_state.rows_p:
                        if r['id']:
                            cursor.execute("""
                                INSERT INTO platos_detalle (codigo_plato_padre, codigo_hijo, cantidad_inicial) 
                                VALUES (%s,%s,%s)
                            """, (cid, r['id'].split(" - ")[0], r['cant']))
                    
                    conn.commit()
                    recalcular_costos_cascada()
                    conn.close()
                    st.balloons()
                    st.session_state.rows_p = []
                    st.rerun()
                else:
                    st.error(f"Error: El código de familia {pre} no existe en clasificacion_supra.")
                    conn.close()
            else:
                st.warning("⚠️ Seleccioná una categoría.")

    else:
        st.subheader("Edición Técnica de Platos")
        conn = get_db_connection()
        df_ex = pd.read_sql("SELECT codigo_plato_supra as cod, nombre_plato as n FROM platos_maestro", conn)
        plato_sel = st.selectbox("Seleccione Plato para Editar:", [""] + df_ex['n'].tolist())
        if plato_sel:
            c_ed = df_ex[df_ex['n'] == plato_sel]['cod'].values[0]
            det = pd.read_sql(f"SELECT id_detalle_plato, codigo_hijo, cantidad_inicial FROM platos_detalle WHERE codigo_plato_padre = '{c_ed}'", conn)
            ed_det = st.data_editor(det, use_container_width=True, hide_index=True)
            
            c_ed1, c_ed2 = st.columns(2)
            with c_ed1:
                if st.button("💾 ACTUALIZAR RECETA"):
                    cursor = conn.cursor()
                    for _, r in ed_det.iterrows():
                        cursor.execute("UPDATE platos_detalle SET cantidad_inicial=%s WHERE id_detalle_plato=%s", (r['cantidad_inicial'], r['id_detalle_plato']))
                    conn.commit()
                    recalcular_costos_cascada()
                    conn.close(); st.success("Receta actualizada"); st.rerun()
            with c_ed2:
                st.download_button("📥 Descargar Ficha", data=descargar_excel(det), file_name=f"receta_{plato_sel}.xlsx")
        conn.close()