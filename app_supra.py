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
        # 1. Update Componentes (Si componentes_detalle usa cantidad_bruta)
        cursor.execute("""
            UPDATE componentes_maestro cm
            INNER JOIN (
                SELECT d.codigo_padre, COALESCE(SUM(d.cantidad_bruta * i.costo_unitario), 0) as nuevo_costo
                FROM componentes_detalle d
                JOIN ingredientes_supra i ON d.codigo_hijo = i.codigo_ingrediente
                GROUP BY d.codigo_padre
            ) calculo ON cm.codigo_componente = calculo.codigo_padre
            SET cm.costo_total_calculado = calculo.nuevo_costo
        """)
        
        # 2. Update Platos Finales (Costo s/ Bruto, Peso s/ Neto)
        cursor.execute("""
            UPDATE platos_maestro pm
            INNER JOIN (
                SELECT 
                    d.codigo_plato_padre,
                    COALESCE(SUM(d.cantidad_bruta * COALESCE(i.costo_unitario, c.costo_total_calculado, 0)), 0) as costo_total,
                    COALESCE(SUM(d.cantidad_neta), 1) as peso_total
                FROM platos_detalle d
                LEFT JOIN ingredientes_supra i ON d.codigo_hijo = i.codigo_ingrediente
                LEFT JOIN componentes_maestro c ON d.codigo_hijo = c.codigo_componente
                GROUP BY d.codigo_plato_padre
            ) calc ON pm.codigo_plato_supra = calc.codigo_plato_padre
            SET pm.costo_total_calculado = calc.costo_total,
                pm.peso_total_gramos = GREATEST(calc.peso_total, 1)
        """)
        conn.commit()
    except Exception as e:
        st.error(f"Error de optimización SQL: {e}")
    finally:
        conn.close()

def descargar_excel_simple(df, nombre_hoja="Datos"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=nombre_hoja)
    return output.getvalue()

def descargar_excel_asistente(df_principal, df_items, df_familias):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_principal.to_excel(writer, index=False, sheet_name='CARGA_RECETAS')
        
        # PREPARACIÓN DE DICCIONARIOS PARA DESPLEGABLES AMIGABLES
        df_items_export = df_items.copy()
        df_items_export['ITEM_MOSTRAR'] = df_items_export['codigo'].astype(str) + " - " + df_items_export['descripcion']
        df_items_export.to_excel(writer, index=False, sheet_name='DICCIONARIO_ITEMS')
        
        df_familias.to_excel(writer, index=False, sheet_name='FAMILIAS')
        
        workbook = writer.book
        ws_carga = writer.sheets['CARGA_RECETAS']
        
        from openpyxl.worksheet.datavalidation import DataValidation
        
        # VALIDACIÓN 1: FAMILIAS (Solo muestra el Número desde Columna A)
        max_fam = len(df_familias) + 1
        dv_fam = DataValidation(type="list", formula1=f"'FAMILIAS'!$A$2:$A${max_fam}", allow_blank=True)
        ws_carga.add_data_validation(dv_fam)
        dv_fam.add("C2:C2000") # Columna C es codigo_familia

        # VALIDACIÓN 2: ITEMS (Muestra ID - NOMBRE desde la nueva Columna C de Diccionario)
        max_items = len(df_items_export) + 1
        dv_item = DataValidation(type="list", formula1=f"'DICCIONARIO_ITEMS'!$C$2:$C${max_items}", allow_blank=True)
        ws_carga.add_data_validation(dv_item)
        dv_item.add("E2:E2000") # Columna E es codigo_item

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
menu = st.sidebar.radio("GESTIÓN PRINCIPAL", ["📊 Dashboard", "📦 Ingredientes", "🍳 Componentes", "🍽️ Platos Finales"])




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
        st.subheader("Catálogo con Análisis de Margen y Rentabilidad")
        
        # 1. Extracción de datos base
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

        # 2. Lógica de Negocio: Ingeniería de Menú
        # Definimos un Food Cost Objetivo (ej. 35% para SUPRA)
        fc_target = 0.35 
        
        # Calculamos Precio de Venta Sugerido (Cost / Target)
        df_d['Venta Sugerida (Sin IVA)'] = df_d['Costo Total ($)'] / fc_target
        
        # Calculamos el Margen de Contribución Unitario
        df_d['Margen ($)'] = df_d['Venta Sugerida (Sin IVA)'] - df_d['Costo Total ($)']
        
        # 3. Visualización con Formato Pro
        st.dataframe(
            df_d.style.format({
                'Costo Total ($)': '${:,.2f}',
                'Costo x KG ($)': '${:,.2f}',
                'Gramaje (g)': '{:,.0f}',
                'Venta Sugerida (Sin IVA)': '${:,.2f}',
                'Margen ($)': '${:,.2f}'
            }).background_gradient(
                subset=['Costo x KG ($)'], 
                cmap='YlOrRd'
            ), 
            use_container_width=True, 
            hide_index=True
        )

        # 4. KPI de Salud del Recetario (Opcional pero recomendado para Broda)
        avg_cost_kg = df_d['Costo x KG ($)'].mean()
        st.info(f"💡 El costo promedio por KG en la planta SUPRA es de **${avg_cost_kg:,.2f}**")
        
        conn.close()

# --- MODULO 1: INSUMOS ---
elif menu == "📦 Ingredientes":
    st.header("Gestión de Insumos")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        with st.expander("➕ Cargar Nuevo Ingrediente Individual"):
            with st.form("new_ing"):
                conn = get_db_connection()
                df_cls = pd.read_sql("SELECT codigo, tipo, sub_division FROM clasificacion_supra WHERE codigo_final LIKE '3%'", conn)
                conn.close()
                
                c1, c2, c3 = st.columns(3)
                with c1:
                    if not df_cls.empty:
                        ops_ins = df_cls.apply(lambda x: f"{x['codigo']} - {x['tipo']} ({x['sub_division']})", axis=1).tolist()
                        fam_sel = st.selectbox("Categoría Insumo", ops_ins)
                        pre = fam_sel.split(" - ")[0]
                    else:
                        st.warning("Sin categorías")
                        pre = "30101"
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
            st.write("Subí el diccionario con códigos de 5 dígitos para autosecuencia o códigos completos para forzar.")
            archivo_insumos = st.file_uploader("Elegir archivo .xlsx", type=['xlsx'], key="bulk_insumos_pro")
            
        if archivo_insumos and st.button("🚀 INICIAR IMPORTACIÓN", key="btn_import_insumos"):
            conn = None
            try:
                df_migrar = pd.read_excel(archivo_insumos, sheet_name='DICCIONARIO_ITEMS').fillna("")
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 300;")
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")

                nuevos = 0
                actualizados = 0

                with st.status("Sincronizando maestro de insumos...", expanded=True):
                    for i, row in df_migrar.iterrows():
                        raw_val = str(row['codigo']).strip()
                        cod_clean = re.sub(r'\D', '', raw_val)
                        if not cod_clean: continue
                        
                        final_id = cod_clean
                        if len(cod_clean) <= 5: nuevos += 1
                        else: actualizados += 1
                        
                        try:
                            c_total = float(row.get('costo_total_envase', 0))
                            c_cant = float(row.get('cantidad_envase', 1))
                        except:
                            c_total, c_cant = 0.0, 1.0
                        
                        u_cost = c_total / c_cant if c_cant > 0 else 0
                        u_medida = str(row.get('um', 'UN')).strip().upper()

                        sql = """
                            INSERT INTO ingredientes_supra 
                            (codigo_ingrediente, descripcion, um, costo_total_envase, cantidad_envase, costo_unitario)
                            VALUES (%s, %s, %s, %s, %s, %s) 
                            ON DUPLICATE KEY UPDATE 
                                descripcion=VALUES(descripcion), um=VALUES(um), costo_total_envase=VALUES(costo_total_envase), 
                                cantidad_envase=VALUES(cantidad_envase), costo_unitario=VALUES(costo_unitario)
                        """
                        cursor.execute(sql, (final_id, str(row['descripcion']).upper().strip(), u_medida, c_total, c_cant, u_cost))
                    
                conn.commit()
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
                recalcular_costos_cascada()
                st.success(f"✅ ¡Éxito! Insumos sincronizados.")
                st.rerun()

            except Exception as e:
                if conn: 
                    conn.rollback()
                    cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
                st.error(f"❌ Error crítico en insumos: {e}")
            finally:
                if conn: conn.close()
                
    st.divider()
    conn = get_db_connection()
    df_l = pd.read_sql("SELECT codigo_ingrediente, descripcion, um, costo_total_envase, cantidad_envase, costo_unitario, proveedor FROM ingredientes_supra ORDER BY codigo_ingrediente DESC", conn)
    conn.close()
    ed_df = st.data_editor(df_l, use_container_width=True, hide_index=True, key="ed_ing")
    
    c_btn1, c_btn2 = st.columns(2)
    with c_btn1:
        if st.button("💾 GUARDAR CAMBIOS DE EDICIÓN"):
            conn = get_db_connection(); cursor = conn.cursor()
            for _, r in ed_df.iterrows():
                c_envase = float(r['costo_total_envase'])
                q_envase = float(r['cantidad_envase'])
                new_u = c_envase / q_envase if q_envase > 0 else 0
                
                cursor.execute("""
                    UPDATE ingredientes_supra 
                    SET descripcion=%s, um=%s, costo_total_envase=%s, cantidad_envase=%s, costo_unitario=%s, proveedor=%s 
                    WHERE codigo_ingrediente=%s
                """, (r['descripcion'], r['um'], c_envase, q_envase, new_u, r['proveedor'], r['codigo_ingrediente']))
            conn.commit()
            recalcular_costos_cascada()
            conn.close()
            st.success("Sincronizado")
            st.rerun()

    with c_btn2:
        excel_ins = descargar_excel_simple(df_l, "Insumos")
        st.download_button(
            label="📥 Exportar Insumos (Excel)", 
            data=excel_ins, 
            file_name="insumos_supra.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# --- MODULO 2: COMPONENTES ---
elif menu == "🍳 Componentes":
    st.header("Elaboración de Componentes")
    with st.expander("➕ Crear Nuevo Componente"):
        if 'rows_c' not in st.session_state: st.session_state.rows_c = []
        conn = get_db_connection()
        df_cls_c = pd.read_sql("SELECT codigo, tipo, sub_division FROM clasificacion_supra WHERE codigo_final LIKE '2%'", conn)
        
        c1, c2 = st.columns(2)
        nom_c = c1.text_input("Nombre de la Sub-receta")
        if not df_cls_c.empty:
            ops_comp = df_cls_c.apply(lambda x: f"{x['codigo']} - {x['tipo']} ({x['sub_division']})", axis=1).tolist()
            fam_c = c1.selectbox("Familia Componente", ops_comp)
        else:
            st.error("No hay categorías Serie 20")
            fam_c = None
            
        tot_c_placeholder = c2.empty()

        df_i = pd.read_sql("SELECT codigo_ingrediente as id, descripcion as n, um FROM ingredientes_supra", conn); conn.close()
        ops_c = df_i.apply(lambda x: f"{x['id']} - {x['n']} ({x['um']})", axis=1).tolist()

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
            if fam_c:
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
elif menu == "🍽️ Platos Finales":
    st.header("Maestro de Recetas Finales")
    
    @st.cache_data(ttl=600)
    def get_cached_dicts():
        conn = get_db_connection()
        if not conn: return pd.DataFrame(), pd.DataFrame()
        
        items = pd.read_sql("""
            SELECT CAST(codigo_ingrediente AS CHAR) as codigo, descripcion FROM ingredientes_supra
            UNION 
            SELECT CAST(codigo_componente AS CHAR), nombre_receta FROM componentes_maestro
            ORDER BY descripcion
        """, conn)
        
        fams = pd.read_sql("""
            SELECT codigo, CONCAT(tipo, ' - ', sub_division) as categoria 
            FROM clasificacion_supra WHERE codigo_final LIKE '10%'
        """, conn)
        conn.close()
        return items, fams

    df_items_dic, df_fams_dic = get_cached_dicts()

    tabs = st.tabs(["✨ Crear Individual", "🚀 Carga Masiva", "✏️ Editar Receta", "📋 Ver Platos", "🏭 Ficha de Producción"])








   # --- TAB 1: CREAR INDIVIDUAL ---
    with tabs[0]:
        if 'rows_p' not in st.session_state: st.session_state.rows_p = []
        conn = get_db_connection()
        df_cls_p = pd.read_sql("SELECT codigo, tipo, sub_division FROM clasificacion_supra WHERE codigo_final LIKE '10%'", conn)
        
        col_m1, col_m2 = st.columns(2)
        p_nom = col_m1.text_input("Nombre del Nuevo Plato").upper().strip()
        
        if not df_cls_p.empty:
            ops_plat = df_cls_p.apply(lambda x: f"{x['codigo']} - {x['tipo']} ({x['sub_division']})", axis=1).tolist()
            p_fam = col_m1.selectbox("Categoría de Plato", ops_plat)
        else:
            st.error("⚠️ No hay categorías Serie 10 cargadas.")
            p_fam = None
            
        # Input en KG, base de datos espera Gramos
        p_kg = col_m2.number_input("Peso Final Sugerido (KG)", value=0.000, format="%.3f")
        p_gr = p_kg * 1000  
        p_tot_view = col_m2.empty()

        ops_p = df_items_dic.apply(lambda x: f"{x['codigo']} - {x['descripcion']}", axis=1).tolist()

        if st.button("➕ Agregar Insumo/Sub-receta"): 
            st.session_state.rows_p.append({"id": "", "cant": 0.0, "merma": 0.0})

        acum_p = 0.0
        for i, row in enumerate(st.session_state.rows_p):
            cols = st.columns([3, 1, 1, 1])
            st.session_state.rows_p[i]['id'] = cols[0].selectbox(f"Item {i}", [""] + ops_p, key=f"p_s_{i}")
            
            # Cantidad Bruta
            st.session_state.rows_p[i]['cant'] = cols[1].number_input("Cant. Bruta", key=f"p_c_{i}", format="%.4f", value=float(row.get('cant', 0.0)))
            
            # Porcentaje de Merma
            st.session_state.rows_p[i]['merma'] = cols[2].number_input("Merma (%)", key=f"p_m_{i}", format="%.2f", value=float(row.get('merma', 0.0)))
            
            if st.session_state.rows_p[i]['id']:
                cant_bruta = st.session_state.rows_p[i]['cant']
                merma_pct = st.session_state.rows_p[i]['merma']
                
                # EL COSTO SE CALCULA SOBRE EL BRUTO (Lo que compramos)
                val_costo = get_item_cost(st.session_state.rows_p[i]['id'].split(" - ")[0]) * cant_bruta
                acum_p += val_costo
                
                # Visualizamos también el neto para control del usuario
                cant_neta = cant_bruta * (1 - (merma_pct / 100.0))
                cols[3].write(f"Costo: ${val_costo:.2f} | Neto: {cant_neta:.3f}")
        
        p_tot_view.metric("COSTO TOTAL CALCULADO", f"$ {acum_p:.2f}")

        if st.button("💾 GUARDAR PLATO FINAL"):
            if p_fam and p_nom: 
                conn = get_db_connection(); cursor = conn.cursor()
                try:
                    pre = p_fam.split(" - ")[0]
                    cursor.execute("SELECT codigo_final FROM clasificacion_supra WHERE codigo = %s LIMIT 1", (pre,))
                    id_cls_real = cursor.fetchone()[0]
                    cid = get_next_code(pre, "platos_maestro", "codigo_plato_supra")
                    
                    cursor.execute("INSERT INTO platos_maestro (codigo_plato_supra, nombre_plato, id_clasificacion, peso_total_gramos) VALUES (%s,%s,%s,%s)", (cid, p_nom, id_cls_real, p_gr))
                    
                    detalles_insert = []
                    for r in st.session_state.rows_p:
                        if r['id']:
                            cod_hijo = r['id'].split(" - ")[0]
                            c_bruta = float(r['cant'])
                            c_merma = float(r['merma'])
                            c_neta = c_bruta * (1 - (c_merma / 100.0))
                            
                            detalles_insert.append((cid, cod_hijo, c_bruta, c_merma, c_neta))
                    
                    if detalles_insert:
                        # Insertamos atacando las nuevas columnas
                        cursor.executemany("""
                            INSERT INTO platos_detalle 
                            (codigo_plato_padre, codigo_hijo, cantidad_bruta, porcentaje_merma, cantidad_neta) 
                            VALUES (%s,%s,%s,%s,%s)
                        """, detalles_insert)
                        
                    conn.commit()
                    recalcular_costos_cascada()
                    st.success(f"Plato {cid} creado exitosamente.")
                    st.session_state.rows_p = []; st.rerun()
                except Exception as e:
                    conn.rollback(); st.error(f"Error de DB: {e}")
                finally: conn.close()
            else: st.warning("⚠️ Completá nombre y categoría.")








    # --- TAB 2: CARGA MASIVA (NUEVA ESTRUCTURA BRUTO/NETO) ---
    with tabs[1]:
        st.subheader("Importación Masiva de Recetas (Control por ID)")
        col_down1, col_down2 = st.columns(2)
        
        columnas_pro = ['ID_PLATO_FORZADO', 'nombre_plato', 'codigo_familia', 'peso_total', 'codigo_item', 'cantidad', 'Merma']

        with col_down1:
            conn = get_db_connection()
            if conn:
                # SE ACTUALIZÓ LA QUERY: Ahora lee cantidad_bruta y porcentaje_merma
                df_actual = pd.read_sql("""
                    SELECT 
                        p.codigo_plato_supra AS ID_PLATO_FORZADO,
                        p.nombre_plato, 
                        LEFT(p.codigo_plato_supra, 5) as codigo_familia, 
                        (p.peso_total_gramos / 1000.0) as peso_total,
                        CONCAT(d.codigo_hijo, ' - ', COALESCE(i.descripcion, c.nombre_receta)) as codigo_item, 
                        COALESCE(d.cantidad_bruta, 0) as cantidad,
                        COALESCE(d.porcentaje_merma, 0) as Merma
                    FROM platos_maestro p
                    LEFT JOIN platos_detalle d ON p.codigo_plato_supra = d.codigo_plato_padre
                    LEFT JOIN ingredientes_supra i ON d.codigo_hijo = i.codigo_ingrediente
                    LEFT JOIN componentes_maestro c ON d.codigo_hijo = c.codigo_componente
                    ORDER BY p.codigo_plato_supra
                """, conn)
                conn.close()
                btn_actual = descargar_excel_asistente(df_actual, df_items_dic, df_fams_dic)
                st.download_button("📥 Descargar Recetario con IDs (Para Editar)", data=btn_actual, 
                                   file_name=f"RECETARIO_SUPRA_CONTROL_{datetime.now().strftime('%Y%m%d')}.xlsx")

        with col_down2:
            df_vacio = pd.DataFrame(columns=columnas_pro)
            df_vacio.loc[0] = ["", "EJEMPLO: PLATO NUEVO", "10101", 0.500, "30101001 - ACEITUNAS", 0.250, 5]
            btn_plantilla = descargar_excel_asistente(df_vacio, df_items_dic, df_fams_dic)
            st.download_button("📄 Descargar Plantilla Vacía", data=btn_plantilla, file_name="PLANTILLA_MASIVA_SUPRA.xlsx")

        st.divider()
        archivo_p = st.file_uploader("Subir Excel editado:", type=['xlsx'], key="bulk_p_fix_v2")
        
        if archivo_p and st.button("🚀 INICIAR IMPORTACIÓN", key="btn_import_platos"):
            conn = None
            try:
                df_bulk = pd.read_excel(archivo_p, sheet_name='CARGA_RECETAS').fillna("")
                
                df_bulk['nombre_plato'] = df_bulk['nombre_plato'].astype(str).str.strip().str.upper()
                df_bulk['codigo_familia'] = df_bulk['codigo_familia'].astype(str).str.replace(r'\D', '', regex=True)
                
                df_bulk['codigo_item'] = df_bulk['codigo_item'].astype(str).apply(lambda x: str(x).split(' - ')[0].strip() if ' - ' in str(x) else re.sub(r'\D', '', str(x)))
                
                if 'ID_PLATO_FORZADO' in df_bulk.columns:
                    df_bulk['ID_PLATO_FORZADO'] = df_bulk['ID_PLATO_FORZADO'].astype(str).str.replace(r'\D', '', regex=True)

                if 'Merma' in df_bulk.columns:
                    df_bulk['Merma'] = pd.to_numeric(df_bulk['Merma'], errors='coerce').fillna(0)
                else:
                    df_bulk['Merma'] = 0

                conn = get_db_connection(); cursor = conn.cursor()
                local_counters = {}
                p_count = 0
                
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 500;")
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
                cursor.execute("SET UNIQUE_CHECKS = 0;")
                
                with st.status("Procesando recetas...", expanded=True) as status:
                    df_bulk['_group_key'] = df_bulk.get('ID_PLATO_FORZADO', '') + "_" + df_bulk['nombre_plato']
                    grupos = df_bulk.groupby('_group_key')
                    
                    for key, grupo in grupos:
                        row_h = grupo.iloc[0]
                        nombre = str(row_h['nombre_plato']).strip()
                        if not nombre or "EJEMPLO" in nombre: continue
                        
                        fam_prefix = str(row_h['codigo_familia'])[:5]
                        forced_id = str(row_h.get('ID_PLATO_FORZADO', '')).strip()
                        
                        cursor.execute("SELECT codigo_final FROM clasificacion_supra WHERE codigo = %s LIMIT 1", (fam_prefix,))
                        c_res = cursor.fetchone()
                        if not c_res:
                            status.write(f"⚠️ Familia {fam_prefix} no existe para '{nombre}'. Saltando.")
                            continue
                        id_cls_final = c_res[0]

                        if forced_id and len(forced_id) >= 6:
                            pid = forced_id 
                        else:
                            if fam_prefix not in local_counters:
                                cursor.execute(f"SELECT MAX(codigo_plato_supra) FROM platos_maestro WHERE codigo_plato_supra LIKE '{fam_prefix}%'")
                                db_max = cursor.fetchone()[0]
                                local_counters[fam_prefix] = int(db_max) if db_max else int(f"{fam_prefix}000")
                            local_counters[fam_prefix] += 1
                            pid = str(local_counters[fam_prefix])

                        try:
                            peso_f = float(row_h['peso_total']) * 1000 if row_h['peso_total'] != "" else 0
                        except:
                            peso_f = 0
                        
                        cursor.execute("""
                            INSERT INTO platos_maestro (codigo_plato_supra, nombre_plato, id_clasificacion, peso_total_gramos)
                            VALUES (%s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE 
                                nombre_plato=VALUES(nombre_plato), 
                                id_clasificacion=VALUES(id_clasificacion), 
                                peso_total_gramos=VALUES(peso_total_gramos)
                        """, (pid, nombre, id_cls_final, peso_f))

                        cursor.execute("DELETE FROM platos_detalle WHERE codigo_plato_padre = %s", (pid,))

                        detalles = []
                        for _, row_d in grupo.iterrows():
                            c_hijo = str(row_d['codigo_item'])
                            if not c_hijo: continue
                            
                            try: cant_bruta = float(row_d['cantidad'])
                            except: cant_bruta = 0.0

                            try: merma_pct = float(row_d['Merma'])
                            except: merma_pct = 0.0

                            # LÓGICA DE MERMA: Rendimiento Real
                            cant_neta = cant_bruta * (1 - (merma_pct / 100.0))

                            if c_hijo.startswith('2'):
                                cursor.execute("SELECT 1 FROM componentes_maestro WHERE codigo_componente = %s", (c_hijo,))
                                if not cursor.fetchone():
                                    cursor.execute("INSERT INTO componentes_maestro (codigo_componente, nombre_receta, costo_total_calculado) VALUES (%s, %s, 0)", 
                                                 (c_hijo, f"AUTO-GEN: {c_hijo}"))

                            # SE ACTUALIZÓ: Agregamos la tupla con los 3 valores
                            detalles.append((pid, c_hijo, cant_bruta, merma_pct, cant_neta))
                            
                        if detalles:
                            # SE ACTUALIZÓ: El INSERT ahora ataca las 3 columnas nuevas
                            cursor.executemany("""
                                INSERT INTO platos_detalle 
                                (codigo_plato_padre, codigo_hijo, cantidad_bruta, porcentaje_merma, cantidad_neta) 
                                VALUES (%s,%s,%s,%s,%s)
                            """, detalles)
                        
                        p_count += 1
                
                conn.commit()
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
                cursor.execute("SET UNIQUE_CHECKS = 1;")
                recalcular_costos_cascada()
                status.update(label=f"¡Éxito! Se sincronizaron {p_count} platos.", state="complete")
                st.rerun()
            except Exception as e:
                if conn: conn.rollback()
                st.error(f"❌ Error en la importación masiva: {e}")
            finally:
                if conn: conn.close()








    # --- TAB 3: EDICIÓN ---
    with tabs[2]:
        st.subheader("Editor Técnico de Recetas")
        conn = get_db_connection()
        if conn:
            df_ex = pd.read_sql("SELECT codigo_plato_supra as cod, nombre_plato as n FROM platos_maestro ORDER BY n", conn)
            plato_sel = st.selectbox("Seleccionar Plato:", [""] + df_ex['n'].tolist())
            
            if plato_sel:
                row_p = df_ex[df_ex['n'] == plato_sel].iloc[0]
                c_ed = row_p['cod']
                
                # Extraemos las 3 columnas de control de volumen
                det = pd.read_sql(f"""
                    SELECT d.id_detalle_plato, d.codigo_hijo, COALESCE(i.descripcion, c.nombre_receta) as item,
                           d.cantidad_bruta, d.porcentaje_merma, d.cantidad_neta, COALESCE(i.um, 'N/A') as unidad,
                           COALESCE(i.costo_unitario, c.costo_total_calculado) as costo_un
                    FROM platos_detalle d
                    LEFT JOIN ingredientes_supra i ON d.codigo_hijo = i.codigo_ingrediente
                    LEFT JOIN componentes_maestro c ON d.codigo_hijo = c.codigo_componente
                    WHERE d.codigo_plato_padre = '{c_ed}'
                """, conn)
                
                # Subtotal en base a lo comprado (Bruto)
                det['subtotal'] = det['cantidad_bruta'] * det['costo_un'].fillna(0)
                
                # Data Editor con bloqueo inteligente de celdas
                ed_det = st.data_editor(det, use_container_width=True, hide_index=True,
                    column_config={
                        "id_detalle_plato": None,
                        "codigo_hijo": st.column_config.Column("Código", disabled=True),
                        "item": st.column_config.Column("Insumo / Componente", disabled=True),
                        "unidad": st.column_config.Column("UM", disabled=True),
                        "costo_un": st.column_config.NumberColumn("Costo x UM", format="$ %.2f", disabled=True),
                        "cantidad_bruta": st.column_config.NumberColumn("Cant. Bruta (✎)", format="%.4f"),
                        "porcentaje_merma": st.column_config.NumberColumn("Merma % (✎)", format="%.2f"),
                        "cantidad_neta": st.column_config.NumberColumn("Neto", format="%.4f", disabled=True),
                        "subtotal": st.column_config.NumberColumn("Costo Item", format="$ %.2f", disabled=True)
                    }
                )
                
                if st.button("💾 ACTUALIZAR FICHA"):
                    cursor = conn.cursor()
                    for _, r in ed_det.iterrows():
                        # Recalculamos la neta en backend por si editaron Bruta o Merma en la UI
                        c_bruta = float(r['cantidad_bruta'])
                        p_merma = float(r['porcentaje_merma'])
                        c_neta = c_bruta * (1 - (p_merma / 100.0))
                        
                        cursor.execute("""
                            UPDATE platos_detalle 
                            SET cantidad_bruta=%s, porcentaje_merma=%s, cantidad_neta=%s 
                            WHERE id_detalle_plato=%s
                        """, (c_bruta, p_merma, c_neta, r['id_detalle_plato']))
                    
                    conn.commit()
                    recalcular_costos_cascada()
                    st.success("Receta actualizada y rendimientos recalculados.")
                    st.rerun()
            conn.close()





    # --- TAB 4: VISOR ---
    with tabs[3]:
        st.subheader("Visor de Producción")
        conn = get_db_connection()
        if conn:
            # Añadimos el cálculo del costo por KG para tener la info completa aquí también
            df_res = pd.read_sql("""
                SELECT 
                    codigo_plato_supra as 'Código', 
                    nombre_plato as 'Plato', 
                    peso_total_gramos as 'Gramaje Real (N)', 
                    costo_total_calculado as 'Costo Total ($)',
                    ROUND(costo_total_calculado / NULLIF(peso_total_gramos/1000, 0), 2) as 'Costo x KG ($)'
                FROM platos_maestro 
                ORDER BY codigo_plato_supra DESC
            """, conn)
            
            st.dataframe(df_res.style.format({
                'Gramaje Real (N)': '{:,.0f} g',
                'Costo Total ($)': '${:,.2f}',
                'Costo x KG ($)': '${:,.2f}'
            }), use_container_width=True, hide_index=True)
            conn.close()


            # --- TAB 5: FICHA DE PRODUCCIÓN (MRP) ---
    with tabs[4]:
        st.subheader("Ficha de Producción y Explosión de Materiales")
        st.write("Ingresá la cantidad a producir por plato. El sistema calculará el Picking List exacto (en Bruto).")
        
        conn = get_db_connection()
        if conn:
            # 1. Grilla editable para ingresar cantidades a producir
            df_platos = pd.read_sql("SELECT codigo_plato_supra as ID, nombre_plato as Plato, 0 as Cantidad FROM platos_maestro ORDER BY Plato", conn)
            
            ed_prod = st.data_editor(
                df_platos, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "ID": st.column_config.Column("Código", disabled=True),
                    "Plato": st.column_config.Column("Plato Final", disabled=True),
                    "Cantidad": st.column_config.NumberColumn("Unidades a Producir (✎)", min_value=0, step=1)
                }
            )
            
            if st.button("⚙️ GENERAR PICKING LIST"):
                produccion = ed_prod[ed_prod['Cantidad'] > 0]
                
                if produccion.empty:
                    st.warning("⚠️ Debes ingresar al menos 1 unidad a producir en algún plato.")
                else:
                    platos_dict = dict(zip(produccion['ID'], produccion['Cantidad']))
                    ids_platos = tuple(platos_dict.keys())
                    
                    ids_str = f"('{ids_platos[0]}')" if len(ids_platos) == 1 else str(ids_platos)
                    
                    # --- EXPLOSIÓN DE MATERIALES (BOM) ACTUALIZADA ---
                    
                    # Query A: Insumos Directos (Serie 30) - LECTURA DE cantidad_bruta
                    query_directos = f"""
                        SELECT 
                            d.codigo_plato_padre as plato_id, 
                            i.codigo_ingrediente as cod_insumo, 
                            i.descripcion as insumo, 
                            i.um, 
                            d.cantidad_bruta as q_req
                        FROM platos_detalle d
                        JOIN ingredientes_supra i ON d.codigo_hijo = i.codigo_ingrediente
                        WHERE d.codigo_plato_padre IN {ids_str}
                    """
                    
                    # Query B: Insumos Indirectos (Dentro de Componentes Serie 20)
                    # Aquí asumimos que en tu tabla componentes_detalle la columna se llama 'cantidad_bruta'
                    # Si se llama distinto (ej. cantidad_inicial), cambialo en la línea '(d.cantidad_bruta * cd.cantidad_bruta) as q_req'
                    query_indirectos = f"""
                        SELECT 
                            d.codigo_plato_padre as plato_id, 
                            i.codigo_ingrediente as cod_insumo, 
                            i.descripcion as insumo, 
                            i.um, 
                            (d.cantidad_bruta * cd.cantidad_bruta) as q_req
                        FROM platos_detalle d
                        JOIN componentes_maestro c ON d.codigo_hijo = c.codigo_componente
                        JOIN componentes_detalle cd ON c.codigo_componente = cd.codigo_padre
                        JOIN ingredientes_supra i ON cd.codigo_hijo = i.codigo_ingrediente
                        WHERE d.codigo_plato_padre IN {ids_str}
                    """
                    
                    try:
                        df_dir = pd.read_sql(query_directos, conn)
                        df_indir = pd.read_sql(query_indirectos, conn)
                        
                        df_total = pd.concat([df_dir, df_indir], ignore_index=True)
                        
                        if not df_total.empty:
                            df_total['Multiplicador'] = df_total['plato_id'].map(platos_dict)
                            df_total['Total_Bruto'] = df_total['q_req'] * df_total['Multiplicador']
                            
                            # Agrupación para consolidar Picking List
                            df_consolidado = df_total.groupby(['cod_insumo', 'insumo', 'um'])['Total_Bruto'].sum().reset_index()
                            df_consolidado.sort_values('insumo', inplace=True)
                            
                            st.divider()
                            col_res1, col_res2 = st.columns([1, 2])
                            
                            with col_res1:
                                st.markdown("### 🍽️ Orden de Producción")
                                st.dataframe(produccion[['Plato', 'Cantidad']], hide_index=True, use_container_width=True)
                                
                            with col_res2:
                                st.markdown("### 📦 Picking List (Bruto para Depósito)")
                                st.dataframe(
                                    df_consolidado.style.format({'Total_Bruto': '{:,.3f}'}).background_gradient(subset=['Total_Bruto'], cmap='Blues'),
                                    column_config={
                                        "cod_insumo": "Código", 
                                        "insumo": "Insumo Requerido", 
                                        "um": "UM", 
                                        "Total_Bruto": "Cantidad Total (Bruta)"
                                    },
                                    hide_index=True, 
                                    use_container_width=True
                                )
                                
                                excel_picking = descargar_excel_simple(df_consolidado, "Picking_List")
                                st.download_button(
                                    label="📥 Descargar Picking List (Excel)", 
                                    data=excel_picking, 
                                    file_name=f"PICKING_SUPRA_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                )
                        else:
                            st.info("No se encontraron insumos configurados para los platos seleccionados.")
                    except Exception as e:
                        st.error(f"Error generando Picking List: {e}")
                        # TIP SENIOR: Si salta error acá, revisá si en la tabla componentes_detalle tenés la columna 'cantidad_bruta'.
                        # Si tu columna se llama diferente en componentes_detalle (ej. cantidad_neta, cantidad_inicial), avisame y lo ajustamos.
            conn.close()