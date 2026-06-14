#region SESSÃO 1: Imports, Configurações e Funções de Base
import io
import time
import math
import re
import os
import shutil
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from streamlit_calendar import calendar

import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from streamlit_js_eval import get_geolocation
import networkx as nx
import matplotlib
import matplotlib.pyplot as plt

import plotly.express as px
import plotly.graph_objects as go
from streamlit_echarts import st_echarts, JsCode

# --- NOVO BLOCO: INTELIGÊNCIA DA MALHA ---
@st.cache_data
def carregar_grafo():
    # Usando sheet_name=0 e sheet_name=1 garantimos que o Python vai ler 
    # a primeira e a segunda aba na ordem, não importa o nome que estiver lá.
    
    # 1. Lendo a aba de Pátios (Primeira aba = Índice 0)
    nodes = pd.read_excel('Dados_Pátios.xlsx', sheet_name=0).dropna()
    
    # 2. Lendo a aba de Ligações (Segunda aba = Índice 1)
    edges = pd.read_excel('Dados_Pátios.xlsx', sheet_name=1).dropna()
    
    # 3. Montando o Grafo
    G = nx.from_pandas_edgelist(
        edges, 'Origem', 'Destino', 
        edge_attr=['Distancia_KM', 'Tempo_Ciclo_Bruto_Min', 'Status_Via'], 
        create_using=nx.DiGraph()
    )
    return G, nodes

G, nodes = carregar_grafo()

# --- CONFIGURAÇÕES GLOBAIS ---
st.set_page_config(page_title="Painel de OS Eletroeletrônica", layout="wide")

# SÓ MOSTRA O TÍTULO SE NÃO ESTIVER LOGADO
if not st.session_state.get("logged_in", False):
    # Criamos três colunas: as laterais vazias centralizam a do meio
    col_vazia1, col_centro, col_vazia2 = st.columns([1, 6, 1])
    with col_centro:
        st.markdown("<h1 style='text-align: center;'>⚡ Sistema de Gestão de Ordens de Serviço</h1>", unsafe_allow_html=True)

# --- FUNÇÕES DE APOIO E PERSISTÊNCIA (Definidas ANTES de serem usadas) ---
def db_path():
    return "baixas_os.db"

def db_users_path():
    return "usuarios.db"

def hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

# Variáveis de status globais
_status_prazo  = {"REALIZADO"}
_status_atraso = {"REALIZADO FORA DA DATA DE PROGRAMAÇÃO", "REALIZADO FORA DO PRAZO"}
_status_aberto = {"NÃO REALIZADO", "NAO REALIZADO", "PENDENTE", "ATRASADO", ""}

def init_db():
    # 1. Banco de Operação (Baixas)
    conn_b = sqlite3.connect(db_path())
    cur_b = conn_b.cursor()
    cur_b.execute("CREATE TABLE IF NOT EXISTS baixas (os TEXT PRIMARY KEY, status TEXT NOT NULL, realizado_em TEXT NOT NULL, coordenacao TEXT NOT NULL, concluido_por TEXT)")
    conn_b.commit(); conn_b.close()
    
    # 2. Banco de Segurança (Usuários)
    conn_u = sqlite3.connect(db_users_path())
    cur_u = conn_u.cursor()
    cur_u.execute("CREATE TABLE IF NOT EXISTS usuarios (username TEXT PRIMARY KEY, senha_hash TEXT NOT NULL, perfil TEXT NOT NULL, escopo TEXT NOT NULL)")
    conn_u.commit(); conn_u.close()

def atualizar_banco_usuarios():
    # Agora aponta para o banco isolado de usuários
    conn = sqlite3.connect(db_users_path())
    cur = conn.cursor()
    # Adicionamos as colunas extras de segurança
    cols = {
        "palavra_recuperacao": "TEXT", 
        "dica_recuperacao": "TEXT", 
        "reset_obrigatorio": "INTEGER DEFAULT 1", 
        "coordenacao_padrao": "TEXT DEFAULT 'ICG'"
    }
    for col, tipo in cols.items():
        try: cur.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {tipo}")
        except sqlite3.OperationalError: pass
    conn.commit(); conn.close()

# AGORA, como as funções já existem, podemos chamá-las com segurança:
init_db()
atualizar_banco_usuarios()
#endregion

#region SESSÃO 1.5: Barreira de Login Corrigida
if "logged_in" not in st.session_state:
    st.session_state.update({"logged_in": False, "username": "", "perfil": "", "escopo": "", "needs_reset": False, "recuperando": False, "trocar_senha": False})

if not st.session_state["logged_in"]:
    st.markdown("<h3 style='text-align: center; color: #475569;'>Acesso Restrito</h3>", unsafe_allow_html=True)
    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    
    with col_l2:
        # 1. FLUXO DE RESET OBRIGATÓRIO (Troca de senha + Definição de Palavra-Chave)
        if st.session_state.get("needs_reset"):
            st.warning("⚠️ Bem-vindo! Configure sua senha e sua palavra de recuperação.")
            with st.form("form_reset"):
                nova_senha = st.text_input("Nova Senha", type="password")
                conf_senha = st.text_input("Confirmar Nova Senha", type="password")
                palavra_nova = st.text_input("Definir sua Palavra-Chave de Recuperação")
                
                if st.form_submit_button("Finalizar Cadastro"):
                    if nova_senha != conf_senha:
                        st.error("As senhas não conferem.")
                    elif not palavra_nova:
                        st.error("Você precisa definir uma palavra-chave!")
                    else:
                        conn = sqlite3.connect(db_users_path())
                        # Agora atualizamos a senha, a palavra-chave e tiramos a obrigatoriedade
                        conn.cursor().execute("""
                            UPDATE usuarios 
                            SET senha_hash = ?, palavra_recuperacao = ?, reset_obrigatorio = 0 
                            WHERE username = ?
                        """, (hash_senha(nova_senha), palavra_nova.strip(), st.session_state["reset_user"]))
                        conn.commit(); conn.close()
                        st.success("Configuração concluída! Entre com sua nova senha."); st.session_state["needs_reset"] = False; st.rerun()
            if st.button("⬅️ Voltar"):
                st.session_state["needs_reset"] = False; st.rerun()

        # 2. FLUXO DE RECUPERAÇÃO (Esqueci a senha)
        elif st.session_state.get("recuperando"):
            st.info("Digite seu login e a palavra-chave.")
            with st.form("form_recuperar"):
                user_rec = st.text_input("Login")
                palavra_rec = st.text_input("Palavra-Chave")
                submit_rec = st.form_submit_button("Validar")
            
            # O botão de voltar deve ficar FORA do form
            if submit_rec:
                conn = sqlite3.connect(db_users_path())
                cur = conn.cursor()
                cur.execute("SELECT palavra_recuperacao FROM usuarios WHERE username = ?", (user_rec.strip(),))
                row = cur.fetchone(); conn.close()
                if row and row[0] == palavra_rec.strip():
                    st.session_state["needs_reset"] = True
                    st.session_state["reset_user"] = user_rec.strip()
                    st.session_state["recuperando"] = False
                    st.rerun()
                else: st.error("Dados incorretos.")
                
            if st.button("⬅️ Voltar ao Login"): 
                st.session_state["recuperando"] = False; st.rerun()

        # 3. FLUXO DE LOGIN PADRÃO
        else:
            with st.form("form_login"):
                user_input = st.text_input("Usuário")
                pass_input = st.text_input("Senha", type="password")
                submit = st.form_submit_button("Entrar", use_container_width=True)
            
            if submit:
                # AQUI ESTAVA O ERRO! CORRIGIDO PARA db_users_path()
                conn = sqlite3.connect(db_users_path())
                cur = conn.cursor()
                cur.execute("SELECT senha_hash, perfil, escopo, reset_obrigatorio FROM usuarios WHERE username = ?", (user_input.strip(),))
                row = cur.fetchone(); conn.close()
                if row and row[0] == hash_senha(pass_input):
                    if row[3] == 1:
                        st.session_state["needs_reset"] = True
                        st.session_state["reset_user"] = user_input.strip()
                        st.rerun()
                    else:
                        st.session_state.update({"logged_in": True, "username": user_input.strip(), "perfil": row[1], "escopo": row[2]})
                        st.rerun()
                else: st.error("❌ Usuário ou senha incorretos.")
            
            if st.button("Esqueci minha senha"): st.session_state["recuperando"] = True; st.rerun()
    st.stop()
#endregion

#region SESSÃO 2.0 ===== Inteligência da Malha Ferroviária =====
#region             ===== Inteligência da Malha Ferroviária =====
@st.cache_data
def carregar_grafo():
    # 1. Lendo a aba de Pátios (Índice 0)
    nodes = pd.read_excel('Dados_Pátios.xlsx', sheet_name=0)
    nodes = nodes.dropna(how='all') # Apaga apenas se a linha toda for vazia
    
    # 2. Lendo a aba de Ligações (Índice 1)
    edges = pd.read_excel('Dados_Pátios.xlsx', sheet_name=1)
    
    # 3. Limpeza Inteligente (O segredo para o Grafo não ficar vazio)
    # Apaga a linha apenas se estiver faltando a Origem ou o Destino
    edges = edges.dropna(subset=['Origem', 'Destino'])
    
    # Preenche vazios nas outras colunas para o código não quebrar
    if 'Status_Via' in edges.columns:
        edges['Status_Via'] = edges['Status_Via'].fillna('Aberta')
        
    if 'Tempo_Ciclo_Bruto_Min' in edges.columns:
        edges['Tempo_Ciclo_Bruto_Min'] = edges['Tempo_Ciclo_Bruto_Min'].fillna(0)
        
    if 'Distancia_KM' in edges.columns:
        edges['Distancia_KM'] = edges['Distancia_KM'].fillna(1.0)

    # Coleta apenas os atributos que realmente existem no seu Excel
    atributos_arestas = [col for col in ['Distancia_KM', 'Tempo_Ciclo_Bruto_Min', 'Status_Via'] if col in edges.columns]
    
    # 4. Montando o Grafo Direcionado
    G = nx.from_pandas_edgelist(
        edges, 'Origem', 'Destino', 
        edge_attr=atributos_arestas, 
        create_using=nx.DiGraph()
    )
    return G, nodes

# Inicializa o grafo globalmente para uso nas abas
G, nodes = carregar_grafo()
#endregion

#region SESSÃO 2.1 ===== Lógica =====
def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    # Agressivo contra sujeiras do SAP: remove \n, \r, espaços extras e deixa maiúsculo
    df.columns = df.columns.astype(str).str.replace('\n', ' ').str.replace('\r', '').str.strip().str.upper()
    return df

def pick_first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None

def classificar_atividade(atividade: str) -> str:
    s = str(atividade).upper()
    if "_MAN_CONF_" in s:
        return "Confiabilidade e Segurança"
    if "_SEG_" in s:
        return "Segurança"
    if "_CONF_" in s:
        return "Confiabilidade"
    return "Confiabilidade"

def extrair_criticidade(prioridade: str):
    p = str(prioridade).strip()
    m = re.match(r"^\s*([1-4])\s*[-–]?\s*(.*)$", p)
    if m:
        codigo = int(m.group(1))
        mapa = {1: "Muito Alta", 2: "Alta", 3: "Média", 4: "Baixa"}
        return codigo, mapa.get(codigo, "Baixa")

    pu = p.upper()
    if "MUITO" in pu and "ALTA" in pu:
        return 1, "Muito Alta"
    if "ALTA" in pu:
        return 2, "Alta"
    if "MÉDIA" in pu or "MEDIA" in pu:
        return 3, "Média"
    if "BAIXA" in pu:
        return 4, "Baixa"
    return 4, "Baixa"

def calcular_nivel_prioridade(classificacao: str, criticidade_rank: int) -> int:
    # Ordem solicitada:
    # 1) Confiabilidade e Segurança
    # 2) Segurança
    # 3) Confiabilidade
    base_map = {
        "Confiabilidade e Segurança": 1,
        "Segurança": 2,
        "Confiabilidade": 3
    }
    base = base_map.get(classificacao, 3)
    return base * 10 + int(criticidade_rank)

def parse_data_programada(valor):
    if pd.isna(valor):
        return pd.NaT
    try:
        return pd.to_datetime(valor, dayfirst=True, errors="coerce")
    except Exception:
        return pd.NaT

def agora_dt():
    return datetime.now()

def formatar_dt_br(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y %H:%M")

def determinar_status_execucao(data_programada: pd.Timestamp, realizado_em: datetime) -> str:
    # Realizado = antes ou na data programada
    # Realizado Fora = após a data programada
    # Se data programada estiver vazia, assume Realizado
    if pd.isna(data_programada):
        return "Realizado"

    data_prog_dia = pd.to_datetime(data_programada).date()
    data_real_dia = realizado_em.date()

    if data_real_dia <= data_prog_dia:
        return "Realizado"
    return "Realizado Fora da Data de Programação"

def haversine_vectorized(lat1, lon1, lat2_series, lon2_series):
    R = 6371.0
    lat1 = np.radians(float(lat1))
    lon1 = np.radians(float(lon1))

    lat2 = np.radians(lat2_series.astype(float).to_numpy())
    lon2 = np.radians(lon2_series.astype(float).to_numpy())

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    c = 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return R * c

@st.cache_data(show_spinner=False)
def geocode_endereco(texto: str):
    geolocator = Nominatim(user_agent="gestao_os_eletro_mrs", timeout=10)
    return geolocator.geocode(texto + ", Brasil")


@st.cache_data(show_spinner=False)
def reverse_geocode_coordenada(lat: float, lon: float) -> str:
    try:
        geolocator = Nominatim(user_agent="gestao_os_eletro_mrs", timeout=10)
        location = geolocator.reverse(
            (float(lat), float(lon)),
            exactly_one=True,
            language="pt-BR",
            addressdetails=True
        )

        if not location:
            return "GPS Local"

        raw = getattr(location, "raw", {}) or {}
        addr = raw.get("address", {}) or {}

        # Componentes principais
        rua = (
            addr.get("road")
            or addr.get("pedestrian")
            or addr.get("residential")
            or addr.get("footway")
            or addr.get("path")
            or ""
        ).strip()

        numero = (
            addr.get("house_number")
            or ""
        ).strip()

        bairro = (
            addr.get("suburb")
            or addr.get("neighbourhood")
            or addr.get("quarter")
            or ""
        ).strip()

        cidade = (
            addr.get("city")
            or addr.get("town")
            or addr.get("municipality")
            or addr.get("village")
            or ""
        ).strip()

        cep = (
            addr.get("postcode")
            or ""
        ).strip()

        partes = []

        if rua and numero:
            partes.append(f"{rua}, {numero}")
        elif rua:
            partes.append(rua)

        if bairro:
            partes.append(bairro)

        if cidade:
            partes.append(cidade)

        if cep:
            partes.append(cep)

        endereco_curto = ", ".join([p for p in partes if p])

        return endereco_curto if endereco_curto else "GPS Local"

    except Exception:
        return "GPS Local"

def tentar_gps_uma_vez():
    loc = get_geolocation()
    if not loc:
        return False, None, None, "Aguardando resposta do navegador…", None
    if isinstance(loc, dict) and "error" in loc:
        code = loc["error"].get("code")
        msg = loc["error"].get("message", "Erro desconhecido de geolocalização.")
        return False, None, None, f"GPS falhou (code {code}): {msg}", None
    if isinstance(loc, dict) and "coords" in loc:
        coords = loc.get("coords", {})
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        acc = coords.get("accuracy")
        if lat is not None and lon is not None:
            return True, float(lat), float(lon), "Localização obtida via GPS.", acc
    return False, None, None, "Não foi possível interpretar a resposta do GPS.", None
#endregion

# ==========================================================
# Helper para manter a navegação na Aba 3
# ==========================================================
def manter_aba_3():
    st.session_state["aba_ativa_sistema"] = "🕸️ Inteligência da Malha"


def aplicar_falha_sidebar():
    patio_sel_sidebar = st.session_state.get("sim_patio_bloqueado_sidebar", "")
    st.session_state["patio_bloqueado_manual"] = patio_sel_sidebar if patio_sel_sidebar else None
    st.session_state["aba_ativa_sistema"] = "🕸️ Inteligência da Malha"


def restaurar_malha_sidebar():
    st.session_state["patio_bloqueado_manual"] = None
    st.session_state["sim_patio_bloqueado_sidebar"] = ""
    st.session_state["aba_ativa_sistema"] = "🕸️ Inteligência da Malha"

#region SESSÃO 2.2 ===== Persistência (SQLite) =====

def upsert_baixa(os_id: str, status: str, realizado_em_str: str, coordenacao: str, concluido_por: str):
    conn = sqlite3.connect(db_path())
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO baixas (os, status, realizado_em, coordenacao, concluido_por)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(os) DO UPDATE SET
            status=excluded.status,
            realizado_em=excluded.realizado_em,
            concluido_por=excluded.concluido_por
    """, (str(os_id), str(status), str(realizado_em_str), str(coordenacao), str(concluido_por)))
    conn.commit()
    conn.close()

def carregar_baixas_df() -> pd.DataFrame:
    # A inicialização do banco (init_db) já ocorreu globalmente na Sessão 1
    conn = sqlite3.connect(db_path())
    df = pd.read_sql_query("SELECT os, status, realizado_em, coordenacao, concluido_por FROM baixas", conn)
    conn.close()
    if df.empty:
        return df
    df["os"] = df["os"].astype(str)
    return df

#endregion

#region SESSÃO 2.3 ===== Export/Salvar Excel (MASTER) =====
def gerar_excel_bytes(df_export: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    df_to_save = df_export.copy()

    # Data programada como dd/mm/aaaa
    if "Data inicial programada" in df_to_save.columns:
        df_to_save["Data inicial programada"] = pd.to_datetime(
            df_to_save["Data inicial programada"], errors="coerce"
        ).dt.strftime("%d/%m/%Y")

    # Data/Hora Realizado já é texto dd/mm/aaaa hh:mm
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_to_save.to_excel(writer, index=False, sheet_name="OS")

    output.seek(0)
    return output.read()

def _acquire_lock(lock_path: str, timeout_sec: int = 15):
    start = time.time()
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            if time.time() - start > timeout_sec:
                return False
            time.sleep(0.5)

def _release_lock(lock_path: str):
    try:
        os.remove(lock_path)
    except Exception:
        pass

def salvar_excel_com_backup_bytes(excel_bytes: bytes, destino: Path, max_tentativas: int = 5):
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    lock_path = str(destino) + ".lock"
    if not _acquire_lock(lock_path, timeout_sec=20):
        raise RuntimeError("Não foi possível obter lock do arquivo. Talvez outro usuário esteja salvando agora.")

    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = destino.with_name(f"{destino.stem}_backup_{ts}{destino.suffix}")
        tmp_path = destino.with_suffix(destino.suffix + f".tmp_{ts}")

        # Backup do arquivo atual (se existir)
        if destino.exists():
            shutil.copy2(destino, backup_path)

        # Escrita segura em arquivo temporário + replace atômico
        tentativa = 0
        while True:
            try:
                with open(tmp_path, "wb") as f:
                    f.write(excel_bytes)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(tmp_path, destino)  # substitui de forma atômica
                return str(backup_path)
            except PermissionError:
                tentativa += 1
                if tentativa >= max_tentativas:
                    raise
                time.sleep(1.0)
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass
    finally:
        _release_lock(lock_path)
#endregion

#region SESSÃO 2.4 ===== Auxiliares: datas/turnos para gráficos gerenciais =====
def parse_datahora_realizado(valor):
    # Espera texto "dd/mm/aaaa hh:mm" ou vazio
    if pd.isna(valor):
        return pd.NaT
    s = str(valor).strip()
    if not s:
        return pd.NaT
    return pd.to_datetime(s, dayfirst=True, errors="coerce")

def classificar_turno(dt):
    # Turnos definidos pelo Julio:
    # 00:00–06:59 | 07:00–15:59 | 16:00–23:59
    if pd.isna(dt):
        return None
    h = int(dt.hour)
    if 0 <= h < 7:
        return "00h-07h"
    if 7 <= h < 16:
        return "07h-16h"
    return "16h-00h"
#endregion

#region SESSÃO 2.5 ===== Auxiliares da Sidebar: preparação e filtros =====
def preparar_df_visao(df_base: pd.DataFrame, filtro_visao: str) -> pd.DataFrame:
    df_visao = df_base.copy()

    if filtro_visao != "Todas":
        df_visao = df_visao[
            df_visao["Coordenacao"].str.contains(filtro_visao, case=False, na=False)
        ].copy()

    df_visao["Status_norm"] = df_visao["Status da Operação"].astype(str).str.strip().str.upper()
    df_visao["dt_realizado"] = df_visao["Data/Hora Realizado"].apply(parse_datahora_realizado)
    df_visao["Turno"] = df_visao["dt_realizado"].apply(classificar_turno)
    df_visao["dia_realizado"] = pd.to_datetime(df_visao["dt_realizado"], errors="coerce").dt.normalize()
    df_visao["dt_prog_filtro"] = pd.to_datetime(df_visao["Data inicial programada"], errors="coerce")
    df_visao["Turno_Filtro"] = df_visao["Turno"].fillna("Pendente (Sem Turno)")

    return df_visao

def aplicar_filtros_sidebar(
    df_visao: pd.DataFrame,
    patios_selecionados: list,
    classif_selecionadas: list,
    turnos_selecionados: list,
    start_date,
    end_date,
    status_sel: str
) -> pd.DataFrame:
    df_filtrado = df_visao[
        (df_visao["Patio"].isin(patios_selecionados)) &
        (df_visao["Classificacao"].isin(classif_selecionadas)) &
        (df_visao["Turno_Filtro"].isin(turnos_selecionados)) &
        (df_visao["dt_prog_filtro"].dt.date >= start_date) &
        (df_visao["dt_prog_filtro"].dt.date <= end_date)
    ].copy()

    if status_sel == "Todas Concluídas":
        df_filtrado = df_filtrado[
            df_filtrado["Status_norm"].isin(_status_prazo | _status_atraso)
        ]
    elif status_sel == "Concluídas no Prazo":
        df_filtrado = df_filtrado[
            df_filtrado["Status_norm"].isin(_status_prazo)
        ]
    elif status_sel == "Concluídas com Atraso":
        df_filtrado = df_filtrado[
            df_filtrado["Status_norm"].isin(_status_atraso)
        ]
    elif status_sel == "Pendentes":
        df_filtrado = df_filtrado[
            df_filtrado["Status_norm"].isin(_status_aberto)
        ]

    return df_filtrado
#endregion

#region SESSÃO 2.6 ===== Calendário mensal de demanda por pátio =====
import calendar as pycal
from datetime import date

@st.cache_data(show_spinner=False)
def _preparar_df_calendario(df_base_cal: pd.DataFrame) -> pd.DataFrame:
    if df_base_cal.empty:
        return pd.DataFrame()

    df = df_base_cal.copy()

    if "dt_prog_filtro" not in df.columns:
        df["dt_prog_filtro"] = pd.to_datetime(df["Data inicial programada"], errors="coerce")

    if "Status_norm" not in df.columns:
        df["Status_norm"] = df["Status da Operação"].astype(str).str.strip().str.upper()

    if "Nivel_Prioridade" not in df.columns:
        df["Nivel_Prioridade"] = 999

    df = df.dropna(subset=["dt_prog_filtro", "Patio"]).copy()
    if df.empty:
        return df

    df["Patio"] = df["Patio"].astype(str).str.strip().str.upper()
    df["dia_prog"] = pd.to_datetime(df["dt_prog_filtro"], errors="coerce").dt.date
    df["Nivel_Prioridade"] = pd.to_numeric(df["Nivel_Prioridade"], errors="coerce").fillna(999).astype(int)

    return df


@st.cache_data(show_spinner=False)
def montar_eventos_calendario_patios(
    df_base_cal: pd.DataFrame,
    ano: int,
    mes: int,
    max_patios_visiveis: int = 2,
) -> list[dict]:
    """
    Regras:
    - Vermelho: pátio com backlog vencido aberto em relação ao dia
    - Verde: pátio com demanda do dia ainda pendente
    - Azul: pátio com demanda do dia 100% executada
    - Carry-over: vencidas abertas continuam aparecendo nos dias seguintes
    - Sem repetir pátio no mesmo dia
    - Ordenação por menor Nivel_Prioridade
    - Exibe no máximo N siglas por dia + evento sintético '+N'
    """
    df = _preparar_df_calendario(df_base_cal)
    if df.empty:
        return []

    primeiro_dia = date(int(ano), int(mes), 1)
    ultimo_dia = date(int(ano), int(mes), pycal.monthrange(int(ano), int(mes))[1])

    dias_mes = pd.date_range(primeiro_dia, ultimo_dia, freq="D")
    eventos = []

    for dia_ts in dias_mes:
        dia = dia_ts.date()

        # 1) Backlog vencido aberto até o dia (carry-over)
        df_vencidas_abertas = df[
            (df["dia_prog"] < dia) &
            (df["Status_norm"].isin(_status_aberto))
        ].copy()

        # 2) Demanda programada no próprio dia
        df_hoje = df[df["dia_prog"] == dia].copy()

        patios_dia = []

        # Primeiro: vencidas abertas -> vermelho
        if not df_vencidas_abertas.empty:
            agg_venc = (
                df_vencidas_abertas.groupby("Patio", as_index=False)
                .agg(
                    ordem=("Nivel_Prioridade", "min"),
                    qtd_os=("Patio", "size")
                )
                .sort_values(["ordem", "Patio"])
            )

            for _, row in agg_venc.iterrows():
                patios_dia.append({
                    "patio": str(row["Patio"]),
                    "cor": "#FF4B4B",  # vermelho
                    "ordem": int(row["ordem"]),
                    "rank_status": 0
                })

        patios_ja_incluidos = {item["patio"] for item in patios_dia}

        # Depois: demanda do dia
        if not df_hoje.empty:
            for patio, grp in df_hoje.groupby("Patio"):
                if patio in patios_ja_incluidos:
                    continue

                ordem_patio = int(grp["Nivel_Prioridade"].min())
                todos_realizados = (~grp["Status_norm"].isin(_status_aberto)).all()

                patios_dia.append({
                    "patio": str(patio),
                    "cor": "#3B82F6" if todos_realizados else "#10B981",  # azul / verde
                    "ordem": ordem_patio,
                    "rank_status": 2 if todos_realizados else 1
                })

        if not patios_dia:
            continue

        patios_dia = sorted(patios_dia, key=lambda x: (x["rank_status"], x["ordem"], x["patio"]))

        patios_visiveis = patios_dia[:max_patios_visiveis]
        qtd_extra = max(0, len(patios_dia) - len(patios_visiveis))

        for idx, item in enumerate(patios_visiveis):
            eventos.append({
                "title": item["patio"],
                "start": dia.isoformat(),
                "allDay": True,
                "backgroundColor": item["cor"],
                "borderColor": item["cor"],
                "textColor": "#FFFFFF",
                "displayOrder": idx + 1,
            })

        if qtd_extra > 0:
            eventos.append({
                "title": f"+{qtd_extra}",
                "start": dia.isoformat(),
                "allDay": True,
                "backgroundColor": "#94A3B8",
                "borderColor": "#94A3B8",
                "textColor": "#FFFFFF",
                "displayOrder": 99,
            })

    return eventos


@st.cache_data(show_spinner=False)
def resumir_demanda_calendario(
    df_base_cal: pd.DataFrame,
    ano: int,
    mes: int,
    dia_ref: int | None = None
) -> dict:
    df = _preparar_df_calendario(df_base_cal)

    primeiro_dia = date(int(ano), int(mes), 1)
    ultimo_dia = date(int(ano), int(mes), pycal.monthrange(int(ano), int(mes))[1])

    if dia_ref is None:
        dia_ref = 1

    dia_ref = max(1, min(int(dia_ref), ultimo_dia.day))
    dia_atual_ref = date(int(ano), int(mes), int(dia_ref))

    if df.empty:
        return {
            "dia_ref": dia_atual_ref,
            "qtd_patios": 0,
            "total_os": 0,
            "patio_prioritario": "-",
            "serie_total_os_mes": [0] * ultimo_dia.day,
            "labels_mes": [f"{d:02d}" for d in range(1, ultimo_dia.day + 1)]
        }

    serie_total_os_mes = []
    labels_mes = []

    for d in pd.date_range(primeiro_dia, ultimo_dia, freq="D"):
        dia = d.date()

        backlog_vencido = df[
            (df["dia_prog"] < dia) &
            (df["Status_norm"].isin(_status_aberto))
        ].copy()

        demanda_dia = df[df["dia_prog"] == dia].copy()

        total_os_dia = len(backlog_vencido) + len(demanda_dia)
        serie_total_os_mes.append(int(total_os_dia))
        labels_mes.append(d.strftime("%d"))

    backlog_ref = df[
        (df["dia_prog"] < dia_atual_ref) &
        (df["Status_norm"].isin(_status_aberto))
    ].copy()

    demanda_ref = df[df["dia_prog"] == dia_atual_ref].copy()

    patio_resumo = {}

    if not backlog_ref.empty:
        for patio, grp in backlog_ref.groupby("Patio"):
            patio_resumo[patio] = {
                "ordem": int(grp["Nivel_Prioridade"].min()),
                "qtd_os": int(len(grp)),
                "rank_status": 0
            }

    if not demanda_ref.empty:
        for patio, grp in demanda_ref.groupby("Patio"):
            todos_realizados = (~grp["Status_norm"].isin(_status_aberto)).all()
            rank_status = 2 if todos_realizados else 1

            if patio in patio_resumo:
                patio_resumo[patio]["qtd_os"] += int(len(grp))
                patio_resumo[patio]["ordem"] = min(
                    patio_resumo[patio]["ordem"],
                    int(grp["Nivel_Prioridade"].min())
                )
            else:
                patio_resumo[patio] = {
                    "ordem": int(grp["Nivel_Prioridade"].min()),
                    "qtd_os": int(len(grp)),
                    "rank_status": rank_status
                }

    qtd_patios = len(patio_resumo)
    total_os = sum(v["qtd_os"] for v in patio_resumo.values())

    if patio_resumo:
        patio_prioritario = sorted(
            patio_resumo.items(),
            key=lambda kv: (kv[1]["rank_status"], kv[1]["ordem"], kv[0])
        )[0]
        patio_prioritario_txt = f"{patio_prioritario[0]} ➔ {patio_prioritario[1]['qtd_os']} OS"
    else:
        patio_prioritario_txt = "-"

    return {
        "dia_ref": dia_atual_ref,
        "qtd_patios": int(qtd_patios),
        "total_os": int(total_os),
        "patio_prioritario": patio_prioritario_txt,
        "serie_total_os_mes": serie_total_os_mes,
        "labels_mes": labels_mes
    }

@st.cache_data(show_spinner=False)
def resumir_conclusoes_por_turno_data(
    df_base_cal: pd.DataFrame,
    data_ref
) -> dict:
    if df_base_cal.empty:
        return {
            "labels": ["00h-07h", "07h-16h", "16h-00h"],
            "valores": [0, 0, 0],
            "titulo": "Quantidade de OS Concluídas",
            "subtitulo": "Sem dados"
        }

    df = df_base_cal.copy()

    if "dt_prog_filtro" not in df.columns:
        df["dt_prog_filtro"] = pd.to_datetime(df["Data inicial programada"], errors="coerce")

    if "dt_realizado" not in df.columns:
        df["dt_realizado"] = df["Data/Hora Realizado"].apply(parse_datahora_realizado)

    if "Turno" not in df.columns:
        df["Turno"] = df["dt_realizado"].apply(classificar_turno)

    if "Status_norm" not in df.columns:
        df["Status_norm"] = df["Status da Operação"].astype(str).str.strip().str.upper()

    data_ref = pd.to_datetime(data_ref).date()
    hoje_ref = datetime.now().date()

    df_realizadas = df[df["Status_norm"].isin(_status_prazo | _status_atraso)].copy()

    if df_realizadas.empty:
        return {
            "labels": ["00h-07h", "07h-16h", "16h-00h"],
            "valores": [0, 0, 0],
            "titulo": "Quantidade de OS Concluídas",
            "subtitulo": "Sem dados"
        }

    if data_ref <= hoje_ref:
        df_ref = df_realizadas[
            pd.to_datetime(df_realizadas["dt_realizado"], errors="coerce").dt.date == data_ref
        ].copy()
        subtitulo = f"Concluídas em {data_ref.strftime('%d/%m/%Y')}"
    else:
        df_ref = df_realizadas[
            (pd.to_datetime(df_realizadas["dt_prog_filtro"], errors="coerce").dt.date == data_ref) &
            (pd.to_datetime(df_realizadas["dt_realizado"], errors="coerce").dt.date < data_ref)
        ].copy()
        subtitulo = f"Antecipadas para {data_ref.strftime('%d/%m/%Y')}"

    ordem_turnos = ["00h-07h", "07h-16h", "16h-00h"]
    serie = df_ref.groupby("Turno").size() if not df_ref.empty else pd.Series(dtype=int)
    valores = [int(serie.get(t, 0)) for t in ordem_turnos]

    return {
        "labels": ordem_turnos,
        "valores": valores,
        "titulo": "Quantidade de OS Concluídas",
        "subtitulo": subtitulo
    }

#endregion
#endregion
#endregion

#region SESSÃO 3: Banco de Coordenadas Fixo

#region SESSÃO 3.1 Coordenadas Fixas
COORDENADAS_FIXAS = {
    "FPI": [-23.444413, -46.309269],
    "IAB": [-23.521338, -46.688570],
    "ICG": [-23.767863, -46.343114],
    "ICP": [-23.658495, -46.490753],
    "ICR": [-23.640310, -46.323992],
    "IEF": [-23.477809, -46.360984],
    "IES": [-23.545441, -46.603648],
    "IIP": [-23.564977, -46.604896],
    "ILA": [-23.520217, -46.698082],
    "IMO": [-23.557803, -46.608382],
    "IOF": [-23.658579, -46.338538],
    "IPA": [-23.774399, -46.306769],
    "IPG": [-23.847950, -46.370812],
    "IPR": [-23.537749, -46.625522],
    "IRG": [-23.736705, -46.382241],
    "IRP": [-23.713578, -46.414862],
    "IRS": [-23.828162, -46.363101],
    "ISA": [-23.647553, -46.531007],
    "ISC": [-23.613874, -46.558834],
    "ISL": [-23.752383, -46.389262],
    "ISU": [-23.551210, -46.288671],
    "IUT": [-23.624864, -46.544716],
    "OAR": [-23.500419, -46.339111],
    "OBF": [-23.525591, -46.666726],
    "OBR": [-23.545397, -46.616293],
    "OCE": [-23.484980, -46.481471],
    "OCV": [-23.525061, -46.333701],
    "OEG": [-23.498082, -46.519759],
    "OET": [-23.510887, -46.552273],
    "OGP": [-23.691962, -46.448784],
    "OIC": [-23.479040, -46.367395],
    "OIT": [-23.493970, -46.401392],
    "OLU": [-23.535423, -46.634503],
    "OMA": [-23.667910, -46.462083],
    "OMP": [-23.490530, -46.443668],
    "OPS": [-23.637494, -46.537198],
    "OSU": [-23.534010, -46.308025],
    "OTA": [-23.591863, -46.590075],
    "OTT": [-23.539844, -46.575501],
    "IAA": [-23.862936, -46.398189],
    "IJN": [-23.195297, -46.870829],
    "ZPD": [-22.363436, -48.711002],

    # ✅ CORRIGIDO: PADRÃO UPPER PARA EVITAR MATCH ERROR
    "Sede IPA": [-23.767355, -46.344117],
    "Sede IPG": [-23.850772, -46.371760]
}
#endregion


#region SESSÃO 3.2 Tipo dos Nós (Semântica da Rede)
TIPO_NO = {
    "FPI": "PATIO",
    "IAB": "ESTACAO",
    "ICG": "PATIO",
    "ICP": "ESTACAO",
    "ICR": "PATIO",
    "IEF": "ESTACAO",
    "IES": "ESTACAO",
    "IIP": "PATIO",
    "ILA": "PATIO",
    "IMO": "PATIO",
    "IOF": "ESTACAO",
    "IPA": "PATIO",
    "IPG": "PATIO",
    "IPR": "ESTACAO",
    "IRG": "ESTACAO",
    "IRP": "ESTACAO",
    "IRS": "PATIO",
    "ISA": "PATIO",
    "ISC": "PATIO",
    "ISL": "PATIO",
    "ISU": "PATIO",
    "IUT": "PATIO",
    "OAR": "ESTACAO",
    "OBF": "ESTACAO",
    "OBR": "ESTACAO",
    "OCE": "ESTACAO",
    "OCV": "ESTACAO",
    "OEG": "ESTACAO",
    "OET": "ESTACAO",
    "OGP": "ESTACAO",
    "OIC": "ESTACAO",
    "OIT": "ESTACAO",
    "OLU": "ESTACAO",
    "OMA": "ESTACAO",
    "OMP": "ESTACAO",
    "OPS": "ESTACAO",
    "OSU": "ESTACAO",
    "OTA": "ESTACAO",
    "OTT": "ESTACAO",

    # ✅ CRÍTICO: ALINHAR COM COORDENADAS
    "IAA": "PATIO",
    "IJN": "ESTACAO",
    "ZPD": "ESTACAO",

    "Sede IPA": "PATIO",
    "Sede IPG": "PATIO",
}
#endregion

#region SESSÃO 3.2 Continuação do código da função de obtenção da base padrão do usuário
def obter_base_padrao_usuario():
    username = str(st.session_state.get("username", "")).strip()
    escopo = str(st.session_state.get("escopo", "")).strip()

    # ✅ NORMALIZAÇÃO CORRETA (SEM USAR "SEDE")
    mapa_normalizacao = {
        "Paranapiacaba": ("IPA", "Sede IPA"),
        "Piaçaguera": ("IPG", "Sede IPG"),
        "Todas": ("IPA", "Sede Padrão (IPA)"),

        # ✅ Nós reais do grafo
        "ICG": ("ICG", "Campo Grande (ICG)"),
        "IPA": ("IPA", "Sede IPA"),
        "IPG": ("IPG", "Base IPG"),
    }

    valor_base = None

    # ✅ BUSCA NO BANCO DE USUÁRIOS
    if username:
        try:
            conn = sqlite3.connect(db_users_path())
            cur = conn.cursor()
            cur.execute(
                "SELECT coordenacao_padrao FROM usuarios WHERE username = ?",
                (username,)
            )
            row = cur.fetchone()
            conn.close()

            if row and row[0]:
                valor_base = str(row[0]).strip()
        except Exception:
            valor_base = None

    # ✅ FALLBACK PARA ESCOPO
    if not valor_base:
        valor_base = escopo

    # ✅ GARANTE UPPER PARA EVITAR ERRO DE MATCH
    valor_base = str(valor_base).strip()
    valor_base_upper = valor_base.upper()

    # ✅ TRADUÇÃO FINAL
    if valor_base in mapa_normalizacao:
        chave_coord, nome_exibicao = mapa_normalizacao[valor_base]
    elif valor_base_upper in mapa_normalizacao:
        chave_coord, nome_exibicao = mapa_normalizacao[valor_base_upper]
    else:
        # ✅ FALLBACK SEGURO
        chave_coord, nome_exibicao = ("IPA", "Base Padrão (IPA)")

    # ✅ BUSCA COORDENADA SEGURA
    coord = COORDENADAS_FIXAS.get(chave_coord, COORDENADAS_FIXAS["IPA"])
    lat, lon = coord

    return float(lat), float(lon), nome_exibicao
#endregion
#endregion

#region SESSÃO 4: ETL (Carregamento e Tratamento)
# ==========================================

ETL_VERSION = "v6_leitura_crua_status_avancado"

def tratar_df_os(df: pd.DataFrame):
    df = normalize_cols(df)

    col_os = pick_first_existing(df, ["ORDEM SERVICO", "ORDEM SERVIÇO", "OS"])
    col_ativo = pick_first_existing(df, ["ATIVO", "EQUIPAMENTO"])
    col_atividade = pick_first_existing(df, ["ATIVIDADE ATIVO", "ATIVIDADE_ATIVO", "ATIVIDADE"])
    col_prioridade = pick_first_existing(df, ["PRIORIDADE", "CRITICIDADE"])
    col_hxh = pick_first_existing(df, ["HXH PLANO", "HXH_PLANO"])
    col_data_prog = pick_first_existing(df, ["DATA INICIAL PROGRAMADA", "DATA PROGRAMADA"])
    col_status = pick_first_existing(df, ["STATUS DA OPERAÇÃO", "STATUS", "STATUS_OPERACAO"])
    col_desc = pick_first_existing(df, ["DESCRIÇÃO LONGA", "DESCRICAO LONGA", "TEXTO LONGO"])

    missing = []
    if not col_os: missing.append("ORDEM SERVICO")
    if not col_ativo: missing.append("ATIVO")
    if not col_atividade: missing.append("ATIVIDADE ATIVO")
    if not col_prioridade: missing.append("PRIORIDADE")
    if not col_data_prog: missing.append("DATA INICIAL PROGRAMADA")
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes no Excel: {', '.join(missing)}")

    df["ATIVO_CAN"] = df[col_ativo].astype(str).str.strip()
    df["ATIVIDADE_CAN"] = df[col_atividade].astype(str).str.strip()
    df["PRIORIDADE_CAN"] = df[col_prioridade].astype(str).str.strip()
    df["HXH_CAN"] = pd.to_numeric(df[col_hxh], errors="coerce").fillna(0) if col_hxh else 0.0
    
    df["PATIO_CAN"] = df["ATIVO_CAN"].str[:3].str.upper()

    df["DATA_PROG_CAN"] = df[col_data_prog].apply(parse_data_programada)
    df["DESC_LONGA_CAN"] = df[col_desc].astype(str).str.strip() if col_desc else ""

    df["Classificacao"] = df["ATIVIDADE_CAN"].apply(classificar_atividade)
    crit = df["PRIORIDADE_CAN"].apply(extrair_criticidade)
    df["Criticidade_rank"] = [c[0] for c in crit]
    df["Criticidade"] = [c[1] for c in crit]
    df["Nivel_Prioridade"] = df.apply(lambda r: calcular_nivel_prioridade(r["Classificacao"], r["Criticidade_rank"]), axis=1)

    hoje_data = datetime.now().date()
    def definir_status_cru(row):
        st_atual = str(row[col_status]).strip().upper() if pd.notna(row[col_status]) and col_status else ""
        
        if "REALIZADO" in st_atual:
            if "FORA" in st_atual or "ATRASO" in st_atual:
                return "Realizado Fora da Data de Programação"
            return "Realizado"
        
        dp = row["DATA_PROG_CAN"]
        if pd.isna(dp):
            return "Pendente"
        
        if dp.date() >= hoje_data:
            return "Pendente"
        else:
            return "Atrasado"

    df["STATUS_CAN"] = df.apply(definir_status_cru, axis=1)

    df_out = pd.DataFrame({
        "Ordem servico": df[col_os].astype(str).str.strip(),
        "Patio": df["PATIO_CAN"],
        "Ativo": df["ATIVO_CAN"],
        "Criticidade": df["Criticidade"],
        "Classificacao": df["Classificacao"],
        "Descrição Longa": df["DESC_LONGA_CAN"],
        "Data inicial programada": df["DATA_PROG_CAN"],
        "Status da Operação": df["STATUS_CAN"],
        "Data/Hora Realizado": "",
        "Concluído por": "",  
        "Hxh Plano": df["HXH_CAN"],
        "Criticidade_rank": df["Criticidade_rank"],
        "Nivel_Prioridade": df["Nivel_Prioridade"],
    })

    return df_out

@st.cache_data
def auto_detect_and_treat(path_ou_bytes):
    if isinstance(path_ou_bytes, bytes):
        df_raw = pd.read_excel(io.BytesIO(path_ou_bytes), engine="openpyxl", header=None)
    else:
        df_raw = pd.read_excel(path_ou_bytes, engine="openpyxl", header=None)
        
    df_raw = df_raw.dropna(how='all')
    df_raw = df_raw.dropna(axis=1, how='all')
    
    if df_raw.empty:
        raise ValueError("O arquivo Excel está completamente sem dados.")
        
    # 1. Resetar o index para garantir que a linha 0 seja o cabeçalho
    df_raw = df_raw.reset_index(drop=True)
    
    # 2. Definir o cabeçalho
    df_raw.columns = df_raw.iloc[0].values
    
    # 3. Separar os dados e resetar
    df_tratado = df_raw.iloc[1:].reset_index(drop=True)
    
    # 4. CORREÇÃO DEFINITIVA (Bug KeyError do Pandas)
    # Garante que não existam colunas com nomes duplicados, o que corrompe
    # o sistema de busca do Pandas e causa o KeyError invisível.
    if df_tratado.columns.duplicated().any():
        colunas_unicas = []
        vistos = set()
        for col in df_tratado.columns:
            nome = str(col)
            novo_nome = nome
            contador = 1
            while novo_nome in vistos:
                novo_nome = f"{nome}_{contador}"
                contador += 1
            vistos.add(novo_nome)
            colunas_unicas.append(novo_nome)
        df_tratado.columns = colunas_unicas
        
    return tratar_df_os(df_tratado)

@st.cache_data
def carregar_excel_por_bytes(excel_bytes: bytes, etl_version: str):
    return auto_detect_and_treat(excel_bytes)

@st.cache_data
def carregar_excel_por_path(path_excel: str, etl_version: str):
    return auto_detect_and_treat(path_excel)

@st.cache_data(show_spinner=False)
def carregar_base_sem_overlay(
    usar_sim: bool,
    qtd_sim: int,
    seed_sim: int,
    escopo_usuario: str,
    etl_version: str,
    pct_pendente_sim: int,
    pesos_criticidade_sim: tuple,
) -> pd.DataFrame:
    if usar_sim:
        pesos_dict = {
            "1-Muito Alta": pesos_criticidade_sim[0],
            "2-Alta": pesos_criticidade_sim[1],
            "3-Média": pesos_criticidade_sim[2],
            "4-Baixa": pesos_criticidade_sim[3],
        }
        return gerar_base_simulada(
            qtd=qtd_sim,
            seed=seed_sim,
            pct_pendente=pct_pendente_sim,
            pesos_criticidade=pesos_dict,
        )

    pasta_bases = Path("bases_os")
    pasta_bases.mkdir(exist_ok=True)

    arquivos = [f for f in pasta_bases.glob("*.xlsx") if not f.name.startswith("~$")]
    if not arquivos:
        return pd.DataFrame()

    dfs = []
    for arq in arquivos:
        df_temp = carregar_excel_por_path(str(arq), etl_version)
        nome_coord = arq.stem.replace("OS_", "").replace("_", " ").strip()
        df_temp["Coordenacao"] = nome_coord
        dfs.append(df_temp)

    df_base_bruto = pd.concat(dfs, ignore_index=True)

    if escopo_usuario != "Todas":
        df_base_bruto = df_base_bruto[
            df_base_bruto["Coordenacao"].str.contains(escopo_usuario, case=False, na=False)
        ]

    return df_base_bruto


@st.cache_data(show_spinner=False)
def aplicar_overlay_baixas(
    df_base_bruto: pd.DataFrame,
    escopo_usuario: str,
    baixas_mtime: float
) -> pd.DataFrame:
    df_base = df_base_bruto.copy()

    if df_base.empty:
        return df_base

    init_db()
    df_baixas = carregar_baixas_df()

    if df_baixas.empty:
        return df_base

    df_base["Ordem servico"] = df_base["Ordem servico"].astype(str)

    if escopo_usuario != "Todas":
        df_baixas = df_baixas[
            df_baixas["coordenacao"].str.contains(escopo_usuario, case=False, na=False)
        ]

    for col in ["Status da Operação", "Data/Hora Realizado", "Concluído por"]:
        if col not in df_base.columns:
            df_base[col] = ""

    df_baixas = df_baixas.rename(columns={
        "os": "Ordem servico",
        "status": "Status da Operação",
        "realizado_em": "Data/Hora Realizado",
        "concluido_por": "Concluído por"
    })

    df_base = df_base.merge(
        df_baixas[["Ordem servico", "Status da Operação", "Data/Hora Realizado", "Concluído por"]],
        on="Ordem servico",
        how="left",
        suffixes=("", "_baixado")
    )

    df_base["Status da Operação"] = np.where(
        df_base["Status da Operação_baixado"].notna(),
        df_base["Status da Operação_baixado"],
        df_base["Status da Operação"]
    )

    df_base["Data/Hora Realizado"] = np.where(
        df_base["Data/Hora Realizado_baixado"].notna(),
        df_base["Data/Hora Realizado_baixado"],
        df_base["Data/Hora Realizado"]
    )

    df_base["Concluído por"] = np.where(
        df_base["Concluído por_baixado"].notna(),
        df_base["Concluído por_baixado"],
        df_base["Concluído por"]
    )

    df_base.drop(
        columns=[
            "Status da Operação_baixado",
            "Data/Hora Realizado_baixado",
            "Concluído por_baixado"
        ],
        inplace=True
    )

    return df_base
#endregion

#region SESSÃO EXTRA: Simulação de dados (APENAS TESTE - remover depois)
# ==========================================
# SESSÃO EXTRA: Simulação de dados (APENAS TESTE - remover depois)
# ==========================================

#region SESSÃO EXTRA: Gerador de base simulada (para testar KPIs e gráficos)
def gerar_base_simulada(
    qtd: int = 800,
    seed: int = 42,
    pct_pendente: int = 45,
    pesos_criticidade: dict | None = None,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    patios = [
        "IAA", "IEF", "OLU", "IPA", "IRS", "IPG", "ICG", "IRG",
        "IOF", "ISU", "ILA", "IJN", "ZPD", "IIP"
    ]

    # --- Criticidade configurável ---
    prioridades = ["1-Muito Alta", "2-Alta", "3-Média", "4-Baixa"]
    if pesos_criticidade is None:
        pesos_criticidade = {
            "1-Muito Alta": 18,
            "2-Alta": 32,
            "3-Média": 30,
            "4-Baixa": 20,
        }

    soma_pesos = sum(max(float(v), 0.0) for v in pesos_criticidade.values())
    if soma_pesos <= 0:
        prob_prio = [0.25, 0.25, 0.25, 0.25]
    else:
        prob_prio = [
            max(float(pesos_criticidade.get(p, 0.0)), 0.0) / soma_pesos
            for p in prioridades
        ]

    atividades = [
        "EE_INS_SEG_C_I_MAQ CHAVE MOLA_1800",
        "EE_MAN_CONF_C_I_CANALETA SUBESTACAO_0720",
        "EE_INS_CONF_S_I_BATERIAS_0360",
    ]
    prob_ativ = [0.35, 0.30, 0.35]

    # --- Status configurável ---
    pct_pendente = int(max(0, min(100, pct_pendente)))
    pct_restante = 100 - pct_pendente
    pct_realizado_prazo = round(pct_restante * 0.75)
    pct_realizado_atraso = pct_restante - pct_realizado_prazo

    status_list = [
        "Não Realizado",
        "Realizado",
        "Realizado Fora da Data de Programação",
    ]
    prob_status = [
        pct_pendente / 100,
        pct_realizado_prazo / 100,
        pct_realizado_atraso / 100,
    ]

    hoje = datetime.now()
    dias_atras = rng.integers(0, 30, size=qtd)
    data_prog = [hoje - pd.Timedelta(days=int(d)) for d in dias_atras]
    data_prog = pd.to_datetime(data_prog).normalize()

    df = pd.DataFrame({
        "Ordem servico": [f"OS-{100000+i}" for i in range(qtd)],
        "Patio": rng.choice(patios, size=qtd),
        "Ativo": [f"{rng.choice(patios)}-ATV-{i:04d}" for i in range(qtd)],
        "Atividade ativo": rng.choice(atividades, size=qtd, p=prob_ativ),
        "Prioridade": rng.choice(prioridades, size=qtd, p=prob_prio),
        "Hxh Plano": np.round(rng.uniform(0.5, 8.0, size=qtd), 1),
        "Data inicial programada": data_prog,
        "Coordenacao": rng.choice(["Paranapiacaba", "Piaçaguera"], size=qtd),
    })

    df["Classificacao"] = df["Atividade ativo"].apply(classificar_atividade)
    crit = df["Prioridade"].apply(extrair_criticidade)
    df["Criticidade_rank"] = [c[0] for c in crit]
    df["Criticidade"] = [c[1] for c in crit]
    df["Nivel_Prioridade"] = df.apply(
        lambda r: calcular_nivel_prioridade(r["Classificacao"], r["Criticidade_rank"]),
        axis=1,
    )

    df["Desc_Prioridade"] = df["Classificacao"] + " | " + df["Criticidade"]
    df["Status da Operação"] = rng.choice(status_list, size=qtd, p=prob_status)
    df["Data/Hora Realizado"] = ""

    for i in range(qtd):
        stt = df.at[i, "Status da Operação"]
        if stt == "Não Realizado":
            continue

        prog = pd.to_datetime(df.at[i, "Data inicial programada"])
        turno = rng.choice(["00h-07h", "07h-16h", "16h-00h"], p=[0.15, 0.60, 0.25])

        if turno == "00h-07h":
            hh = int(rng.integers(0, 7))
        elif turno == "07h-16h":
            hh = int(rng.integers(7, 16))
        else:
            hh = int(rng.integers(16, 24))

        mm = int(rng.integers(0, 60))

        if stt == "Realizado":
            delta = int(rng.integers(0, 4))
            real_date = (prog - pd.Timedelta(days=delta)).to_pydatetime()
        else:
            delta = int(rng.integers(1, 11))
            real_date = (prog + pd.Timedelta(days=delta)).to_pydatetime()

        real_dt = real_date.replace(hour=hh, minute=mm, second=0, microsecond=0)
        df.at[i, "Data/Hora Realizado"] = formatar_dt_br(real_dt)

    return df
#endregion

#region SESSÃO EXTRA: Controle na Sidebar
def simulacao_sidebar():
    st.sidebar.header("🧪 Simulação (Teste)")
    usar_sim = st.sidebar.checkbox("Usar dados simulados (teste KPIs)", value=False)

    if not usar_sim:
        return False, None

    qtd_sim = st.sidebar.slider("Quantidade de OS simuladas", 100, 4000, 1200, 100)
    seed_sim = st.sidebar.number_input("Seed (repete os mesmos dados)", min_value=1, max_value=999999, value=42, step=1)

    df_sim = gerar_base_simulada(qtd=int(qtd_sim), seed=int(seed_sim))
    st.sidebar.info("✅ Simulação ativa. Excel real NÃO será carregado.")
    return True, df_sim
#endregion
#endregion

#region SESSÃO 5: Sidebar, Navegação, Carga e Filtro

#region SESSÃO 5.1: Identidade visual, navegação e escopo
# 5.1.1 CSS / identidade visual
st.markdown("""
    <style>
    [data-testid="stSidebar"] {
        background-color: #1A202C !important; 
    }
    
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, 
    [data-testid="stSidebar"] h4, [data-testid="stSidebar"] h5, [data-testid="stSidebar"] h6,
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] small, [data-testid="stSidebar"] caption {
        color: #F1F5F9 !important;
    }

    [data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child {
        display: none !important;
    }
    
    [data-testid="stSidebar"] div[role="radiogroup"] > label {
        padding: 10px 16px !important;
        background-color: transparent !important;
        border-radius: 8px !important;
        margin-bottom: 6px !important;
        transition: all 0.2s ease-in-out !important;
        cursor: pointer !important;
        color: #CBD5E1 !important;
    }
    
    [data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
        background-color: rgba(255, 255, 255, 0.08) !important;
        color: #FFFFFF !important;
    }
    
    [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
        background-color: rgba(255, 75, 75, 0.2) !important; 
        border-left: 4px solid #FF4B4B !important;
    }
    [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p {
        font-weight: bold !important;
        color: #FFFFFF !important;
    }
    
    [data-testid="stSidebar"] .stSelectbox label p, 
    [data-testid="stSidebar"] .stMultiSelect label p,
    [data-testid="stSidebar"] .stDateInput label p {
        font-size: 16px !important;
        font-weight: 700 !important;
        color: #F8FAFC !important;
        margin-bottom: 4px;
    }

    .stMultiSelect [data-baseweb="tag"] {
        background-color: #FF4B4B !important;
        color: white !important;
        border-radius: 6px !important;
    }
    
    [data-testid="stSidebar"] div[data-baseweb="select"] > div,
    [data-testid="stSidebar"] div[data-baseweb="input"] > div,
    [data-testid="stSidebar"] div[data-baseweb="base-input"] > input {
        background-color: #333D4E !important;
        border-color: #475569 !important;
        border-radius: 6px !important;
        color: white !important;
    }
    [data-testid="stSidebar"] div[data-baseweb="select"] span,
    [data-testid="stSidebar"] div[data-baseweb="input"] input {
        color: white !important;
    }
    
    [data-testid="stSidebar"] [data-testid="stExpander"] details {
        border: 1px solid #FF4B4B !important;
        border-radius: 8px !important;
        overflow: hidden;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary {
        background-color: #FF4B4B !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary p {
        color: #FFFFFF !important;
        font-weight: 800 !important;
        font-size: 16px !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] svg {
        fill: #FFFFFF !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
        background-color: #1A202C !important;
        padding-top: 15px !important;
    }
    
    [data-testid="stSidebar"] button {
        background-color: #333D4E !important;
        color: #FFFFFF !important;
        border: 1px solid #475569 !important;
        border-radius: 6px !important;
        transition: all 0.2s ease-in-out;
    }
    [data-testid="stSidebar"] button:hover {
        background-color: #475569 !important;
        border-color: #cbd5e1 !important;
        color: #FFFFFF !important;
    }
    
    [data-testid="stMetricValue"] {
        font-size: 28px !important;
    }
    
    button[data-baseweb="tab"][aria-selected="true"] {
        background-color: rgba(255, 75, 75, 0.15) !important;
        border-radius: 6px 6px 0px 0px !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] p {
        font-weight: bold !important;
    }
    button[data-baseweb="tab"]:hover {
        background-color: rgba(255, 75, 75, 0.05) !important;
        border-radius: 6px 6px 0px 0px !important;
    }
    </style>
""", unsafe_allow_html=True)

# 5.1.2 Logotipo
st.sidebar.image("logo_mrs.png", use_container_width=True)
st.sidebar.markdown("<br>", unsafe_allow_html=True)

# 5.1.3 Navegação e definição do escopo visual
st.sidebar.markdown("### 🧭 Navegação")
if st.session_state["perfil"] == "Gerência":
    visao_selecionada = st.sidebar.radio("Selecione a Visão:", ["Gerência", "Paranapiacaba", "Piaçaguera"], label_visibility="collapsed")
    filtro_visao = "Todas" if visao_selecionada == "Gerência" else visao_selecionada
else:
    filtro_visao = st.session_state["escopo"]
    st.sidebar.info(f"Visão Restrita: {filtro_visao}")
#endregion

#region SESSÃO 5.2: Carregamento da base operacional
usar_sim = st.session_state.get("chk_sim", False)
qtd_sim = st.session_state.get("qtd_sim", 1200)
seed_sim = st.session_state.get("seed_sim", 42)

baixas_mtime = os.path.getmtime(db_path()) if os.path.exists(db_path()) else 0.0

df_base_bruto = carregar_base_sem_overlay(
    usar_sim=usar_sim,
    qtd_sim=int(qtd_sim),
    seed_sim=int(seed_sim),
    escopo_usuario=st.session_state["escopo"],
    etl_version=ETL_VERSION,
    pct_pendente_sim=int(st.session_state.get("sim_pct_pendente", 45)),
    pesos_criticidade_sim=(
        int(st.session_state.get("sim_pct_crit_1", 18)),
        int(st.session_state.get("sim_pct_crit_2", 32)),
        int(st.session_state.get("sim_pct_crit_3", 30)),
        int(st.session_state.get("sim_pct_crit_4", 20)),
    ),
)

if df_base_bruto.empty and not usar_sim:
    pasta_bases = Path("bases_os")
    st.error(f"Nenhuma planilha encontrada na pasta '{pasta_bases.absolute()}'.")
    st.stop()

df_base = aplicar_overlay_baixas(
    df_base_bruto=df_base_bruto,
    escopo_usuario=st.session_state["escopo"],
    baixas_mtime=baixas_mtime
)

st.session_state["df_os"] = df_base
df_visao = preparar_df_visao(df_base, filtro_visao)
#endregion

#region SESSÃO 5.3: Filtros da sidebar
st.sidebar.markdown("### 📊 Filtros")

valid_dates = df_visao["dt_prog_filtro"].dropna()

if not valid_dates.empty:
    min_date = valid_dates.min().date()
    max_date = valid_dates.max().date()
else:
    min_date = datetime.now().date() - pd.Timedelta(days=30)
    max_date = datetime.now().date()

if st.session_state["perfil"] != "Técnico":
    data_selecionada = st.sidebar.date_input(
        "Período de Programação",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date
    )

    if isinstance(data_selecionada, tuple):
        if len(data_selecionada) == 2:
            start_date, end_date = data_selecionada
        else:
            start_date = data_selecionada[0]
            end_date = data_selecionada[0]
    else:
        start_date = data_selecionada
        end_date = data_selecionada

    lista_patios = sorted(df_visao["Patio"].dropna().astype(str).unique().tolist())
    patios_selecionados = st.sidebar.multiselect("Pátio", lista_patios, default=lista_patios)

    classif_selecionadas = st.sidebar.multiselect(
        "Classificação",
        ["Confiabilidade e Segurança", "Segurança", "Confiabilidade"],
        default=["Confiabilidade e Segurança", "Segurança", "Confiabilidade"]
    )

    lista_turnos = ["00h-07h", "07h-16h", "16h-00h", "Pendente (Sem Turno)"]
    turnos_selecionados = st.sidebar.multiselect("Turno", lista_turnos, default=lista_turnos)

    status_sel = st.sidebar.selectbox(
        "Status da OS",
        ["Todos", "Todas Concluídas", "Concluídas no Prazo", "Concluídas com Atraso", "Pendentes"]
    )
else:
    st.sidebar.info("💡 Filtros automáticos aplicados de acordo com o seu escopo operacional de campo.")
    start_date = min_date
    end_date = max_date
    patios_selecionados = sorted(df_visao["Patio"].dropna().astype(str).unique().tolist())
    classif_selecionadas = ["Confiabilidade e Segurança", "Segurança", "Confiabilidade"]
    turnos_selecionados = ["00h-07h", "07h-16h", "16h-00h", "Pendente (Sem Turno)"]
    status_sel = "Todos"

df_filtrado = aplicar_filtros_sidebar(
    df_visao=df_visao,
    patios_selecionados=patios_selecionados,
    classif_selecionadas=classif_selecionadas,
    turnos_selecionados=turnos_selecionados,
    start_date=start_date,
    end_date=end_date,
    status_sel=status_sel
)
#endregion
#endregion

#region SESSÃO 6: Sistema, dados e gestão de usuários
if st.session_state["perfil"] == "Gerência":
    with st.sidebar.expander("⚙️ Sistema, Dados e Gestão", expanded=False):
        # =====================================================
        # 6.1 — SIMULAÇÃO DE DADOS OPERACIONAIS (OS)
        # =====================================================
        st.markdown("### 🧪 Simulação de OS")
        st.checkbox("Usar dados simulados (teste rápido)", key="chk_sim")

        if st.session_state.get("chk_sim"):
            st.slider(
                "Volume de OS simuladas",
                min_value=100,
                max_value=4000,
                value=1200,
                step=100,
                key="qtd_sim"
            )
            st.number_input(
                "Seed (repete mesmos dados)",
                min_value=1,
                max_value=999999,
                value=42,
                step=1,
                key="seed_sim"
            )

            st.slider(
                "% de OS Pendentes",
                min_value=0,
                max_value=100,
                value=45,
                step=5,
                key="sim_pct_pendente"
            )

            st.markdown("**% de Criticidade (base de distribuição)**")
            st.slider("1 - Muito Alta", 0, 100, 18, 1, key="sim_pct_crit_1")
            st.slider("2 - Alta",        0, 100, 32, 1, key="sim_pct_crit_2")
            st.slider("3 - Média",       0, 100, 30, 1, key="sim_pct_crit_3")
            st.slider("4 - Baixa",       0, 100, 20, 1, key="sim_pct_crit_4")

            soma_crit = (
                st.session_state.get("sim_pct_crit_1", 18)
                + st.session_state.get("sim_pct_crit_2", 32)
                + st.session_state.get("sim_pct_crit_3", 30)
                + st.session_state.get("sim_pct_crit_4", 20)
            )
            st.caption(f"Soma atual da criticidade: {soma_crit}% (o sistema normaliza automaticamente)")
        else:
            if st.button("🔄 Recarregar dados (ETL)", use_container_width=True, key="btn_recarregar_etl"):
                st.cache_data.clear()
                st.rerun()

        # =====================================================
        # 6.2 — SIMULAÇÃO DA MALHA (GÊMEO DIGITAL)
        # =====================================================
        st.markdown("### 🕸️ Simulação da Malha")

        st.toggle(
            "Ativar Gêmeo Digital: bloquear rede por OS Crítica Pendente",
            value=False,
            key="sim_ativar_gemeo_digital",
            on_change=manter_aba_3
        )

        lista_nos_sidebar = sorted(list(G.nodes()))

        st.selectbox(
            "Simular falha manual no pátio",
            [""] + lista_nos_sidebar,
            key="sim_patio_bloqueado_sidebar",
            on_change=manter_aba_3
        )

        col_sb1, col_sb2 = st.columns(2)
        with col_sb1:
            st.button(
                "Aplicar Falha",
                use_container_width=True,
                key="btn_aplicar_falha_sidebar",
                on_click=aplicar_falha_sidebar
            )

        with col_sb2:
            st.button(
                "Restaurar Malha",
                use_container_width=True,
                key="btn_restaurar_malha_sidebar",
                on_click=restaurar_malha_sidebar
            )

        patio_manual_atual = st.session_state.get("patio_bloqueado_manual")
        if patio_manual_atual:
            st.warning(f"Falha manual ativa em: {patio_manual_atual}")
        else:
            st.caption("Nenhuma falha manual ativa na malha.")

        # =====================================================
        # 6.3 — GESTÃO DE USUÁRIOS
        # =====================================================
        st.markdown(
            "<h4 style='margin-bottom:0.4rem;'>Gestão de Usuários</h4>",
            unsafe_allow_html=True
        )

        if "msg_sucesso_user" in st.session_state:
            st.success(st.session_state["msg_sucesso_user"])
            del st.session_state["msg_sucesso_user"]

        def sedes_por_escopo(escopo: str):
            escopo = str(escopo).strip()
            if escopo == "Paranapiacaba":
                return ["Sede IPA"]
            elif escopo == "Piaçaguera":
                return ["Sede IPG"]
            elif escopo == "Todas":
                return ["Sede IPA", "Sede IPG"]
            return ["Sede IPA"]

        with st.form("form_novo_user", clear_on_submit=True):
            n_user = st.text_input("Login (Nova conta)", key="novo_user_login")
            n_perf = st.selectbox("Perfil", ["Técnico", "Coordenador", "Gerência"], key="novo_user_perfil")
            n_esco = st.selectbox("Escopo", ["Paranapiacaba", "Piaçaguera", "Todas"], key="novo_user_escopo")

            sedes_validas = sedes_por_escopo(n_esco)
            sede_default = {
                "Paranapiacaba": "Sede IPA",
                "Piaçaguera": "Sede IPG",
                "Todas": "Sede IPA"
            }.get(n_esco, "Sede IPA")
            idx_sede_default = sedes_validas.index(sede_default) if sede_default in sedes_validas else 0

            n_sede = st.selectbox(
                "Sede",
                sedes_validas,
                index=idx_sede_default,
                key="novo_user_sede",
                format_func=lambda x: x.replace("Sede ", "")
            )
            st.caption("A senha inicial padrão será definida automaticamente como **mrs123**.")

            if st.form_submit_button("Salvar Novo Usuário"):
                if n_user:
                    conn = sqlite3.connect(db_users_path())
                    try:
                        conn.cursor().execute(
                            """
                            INSERT INTO usuarios (username, senha_hash, perfil, escopo, palavra_recuperacao, dica_recuperacao, coordenacao_padrao, reset_obrigatorio)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (n_user.strip(), hash_senha("mrs123"), n_perf, n_esco, "PENDENTE", "PENDENTE", n_sede, 1)
                        )
                        conn.commit()
                        conn.close()
                        st.session_state["msg_sucesso_user"] = f"Usuário '{n_user}' criado com sucesso!"
                        st.rerun()
                    except sqlite3.IntegrityError:
                        conn.close()
                        st.error("Erro: Este usuário já existe no sistema.")
                else:
                    st.warning("Preencha o login do usuário.")

        st.markdown("**👥 Gerenciar Usuários**", unsafe_allow_html=True)
        conn = sqlite3.connect(db_users_path())
        df_usuarios = pd.read_sql_query("SELECT username, perfil, escopo, coordenacao_padrao FROM usuarios", conn)
        conn.close()

        lista_users = df_usuarios["username"].tolist()
        usr_sel = st.selectbox("Selecione um usuário para gerenciar:", [""] + lista_users, key="gerenciar_usuario_select")

        if usr_sel != "":
            dados_usr = df_usuarios[df_usuarios["username"] == usr_sel].iloc[0]
            st.caption(
                f"**Perfil Atual:** {dados_usr['perfil']} | "
                f"**Visão:** {dados_usr['escopo']} | "
                f"**Sede Atual:** {str(dados_usr['coordenacao_padrao']).replace('Sede ', '')}"
            )

            acao = st.radio(
                "Escolha a ação:",
                ["✏️ Editar Acesso", "🔑 Resetar Senha", "🗑️ Excluir"],
                horizontal=True,
                key=f"acao_usuario_{usr_sel}"
            )

            if acao == "✏️ Editar Acesso":
                with st.form(f"form_edit_{usr_sel}"):
                    perfis_validos = ["Técnico", "Coordenador", "Gerência"]
                    escopos_validos = ["Paranapiacaba", "Piaçaguera", "Todas"]

                    idx_perf = perfis_validos.index(dados_usr["perfil"]) if dados_usr["perfil"] in perfis_validos else 0
                    idx_esco = escopos_validos.index(dados_usr["escopo"]) if dados_usr["escopo"] in escopos_validos else 0

                    n_perf_edit = st.selectbox("Novo Perfil", perfis_validos, index=idx_perf, key=f"edit_perf_{usr_sel}")
                    n_esco_edit = st.selectbox("Nova Visão", escopos_validos, index=idx_esco, key=f"edit_escopo_{usr_sel}")

                    sedes_validas_edit = sedes_por_escopo(n_esco_edit)
                    sede_atual = str(dados_usr["coordenacao_padrao"]).strip() if pd.notna(dados_usr["coordenacao_padrao"]) else "Sede IPA"
                    idx_sede = sedes_validas_edit.index(sede_atual) if sede_atual in sedes_validas_edit else 0

                    n_sede_edit = st.selectbox(
                        "Sede",
                        sedes_validas_edit,
                        index=idx_sede,
                        key=f"edit_sede_{usr_sel}",
                        format_func=lambda x: x.replace("Sede ", "")
                    )

                    if st.form_submit_button("Salvar Alterações"):
                        conn = sqlite3.connect(db_users_path())
                        conn.cursor().execute(
                            "UPDATE usuarios SET perfil = ?, escopo = ?, coordenacao_padrao = ? WHERE username = ?",
                            (n_perf_edit, n_esco_edit, n_sede_edit, usr_sel)
                        )
                        conn.commit()
                        conn.close()
                        st.session_state["msg_sucesso_user"] = f"Permissões de {usr_sel} atualizadas!"
                        st.rerun()

            elif acao == "🔑 Resetar Senha":
                st.warning("A senha voltará para 'mrs123' e o usuário será forçado a criar uma nova.")
                if st.button("Confirmar Reset", key=f"btn_reset_{usr_sel}"):
                    conn = sqlite3.connect(db_users_path())
                    conn.cursor().execute(
                        "UPDATE usuarios SET senha_hash = ?, reset_obrigatorio = 1 WHERE username = ?",
                        (hash_senha("mrs123"), usr_sel)
                    )
                    conn.commit()
                    conn.close()
                    st.session_state["msg_sucesso_user"] = f"Senha de {usr_sel} resetada com sucesso!"
                    st.rerun()

            elif acao == "🗑️ Excluir":
                if usr_sel == st.session_state["username"]:
                    st.error("Você não pode excluir a si mesmo para evitar bloqueio do sistema.")
                else:
                    st.warning("O acesso será removido. O histórico de OS continuará intacto.")
                    if st.button("Confirmar Exclusão", key=f"btn_del_{usr_sel}", type="primary"):
                        conn = sqlite3.connect(db_users_path())
                        conn.cursor().execute("DELETE FROM usuarios WHERE username = ?", (usr_sel,))
                        conn.commit()
                        conn.close()
                        st.session_state["msg_sucesso_user"] = f"Usuário {usr_sel} excluído permanentemente."
                        st.rerun()
#endregion

#region SESSÃO 7: DASHBOARD HEADER E KPI METRICS
col_titulo, col_acoes = st.columns([9, 1])

with col_titulo:
    st.title("⚡ Sistema de Gestão de Ordens de Serviço")
    st.markdown(f"<h5 style='color: #475569; margin-top: -10px;'>Olá, <b>{st.session_state.get('username', 'Usuário')}</b> 👋</h5>", unsafe_allow_html=True)

with col_acoes:
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    if st.button("🔄 Atualizar", use_container_width=True):
        st.rerun()
    if st.button("🔑 Trocar", use_container_width=True):
        usr_atual = st.session_state["username"]
        conn = sqlite3.connect(db_users_path())
        conn.cursor().execute("UPDATE usuarios SET reset_obrigatorio = 1 WHERE username = ?", (usr_atual,))
        conn.commit(); conn.close()
        st.session_state.clear()
        st.session_state["logged_in"] = False
        st.session_state["needs_reset"] = True
        st.session_state["reset_user"] = usr_atual
        st.rerun()
    if st.button("🚪 Sair", use_container_width=True):
        st.session_state.clear() 
        st.session_state["logged_in"] = False
        st.rerun()

st.markdown("---")

# CÁLCULO DOS KPIS PARA A SESSÃO 7
total_os = len(df_filtrado)
realizado_prazo = len(df_filtrado[df_filtrado["Status_norm"].isin(_status_prazo)])
realizado_atraso = len(df_filtrado[df_filtrado["Status_norm"].isin(_status_atraso)])
realizado_total = realizado_prazo + realizado_atraso
nao_realizado = len(df_filtrado[df_filtrado["Status_norm"].isin(_status_aberto)])
taxa_conclusao = (realizado_total / total_os * 100) if total_os > 0 else 0.0

st.markdown("""
    <style>
    iframe, .stEcharts, [data-testid="stHtmlBlock"] + div iframe {
        border-radius: 12px !important;
        overflow: hidden !important;
    }
    .kpi-header-wrapper { font-family: "Source Sans Pro", sans-serif; }
    .kpi-header-card {
        font-family: "Source Sans Pro", sans-serif;
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 4px 6px rgba(15, 23, 42, 0.08);
        height: 140px; 
        display: flex;
        flex-direction: column;
        justify-content: center;
        box-sizing: border-box;
        margin-bottom: 15px;
    }
    .kpi-border-gray { border-left: 5px solid #64748B; background: linear-gradient(135deg, #F8FAFC 0%, #F1F5F9 100%); }
    .kpi-border-red { border-left: 5px solid #FF4B4B; background: linear-gradient(135deg, #FEF2F2 0%, #FEE2E2 100%); }
    .kpi-border-green { border-left: 5px solid #10B981; background: linear-gradient(135deg, #F0FDF4 0%, #D1FAE5 100%); }
    .kpi-border-blue { border-left: 5px solid #3B82F6; background: linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%); }
    .kpi-header-title { font-size: 14px; font-weight: 700; color: #1E293B; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
    .kpi-header-val { font-size: 32px; font-weight: 400; color: #0F172A; line-height: 1; }
    .kpi-header-sub { font-size: 12px; font-weight: 400; margin-top: 8px; padding: 4px 10px; border-radius: 20px; display: inline-block; width: fit-content; }
    .badge-gray { background-color: #E2E8F0; color: #475569; }
    .badge-red { background-color: #FECACA; color: #991B1B; }
    .badge-green { background-color: #A7F3D0; color: #065F46; }
    .badge-blue { background-color: #DBEAFE; color: #1E40AF; }
    </style>
""", unsafe_allow_html=True)

col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)

with col_kpi1:
    st.markdown(f"""
        <div class="kpi-header-wrapper kpi-header-card kpi-border-gray">
            <div class="kpi-header-title">📋 Planejado (OS)</div>
            <div class="kpi-header-val">{total_os}</div>
            <div class="kpi-header-sub badge-gray">Total de O.S do período</div>
        </div>
    """, unsafe_allow_html=True)

with col_kpi2:
    st.markdown(f"""
        <div class="kpi-header-wrapper kpi-header-card kpi-border-red">
            <div class="kpi-header-title">🔴 Backlog (Não Realizado)</div>
            <div class="kpi-header-val">{nao_realizado}</div>
            <div class="kpi-header-sub badge-red">↑ {nao_realizado} pendentes</div>
        </div>
    """, unsafe_allow_html=True)

with col_kpi3:
    st.markdown(f"""
        <div class="kpi-header-wrapper kpi-header-card kpi-border-green">
            <div class="kpi-header-title">🟢 Realizado (Total)</div>
            <div class="kpi-header-val">{realizado_total}</div>
            <div class="kpi-header-sub badge-green">↑ {realizado_prazo} no prazo / {realizado_atraso} atrasado</div>
        </div>
    """, unsafe_allow_html=True)

with col_kpi4:
    st.markdown(f"""
        <div class="kpi-header-wrapper kpi-header-card kpi-border-blue">
            <div class="kpi-header-title">📈 Taxa de Conclusão</div>
            <div class="kpi-header-val">{taxa_conclusao:.1f}%</div>
            <div class="kpi-header-sub badge-blue">Aproveitamento geral</div>
        </div>
    """, unsafe_allow_html=True)

st.markdown("---")
#endregion

#region SESSÃO 8: Abas e Renderização dos Gráficos

#region SESSÃO 8.1: Abas ativas
OPCOES_ABA_SISTEMA = [
    "📊 Visão Gerencial (Indicadores)",
    "🗺️ Roteirização e Mapa de Campo",
    "🕸️ Inteligência da Malha",
]

if "aba_ativa_sistema" not in st.session_state:
    st.session_state["aba_ativa_sistema"] = "📊 Visão Gerencial (Indicadores)"

if "_aba_ativa_widget" not in st.session_state:
    st.session_state["_aba_ativa_widget"] = st.session_state["aba_ativa_sistema"]

if "_aba_ativa_widget_prev" not in st.session_state:
    st.session_state["_aba_ativa_widget_prev"] = st.session_state["_aba_ativa_widget"]

# -----------------------------------------------------------------
# Correção da navegação:
# - se o usuário clicar manualmente no radio, o widget muda e vence;
# - se alguma outra parte do app forçar uma aba (ex.: Aba 3),
#   o estado lógico muda e o widget é sincronizado uma única vez.
# -----------------------------------------------------------------
widget_atual = st.session_state.get("_aba_ativa_widget")
widget_anterior = st.session_state.get("_aba_ativa_widget_prev")
aba_logica = st.session_state.get("aba_ativa_sistema")

mudanca_manual_widget = widget_atual != widget_anterior
mudanca_logica_externa = (aba_logica != widget_anterior) and (widget_atual == widget_anterior)

if mudanca_logica_externa and aba_logica in OPCOES_ABA_SISTEMA:
    st.session_state["_aba_ativa_widget"] = aba_logica

aba_ativa = st.radio(
    "Navegação do Sistema",
    OPCOES_ABA_SISTEMA,
    horizontal=True,
    key="_aba_ativa_widget"
)

# reflete a escolha final do usuário/sistema no estado lógico
st.session_state["aba_ativa_sistema"] = aba_ativa
st.session_state["_aba_ativa_widget_prev"] = aba_ativa
#endregion

#region 8.2: ABA 1 — Visão Gerencial (Indicadores)
if aba_ativa == "📊 Visão Gerencial (Indicadores)":
    if st.session_state["perfil"] == "Técnico":
        st.info("🔒 Seu perfil (Técnico) tem foco operacional. Por favor, utilize a aba 'Roteirização e Mapa de Campo'.")
    else:
        df_visao_base = df_filtrado.copy()

        cor_plan = "#64748B"      
        cor_real = "#3B82F6"      
        cor_prazo = "#10B981"     
        cor_atraso = "#F59E0B"    
        cor_pendente = "#FF4B4B"  

        if taxa_conclusao <= 25: gauge_color = cor_pendente
        elif taxa_conclusao <= 50: gauge_color = cor_atraso
        elif taxa_conclusao <= 80: gauge_color = cor_prazo
        else: gauge_color = cor_real

        with st.expander("Resumo Executivo (Geral)", expanded=True):
            col_g1, col_g2, col_g5 = st.columns(3)

            with col_g1:
                st.markdown("#### Realizado x Planejado")
                gauge_options = {
                    "tooltip": {"formatter": "{a} <br/>{b}: {c}%"},
                    "series": [{
                        "name": "Conclusão", "type": "gauge", "min": 0, "max": 100, "radius": "75%",
                        "progress": {"show": True, "width": 14, "itemStyle": {"color": gauge_color}},
                        "axisLine": {
                            "lineStyle": {
                                "width": 14,
                                "color": [[0.25, cor_pendente], [0.50, cor_atraso], [0.80, cor_prazo], [1.00, cor_real]]
                            }
                        },
                        "pointer": {"show": True, "length": "60%", "width": 6},
                        "itemStyle": {"color": gauge_color},
                        "title": {"show": True, "offsetCenter": [0, "70%"], "fontSize": 14},
                        "detail": {
                            "valueAnimation": True, "offsetCenter": [0, "40%"],
                            "formatter": f"{taxa_conclusao:.1f}%\n{realizado_total} / {total_os}", "fontSize": 16
                        },
                        "data": [{"value": round(taxa_conclusao, 1), "name": "Realizado"}],
                    }],
                }
                st_echarts(options=gauge_options, height="350px", theme="streamlit", key="aba1_gauge")

            with col_g2:
                st.markdown("#### Distribuição por Status")
                rosca_options = {
                    "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                    "legend": {"orient": "horizontal", "bottom": "0%"},
                    "series": [{
                        "name": "Status", "type": "pie", "radius": ["45%", "75%"],
                        "data": [
                            {"value": realizado_prazo, "name": "No Prazo", "itemStyle": {"color": cor_prazo}},
                            {"value": realizado_atraso, "name": "Atrasado", "itemStyle": {"color": cor_atraso}},
                            {"value": nao_realizado, "name": "Pendentes", "itemStyle": {"color": cor_pendente}},
                        ],
                        "label": {"show": True, "position": "inside", "formatter": "{c}\n({d}%)", "color": "#FFFFFF", "fontWeight": "bold"},
                    }],
                }
                st_echarts(options=rosca_options, height="350px", theme="streamlit", key="aba1_rosca")

            with col_g5:
                st.markdown("#### Plan x Real Acumulado")
                df_area = df_visao_base.copy()
                df_area["dia_programado"] = pd.to_datetime(df_area["Data inicial programada"], errors="coerce").dt.normalize()

                realizado_diario_a = (df_area[df_area["Status_norm"].isin(_status_prazo | _status_atraso)].groupby("dia_realizado").size().rename("Realizado_Dia"))
                planejado_diario_a = (df_area.groupby("dia_programado").size().rename("Planejado_Dia"))

                _datas_a = pd.Index([]).union(realizado_diario_a.index).union(planejado_diario_a.index)

                if len(_datas_a) > 0:
                    _idx_da = pd.date_range(start=_datas_a.min(), end=_datas_a.max(), freq="D")
                    _real_acum = realizado_diario_a.reindex(_idx_da, fill_value=0).cumsum()
                    _plan_acum = planejado_diario_a.reindex(_idx_da, fill_value=0).cumsum()

                    area_options = {
                        "tooltip": {"trigger": "axis"},
                        "legend": {"top": "bottom"},
                        "toolbox": {"show": True, "feature": {"magicType": {"type": ["line", "bar"], "title": {"line": "Linha", "bar": "Barra"}}, "restore": {"title": "Restaurar"}, "saveAsImage": {"title": "Salvar Imagem"}}},
                        "dataZoom": [{"type": "slider", "show": True, "xAxisIndex": [0], "start": 0, "end": 100, "bottom": "5%"}],
                        "grid": {"left": "5%", "right": "5%", "bottom": "25%", "top": "15%", "containLabel": True},
                        "xAxis": {"type": "category", "data": [d.strftime("%d/%m") for d in _idx_da]},
                        "yAxis": {"type": "value"},
                        "series": [
                            {"name": "Realizado Acumulado", "type": "line", "smooth": True, "data": _real_acum.tolist(), "areaStyle": {"color": "rgba(59,130,246,0.2)"}, "lineStyle": {"color": cor_real, "width": 3}, "itemStyle": {"color": cor_real}},
                            {"name": "Planejado Acumulado", "type": "line", "smooth": True, "data": _plan_acum.tolist(), "lineStyle": {"color": cor_plan, "width": 3, "type": "dashed"}, "itemStyle": {"color": cor_plan}},
                        ],
                    }
                    st_echarts(options=area_options, height="350px", theme="streamlit", key="aba1_area")
                else:
                    st.info("Sem datas suficientes para área.")

        with st.expander("Análise Operacional: Matriz de Prioridades e Execução por Categoria", expanded=True):
            col_h1, col_h2 = st.columns([1.2, 1])

            with col_h1:
                st.markdown("#### Matriz: Prioridade vs Classificação")
                st.caption("Volume total de OS planejadas (Cor indica concentração)")

                df_heat = df_visao_base.copy()
                agg = df_heat.groupby(["Classificacao", "Criticidade"]).size().reset_index(name="Total")

                ordem_class = ["Confiabilidade", "Segurança", "Confiabilidade e Segurança"]
                ordem_crit = ["Muito Alta", "Alta", "Média", "Baixa"]

                if not agg.empty:
                    heat_data = []
                    max_val = 0

                    for _yi, _cls in enumerate(ordem_class):
                        for _xi, _crt in enumerate(ordem_crit):
                            _row = agg[(agg["Classificacao"] == _cls) & (agg["Criticidade"] == _crt)]
                            _val = int(_row["Total"].iloc[0]) if not _row.empty else 0
                            heat_data.append([_xi, _yi, _val])
                            if _val > max_val: max_val = _val

                    heatmap_options = {
                        "tooltip": {"position": "top"},
                        "grid": {"height": "70%", "top": "10%", "left": "25%", "containLabel": True},
                        "xAxis": {"type": "category", "data": ordem_crit, "splitArea": {"show": True}, "axisLine": {"show": False}, "axisTick": {"show": False}},
                        "yAxis": {"type": "category", "data": ordem_class, "splitArea": {"show": True}, "axisLine": {"show": False}, "axisTick": {"show": False}},
                        "visualMap": {"min": 0, "max": max_val if max_val > 0 else 10, "calculable": True, "orient": "horizontal", "left": "center", "bottom": "0%", "inRange": {"color": ["#F1F5F9", "#93C5FD", "#3B82F6", "#1E3A8A"]}},
                        "series": [{"name": "Total de OS", "type": "heatmap", "data": heat_data, "label": {"show": True, "color": "#FFFFFF", "fontWeight": "bold", "formatter": JsCode("function(p){return p.value[2] > 0 ? p.value[2] : '';}")}, "itemStyle": {"borderColor": "#FFFFFF", "borderWidth": 2}}],
                    }
                    st_echarts(options=heatmap_options, height="380px", theme="streamlit", key="aba1_heatmap_discrete")
                else:
                    st.info("Sem dados para a Matriz.")

            with col_h2:
                st.markdown("#### Plan x Realizado por Categoria")
                st.caption("Comparativo de volume total e execução.")

                df_bar_cat = df_visao_base.copy()
                plan_cat = df_bar_cat.groupby("Classificacao").size()
                real_cat = (df_bar_cat[df_bar_cat["Status_norm"].isin(_status_prazo | _status_atraso)].groupby("Classificacao").size())

                cats = ["Confiabilidade e Segurança", "Segurança", "Confiabilidade"]
                val_plan = [int(plan_cat.get(c, 0)) for c in cats]
                val_real = [int(real_cat.get(c, 0)) for c in cats]

                bar_horiz_options = {
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                    "legend": {"bottom": "0%"},
                    "grid": {"left": "3%", "right": "10%", "bottom": "15%", "top": "10%", "containLabel": True},
                    "xAxis": {"type": "value", "boundaryGap": [0, 0.01]},
                    "yAxis": {"type": "category", "data": cats, "axisLabel": {"interval": 0}},
                    "series": [
                        {"name": "Planejado", "type": "bar", "data": val_plan, "itemStyle": {"color": cor_plan}, "label": {"show": True, "position": "right", "color": "#475569"}},
                        {"name": "Realizado", "type": "bar", "data": val_real, "itemStyle": {"color": cor_real}, "label": {"show": True, "position": "right", "color": "#475569"}}
                    ]
                }
                st_echarts(options=bar_horiz_options, height="380px", theme="streamlit", key="aba1_bar_horiz")

        with st.expander("Execução por Turno e Acumulado", expanded=True):
            col_g3, col_g6 = st.columns(2)

            _cor_turno = { "00h-07h": "#4F46E5", "07h-16h": "#3B82F6", "16h-00h": "#06B6D4" }

            with col_g3:
                st.markdown("#### Realizado por Turno")
                df_barra_real = df_visao_base[df_visao_base["Status_norm"].isin(_status_prazo | _status_atraso)].copy()
                x_turnos = ["00h-07h", "07h-16h", "16h-00h"]
                _cnt_t = df_barra_real.groupby("Turno").size()
                y_vals = [int(_cnt_t.get(t, 0)) for t in x_turnos]

                barra_options = {
                    "tooltip": {"trigger": "axis"},
                    "xAxis": {"type": "category", "data": x_turnos},
                    "yAxis": {"type": "value"},
                    "toolbox": {"show": True, "feature": {"magicType": {"type": ["line", "bar"], "title": {"line": "Linha", "bar": "Barra"}}, "restore": {"title": "Restaurar"}, "saveAsImage": {"title": "Salvar Imagem"}}},
                    "grid": {"left": "5%", "right": "5%", "bottom": "15%", "top": "15%", "containLabel": True},
                    "series": [{"type": "bar", "barWidth": "55%", "label": {"show": True, "position": "inside", "formatter": "{c}", "color": "#FFFFFF", "fontWeight": "bold"}, "data": [{"value": v, "name": t, "itemStyle": {"color": _cor_turno.get(t, "#94A3B8")}} for t, v in zip(x_turnos, y_vals)]}],
                }
                st_echarts(options=barra_options, height="350px", theme="streamlit", key="aba1_barra")

            with col_g6:
                st.markdown("#### Realizado Acumulado por Turno")
                df_linhas_plot = df_visao_base.dropna(subset=["dia_realizado"]).copy()

                if not df_linhas_plot.empty:
                    _ordem_t = ["00h-07h", "07h-16h", "16h-00h"]
                    _idx_dt = pd.date_range(start=df_linhas_plot["dia_realizado"].min(), end=df_linhas_plot["dia_realizado"].max(), freq="D")

                    _series_t = []
                    for _t in _ordem_t:
                        _s = (df_linhas_plot[df_linhas_plot["Turno"] == _t].groupby("dia_realizado").size().reindex(_idx_dt, fill_value=0).cumsum())
                        _series_t.append({"name": _t, "type": "line", "smooth": True, "data": _s.tolist(), "lineStyle": {"color": _cor_turno[_t], "width": 3}, "itemStyle": {"color": _cor_turno[_t]}})

                    linhas_options = {
                        "tooltip": {"trigger": "axis"},
                        "legend": {"top": "bottom"},
                        "toolbox": {"show": True, "feature": {"magicType": {"type": ["line", "bar", "stack"], "title": {"line": "Linha", "bar": "Barra", "stack": "Empilhado"}}, "restore": {"title": "Restaurar"}, "saveAsImage": {"title": "Salvar Imagem"}}},
                        "dataZoom": [{"type": "slider", "show": True, "xAxisIndex": [0], "start": 0, "end": 100, "bottom": "5%"}],
                        "grid": {"left": "5%", "right": "5%", "bottom": "25%", "top": "15%", "containLabel": True},
                        "xAxis": {"type": "category", "data": [d.strftime("%d/%m") for d in _idx_dt]},
                        "yAxis": {"type": "value"},
                        "series": _series_t,
                    }
                    st_echarts(options=linhas_options, height="350px", theme="streamlit", key="aba1_linhas")
                else:
                    st.info("Sem dados cronológicos.")

        st.subheader("📋 Lista Detalhada de OS")
        df_lista = df_visao_base.copy().rename(columns={"Ordem servico": "OS"})

        if "Data inicial programada" in df_lista.columns:
            df_lista["Data inicial programada"] = pd.to_datetime(df_lista["Data inicial programada"], errors="coerce").dt.strftime("%d/%m/%Y")

        if "Data/Hora Realizado" in df_lista.columns:
            df_lista["Data/Hora Realizado"] = pd.to_datetime(df_lista["Data/Hora Realizado"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M").fillna("")

        colunas_ordem = ["OS", "Patio", "Ativo", "Criticidade", "Classificacao", "Descrição Longa", "Data inicial programada", "Status da Operação", "Data/Hora Realizado", "Concluído por"]

        for c in colunas_ordem:
            if c not in df_lista.columns: df_lista[c] = ""

        if not df_lista.empty:
            df_styled = df_lista[colunas_ordem].style.set_properties(**{'text-align': 'center'}).set_table_styles([{'selector': 'th', 'props': [('text-align', 'center')]}])
            st.dataframe(df_styled, use_container_width=True, height=400, hide_index=True)
#endregion
#region 8.3: ABA 2 — Roteirização e Mapa de Campo
if aba_ativa == "🗺️ Roteirização e Mapa de Campo":

    # 0. Inicialização de segurança da variável para evitar NameError
    df_recomendado = pd.DataFrame()
    
    # 8.3.1 Calendário mensal
    st.markdown("### 📅 Agenda Mensal de Demanda por Pátio")
    
    # CSS: Fontes e Cartões da Aba 2 com dimensões limpas e elegantes
    st.markdown(
        """
        <style>
        .kpi-wrapper { font-family: "Source Sans Pro", sans-serif; }

        /* Card 1: Azul */
        .kpi-card-blue {
            background: linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%);
            border-left: 5px solid #3B82F6; 
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 4px 6px rgba(59, 130, 246, 0.15);
            height: 140px; 
            margin-bottom: 16px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            box-sizing: border-box;
        }
        .kpi-title-blue { color: #1E3A8A; font-size: 14px; font-weight: 700; margin-bottom: 6px; text-transform: uppercase; }
        .kpi-val-blue { color: #1E40AF; font-size: 32px; font-weight: 400; line-height: 1; }
        .kpi-sub-blue { color: #3B82F6; font-size: 12px; font-weight: 400; margin-top: 8px;}

        /* Card 2: Verde */
        .kpi-card-green {
            background: linear-gradient(135deg, #F0FDF4 0%, #D1FAE5 100%);
            border-left: 5px solid #10B981; 
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 4px 6px rgba(16, 185, 129, 0.15);
            height: 140px; 
            margin-bottom: 16px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            box-sizing: border-box;
        }
        .kpi-title-green { color: #064E3B; font-size: 14px; font-weight: 700; margin-bottom: 6px; text-transform: uppercase; }
        .kpi-val-green { color: #065F46; font-size: 32px; font-weight: 400; line-height: 1; }
        .kpi-badge { 
            font-size: 12px; font-weight: 400; padding: 4px 10px; border-radius: 20px; 
            display: inline-block; margin-top: 10px; width: fit-content; 
        }

        /* Card 3: Vermelho */
        .kpi-card-red {
            background: linear-gradient(135deg, #FEF2F2 0%, #FEE2E2 100%);
            border-left: 5px solid #FF4B4B; 
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 4px 6px rgba(255, 75, 75, 0.15);
            height: 140px; 
            margin-bottom: 16px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            box-sizing: border-box;
        }
        .kpi-title-red { color: #7F1D1D; font-size: 14px; font-weight: 700; margin-bottom: 6px; text-transform: uppercase; }
        .kpi-val-red { color: #991B1B; font-size: 24px; font-weight: 400; line-height: 1.2; margin-top: 4px;} 
        .kpi-sub-red { color: #EF4444; font-size: 12px; font-weight: 400; margin-top: 8px;}
        </style>
        """,
        unsafe_allow_html=True
    )

    hoje_ref = datetime.now()

    # Garantir que as variáveis de estado existam
    if "cal_ref_mes" not in st.session_state: st.session_state["cal_ref_mes"] = int(hoje_ref.month)
    if "cal_ref_ano" not in st.session_state: st.session_state["cal_ref_ano"] = int(hoje_ref.year)

    # Colunas criadas FORA do IF para não quebrar o layout da árvore do Streamlit
    col_cal_ctrl_1, col_cal_ctrl_2, _ = st.columns([1, 1, 4])

    is_tecnico = st.session_state.get("perfil") == "Técnico"

    if is_tecnico:
        st.session_state["cal_ref_mes"] = int(hoje_ref.month)
        st.session_state["cal_ref_ano"] = int(hoje_ref.year)
        with col_cal_ctrl_1: 
            st.info(f"Mês: {hoje_ref.strftime('%m')}")
        with col_cal_ctrl_2: 
            st.info(f"Ano: {hoje_ref.year}")
        st.caption(f"📌 **Visão Operacional de Campo:** Calendário fixado no mês vigente ({hoje_ref.strftime('%m/%Y')})")
        st.markdown("<div style='margin-bottom: -10px;'></div>", unsafe_allow_html=True)
    else:
        with col_cal_ctrl_1:
            mes_opcao = st.selectbox(
                "Mês",
                list(range(1, 13)),
                index=int(st.session_state["cal_ref_mes"]) - 1,
                format_func=lambda x: f"{x:02d}",
                key="cal_mes_ref_select"
            )

        with col_cal_ctrl_2:
            ano_atual = hoje_ref.year
            ano_opcao = st.number_input(
                "Ano",
                min_value=ano_atual - 2,
                max_value=ano_atual + 2,
                value=int(st.session_state["cal_ref_ano"]),
                step=1,
                key="cal_ano_ref_input"
            )

        st.session_state["cal_ref_mes"] = int(mes_opcao)
        st.session_state["cal_ref_ano"] = int(ano_opcao)

    # Preparação dos dados do calendário
    df_calendario = df_visao.copy()
    
    # Tratamento seguro caso os filtros não existam no escopo local
    if "patios_selecionados" in locals() and "classif_selecionadas" in locals():
        df_calendario = df_calendario[
            (df_calendario["Patio"].isin(patios_selecionados)) &
            (df_calendario["Classificacao"].isin(classif_selecionadas))
        ].copy()

    hoje_real = datetime.now().date()
    if (
        int(st.session_state["cal_ref_ano"]) == hoje_real.year and
        int(st.session_state["cal_ref_mes"]) == hoje_real.month
    ):
        dia_ref_default = hoje_real
    else:
        dia_ref_default = datetime(
            int(st.session_state["cal_ref_ano"]),
            int(st.session_state["cal_ref_mes"]),
            1
        ).date()

    user_limpo = str(st.session_state.get('username', 'usr')).replace(" ", "_").lower()
    # Key fixa para técnico previne re-renders desnecessários
    cal_key = f"cal_fixo_tecnico_{user_limpo}" if is_tecnico else f"cal_dinamico_{user_limpo}"

    cal_state = st.session_state.get(cal_key)
    data_ref_card = dia_ref_default
    
    if cal_state and isinstance(cal_state, dict):
        if cal_state.get("callback") == "dateClick":
            data_ref_card = pd.to_datetime(cal_state["dateClick"]["date"]).date()
        elif cal_state.get("callback") == "eventClick":
            data_ref_card = pd.to_datetime(cal_state["eventClick"]["event"]["start"]).date()
            
    if data_ref_card.year != int(st.session_state["cal_ref_ano"]) or data_ref_card.month != int(st.session_state["cal_ref_mes"]):
        data_ref_card = dia_ref_default

    calendar_events = montar_eventos_calendario_patios(
        df_base_cal=df_calendario,
        ano=int(st.session_state["cal_ref_ano"]),
        mes=int(st.session_state["cal_ref_mes"]),
        max_patios_visiveis=2
    )

    calendar_options = {
        "initialView": "dayGridMonth",
        "initialDate": f"{int(st.session_state['cal_ref_ano']):04d}-{int(st.session_state['cal_ref_mes']):02d}-01",
        "locale": "pt-br",
        "height": "auto",
        "contentHeight": "auto",
        "headerToolbar": { "left": "", "center": "title", "right": "" },
        "dayMaxEvents": 2,
        "eventOrder": "displayOrder,title",
        "fixedWeekCount": False,
        "showNonCurrentDates": True,
        "expandRows": True,
        "handleWindowResize": True,
    }

    calendar_css_base = """
    .fc { font-size: 14px; background: #FFFFFF; border-radius: 12px; padding: 6px; box-shadow: 0 1px 8px rgba(15, 23, 42, 0.08); }
    .fc .fc-toolbar { margin-bottom: 0.25rem !important; }
    .fc .fc-toolbar-title { font-size: 1.4rem !important; font-weight: 800; text-align: center; text-transform: capitalize; color: #1E293B; }
    .fc .fc-scrollgrid { border-radius: 10px; overflow: hidden; border: 1px solid #E2E8F0; }
    .fc .fc-scroller, .fc .fc-scroller-liquid-absolute { overflow: hidden !important; }
    .fc .fc-col-header-cell { background-color: #F8FAFC; }
    .fc .fc-col-header-cell-cushion { font-size: 14px; font-weight: 800; color: #334155; padding: 6px 2px !important; text-transform: capitalize; }
    .fc .fc-daygrid-day-number { font-size: 1.1rem; font-weight: 800; padding: 4px 6px !important; color: #334155; }
    .fc .fc-daygrid-day-frame { min-height: 62px !important; cursor: pointer; transition: background-color 0.2s; }
    .fc .fc-daygrid-day-frame:hover { background-color: #F8FAFC !important; }
    .fc .fc-daygrid-event { border-radius: 6px; padding: 3px 5px; font-size: 12.5px !important; line-height: 1.15; font-weight: 800; margin-top: 1px !important; cursor: pointer; }
    .fc .fc-daygrid-event .fc-event-title { white-space: nowrap !important; overflow: hidden; text-overflow: ellipsis; letter-spacing: 0.2px; }
    .fc .fc-theme-standard td, .fc .fc-theme-standard th { border-color: #E2E8F0; }
    """

    calendar_css_dinamico = f"""
    {calendar_css_base}
    .fc-daygrid-day[data-date="{data_ref_card.strftime('%Y-%m-%d')}"] {{
        background-color: #EFF6FF !important;
        box-shadow: inset 0 0 0 3px #3B82F6 !important;
    }}
    .fc-daygrid-day[data-date="{data_ref_card.strftime('%Y-%m-%d')}"] .fc-daygrid-day-number {{
        color: #1D4ED8 !important;
        background-color: #DBEAFE !important;
        border-radius: 6px;
        padding: 2px 6px !important;
    }}
    """

    # PROPORÇÃO DAS COLUNAS CORRIGIDA PARA DAR ESPAÇO AO GRÁFICO
    col_calendario, col_cards, col_turno = st.columns([5.8, 2.0, 2.2], gap="large")

    with col_calendario:
        calendar_state = calendar(
            events=calendar_events,
            options=calendar_options,
            custom_css=calendar_css_dinamico,
            callbacks=["dateClick", "eventClick"],
            key=cal_key
        )

    resumo_card = resumir_demanda_calendario(
        df_base_cal=df_calendario, ano=data_ref_card.year, mes=data_ref_card.month, dia_ref=data_ref_card.day
    )

    resumo_turno = resumir_conclusoes_por_turno_data(df_base_cal=df_calendario, data_ref=data_ref_card)

    with col_cards:
        # Card 1 - Pátios do Dia (Azul) 
        st.markdown(
            f"""
            <div class="kpi-wrapper kpi-card-blue">
                <div class="kpi-title-blue">Pátios do Dia</div>
                <div class="kpi-val-blue">{resumo_card['qtd_patios']} <span style='font-size: 22px;'>📌</span></div>
                <div class="kpi-sub-blue">Referência: {data_ref_card.strftime('%d/%m/%Y')}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

        # Lógica de Variação para o Card 2 (Delta)
        dia_idx = data_ref_card.day - 1
        serie_mes = resumo_card["serie_total_os_mes"]
        hoje_total = serie_mes[dia_idx] if dia_idx < len(serie_mes) else 0
        ontem_total = serie_mes[dia_idx - 1] if dia_idx > 0 else hoje_total

        if ontem_total > 0:
            delta_pct = ((hoje_total - ontem_total) / ontem_total) * 100
        else:
            delta_pct = 0.0

        if delta_pct > 0:
            seta, cor_badge, bg_badge, sinal = "↑", "#065F46", "#A7F3D0", "+"
        elif delta_pct < 0:
            seta, cor_badge, bg_badge, sinal = "↓", "#991B1B", "#FECACA", ""
        else:
            seta, cor_badge, bg_badge, sinal = "→", "#475569", "#E2E8F0", ""

        # Card 2 - Total de OS do Dia (Verde com Mini Sparkline Otimizado)
        total_os_options = {
            "backgroundColor": "#F0FDF4", 
            "animation": False,
            "grid": { "left": "6%", "right": "6%", "top": "68%", "bottom": "8%" },
            "xAxis": { "type": "category", "show": False, "boundaryGap": False, "data": resumo_card["labels_mes"] },
            "yAxis": { "type": "value", "show": False },
            "series": [{
                "type": "bar",
                "barWidth": 3,
                "itemStyle": { "color": "#10B981", "borderRadius": [2, 2, 0, 0] }, 
                "data": resumo_card["serie_total_os_mes"]
            }],
            "graphic": [
                { "type": "text", "left": "6%", "top": "12%", "style": { "text": "TOTAL DE OS DO DIA", "fill": "#064E3B", "font": "700 14px 'Source Sans Pro', sans-serif" } },
                { "type": "text", "left": "6%", "top": "34%", "style": { "text": f"{hoje_total} 🎯", "fill": "#065F46", "font": "400 32px 'Source Sans Pro', sans-serif" } },
                { "type": "text", "left": "6%", "top": "70%", "style": { "text": f"{seta} {sinal}{delta_pct:.1f}% vs ontem", "fill": "#10B981", "font": "400 12px 'Source Sans Pro', sans-serif" } },
                { "type": "rect", "left": 0, "top": 0, "shape": {"width": 5, "height": 140}, "style": {"fill": "#10B981"} } 
            ]
        }
        
        st_echarts(options=total_os_options, height="140px", key="card_total_os_dia")
        st.markdown("<div style='margin-bottom: 16px;'></div>", unsafe_allow_html=True) 

        # Card 3 - Pátio Prioritário (Vermelho)
        st.markdown(
            f"""
            <div class="kpi-wrapper kpi-card-red">
                <div class="kpi-title-red">Pátio Prioritário</div>
                <div class="kpi-val-red">{resumo_card['patio_prioritario']}</div>
                <div class="kpi-sub-red">Critério: backlog + prioridade</div>
            </div>
            """,
            unsafe_allow_html=True
        )

    with col_turno:
        _cor_turno_aba2 = { "00h-07h": "#4F46E5", "07h-16h": "#3B82F6", "16h-00h": "#06B6D4" }
        
        dados_formatados_turno = [
            {
                "value": val, 
                "itemStyle": { "color": _cor_turno_aba2.get(lbl, "#3B82F6"), "borderRadius": [0, 6, 6, 0] }
            }
            for lbl, val in zip(resumo_turno["labels"], resumo_turno["valores"])
        ]

        with st.container(border=True):
            concl_turno_options = {
                "title": {
                    "text": resumo_turno["titulo"],
                    "subtext": resumo_turno["subtitulo"],
                    "left": "center",
                    "top": "5%",
                    "textStyle": { "fontSize": 14, "fontWeight": "bold", "color": "#1E293B", "fontFamily": '"Source Sans Pro", sans-serif' },
                    "subtextStyle": { "fontSize": 12, "color": "#64748B", "fontFamily": '"Source Sans Pro", sans-serif' }
                },
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "grid": { "left": "18%", "right": "10%", "bottom": "12%", "top": "24%", "containLabel": True },
                "xAxis": { "type": "value", "minInterval": 1, "splitLine": { "lineStyle": { "type": "dashed", "color": "#E2E8F0" } } },
                "yAxis": { 
                    "type": "category", 
                    "data": resumo_turno["labels"], 
                    "axisLabel": { "fontSize": 12, "fontWeight": "600", "color": "#475569", "fontFamily": '"Source Sans Pro", sans-serif' },
                    "axisLine": { "show": False }, "axisTick": { "show": False }
                },
                "series": [{
                    "name": "OS Concluídas", "type": "bar", "data": dados_formatados_turno, "barWidth": "42%",
                    "label": { "show": True, "position": "right", "color": "#1E293B", "fontWeight": "bold", "fontSize": 13, "fontFamily": '"Source Sans Pro", sans-serif' }
                }]
            }
            st_echarts(options=concl_turno_options, height="435px", theme="streamlit", key="chart_conclusoes_turno_data")

    st.markdown("---")

# 8.3.2 Navegação geográfica operacional
    st.markdown("### 🗺️ Navegação Geográfica Operacional")

    col_mapa, col_acao = st.columns([6, 4], gap="large")

    # Inicialização de segurança
    df_recomendado = pd.DataFrame()

    # Proteção caso df_filtrado não esteja no escopo
    if "df_filtrado" in locals():
        df_pendentes_f = df_filtrado[df_filtrado["Status_norm"].isin(_status_aberto)].copy()
    else:
        df_pendentes_f = df_visao[df_visao["Status_norm"].isin(_status_aberto)].copy()

    with col_acao:
        st.markdown("#### ⚙️ Ferramentas de Campo")

        # ==========================================================
        # Estado inicial da origem
        # ==========================================================
        if "lat_partida" not in st.session_state:
            lat_base, lon_base, nome_base = obter_base_padrao_usuario()
            st.session_state["lat_partida"] = lat_base
            st.session_state["lon_partida"] = lon_base
            st.session_state["local_nome"] = nome_base
            st.session_state["origem_tipo"] = "BASE"

        if "gps_pending" not in st.session_state:
            st.session_state["gps_pending"] = False

        if "gps_trials" not in st.session_state:
            st.session_state["gps_trials"] = 0

        if "origem_tipo" not in st.session_state:
            st.session_state["origem_tipo"] = "BASE"

        GPS_MAX_TRIALS = 25

        c1, c2 = st.columns(2)

        with c1:
            if st.button("📍 Minha Localização", use_container_width=True, key="btn_gps_localizacao"):
                st.session_state["gps_pending"] = True
                st.session_state["gps_trials"] = 0
                st.rerun()

        with c2:
            if st.button("🏠 Minha Base", use_container_width=True, key="btn_minha_base"):
                lat_base, lon_base, nome_base = obter_base_padrao_usuario()
                st.session_state["lat_partida"] = lat_base
                st.session_state["lon_partida"] = lon_base
                st.session_state["local_nome"] = nome_base
                st.session_state["origem_tipo"] = "BASE"
                st.session_state["gps_pending"] = False
                st.session_state["gps_trials"] = 0
                st.rerun()

        # ==========================================================
        # Captura do GPS
        # ==========================================================
        if st.session_state.get("gps_pending"):
            st.info("Aguardando autorização do navegador e captura do GPS...")

            loc = get_geolocation()

            if loc and isinstance(loc, dict) and "coords" in loc:
                coords = loc.get("coords", {})
                lat = coords.get("latitude")
                lon = coords.get("longitude")

                if lat is not None and lon is not None:
                    st.session_state["lat_partida"] = float(lat)
                    st.session_state["lon_partida"] = float(lon)
                    st.session_state["local_nome"] = reverse_geocode_coordenada(float(lat), float(lon))
                    st.session_state["origem_tipo"] = "GPS"
                    st.session_state["gps_pending"] = False
                    st.session_state["gps_trials"] = 0
                    st.success("GPS Ativado!")
                    st.rerun()

            elif loc and isinstance(loc, dict) and "error" in loc:
                st.session_state["gps_pending"] = False
                st.session_state["gps_trials"] = 0
                st.error(f"GPS falhou: {loc['error'].get('message', 'Erro')}")

            else:
                st.session_state["gps_trials"] += 1

                if st.session_state["gps_trials"] < GPS_MAX_TRIALS:
                    time.sleep(0.3)
                    st.rerun()
                else:
                    st.session_state["gps_pending"] = False
                    st.session_state["gps_trials"] = 0
                    st.error("Tempo do GPS esgotado. Tente novamente ou use a opção Minha Base.")

        st.markdown("---")
        raio_busca_km = st.slider("📏 Raio de Atuação Visual (km):", 0, 50, 10, 5, key="slider_raio_atuacao")
        st.caption(f"📌 Origem: **{st.session_state['local_nome']}**")

        lat_origem = float(st.session_state["lat_partida"])
        lon_origem = float(st.session_state["lon_partida"])

        if not df_pendentes_f.empty:
            df_calc = df_pendentes_f.copy()
            df_calc["lat_patio"] = df_calc["Patio"].map(
                lambda p: COORDENADAS_FIXAS.get(str(p).strip().upper(), [np.nan, np.nan])[0]
            )
            df_calc["lon_patio"] = df_calc["Patio"].map(
                lambda p: COORDENADAS_FIXAS.get(str(p).strip().upper(), [np.nan, np.nan])[1]
            )
            com_coord = df_calc.dropna(subset=["lat_patio", "lon_patio"]).copy()

            if not com_coord.empty:
                hoje_atual = datetime.now().date()

                com_coord["Ordem_Prazo"] = com_coord["dt_prog_filtro"].apply(
                    lambda dt: 1 if pd.notna(dt) and dt.date() < hoje_atual
                    else (2 if pd.notna(dt) and dt.date() == hoje_atual else 3)
                )

                com_coord["Distancia_km"] = haversine_vectorized(
                    lat_origem,
                    lon_origem,
                    com_coord["lat_patio"],
                    com_coord["lon_patio"]
                )

                df_recomendado = com_coord[
                    com_coord["Distancia_km"] <= raio_busca_km
                ].sort_values(
                    by=["Ordem_Prazo", "Criticidade_rank", "Distancia_km"]
                )

        st.info(f"**{len(df_recomendado)} OS pendentes** encontradas no raio de {raio_busca_km}km.")

        if not df_recomendado.empty:
            st.markdown("---")
            st.markdown("#### ✅ Confirmar Execução")

            os_selecionada = st.selectbox(
                "Escolha a OS concluída:",
                df_recomendado["Ordem servico"].astype(str).tolist()
            )

            if st.button("Gravar Baixa no Sistema", use_container_width=True, type="primary"):
                realizado_dt = agora_dt()
                usr = st.session_state["username"]

                mask = (
                    st.session_state["df_os"]["Ordem servico"].astype(str)
                    == str(os_selecionada)
                )

                dt_prog = (
                    st.session_state["df_os"].loc[mask, "Data inicial programada"].iloc[0]
                    if len(st.session_state["df_os"].loc[mask]) > 0 else pd.NaT
                )

                novo_status = determinar_status_execucao(dt_prog, realizado_dt)
                coord = st.session_state["df_os"].loc[mask, "Coordenacao"].iloc[0]

                upsert_baixa(
                    str(os_selecionada),
                    novo_status,
                    formatar_dt_br(realizado_dt),
                    coord,
                    usr
                )

                st.toast(f"OS {os_selecionada} baixada com sucesso!")
                st.rerun()

    with col_mapa:
        SP_MIN_LAT, SP_MAX_LAT = -25.50, -19.50
        SP_MIN_LON, SP_MAX_LON = -53.50, -44.00

        lat_centro = min(max(lat_origem, SP_MIN_LAT), SP_MAX_LAT)
        lon_centro = min(max(lon_origem, SP_MIN_LON), SP_MAX_LON)

        def calcular_zoom_por_raio(raio_km: float, latitude_ref: float) -> int:
            raio_km = max(float(raio_km), 0.5)
            lat_rad = math.radians(float(latitude_ref))
            km_por_grau_lon = 111.320 * max(math.cos(lat_rad), 0.20)
            largura_graus = (2.0 * raio_km) / km_por_grau_lon
            zoom = math.log2(360.0 / max(largura_graus, 1e-6))
            return int(min(18, max(6, round(zoom))))

        zoom_mapa = calcular_zoom_por_raio(raio_busca_km, lat_centro)

        # ==========================================================
        # MAPA BASE — MESMO ESTILO CLEAN DA ABA 3
        # ==========================================================
        mapa = folium.Map(
            location=[lat_centro, lon_centro],
            zoom_start=zoom_mapa,
            control_scale=True,
            tiles="CartoDB positron",
            prefer_canvas=True,
            max_bounds=True,
            min_lat=SP_MIN_LAT,
            max_lat=SP_MAX_LAT,
            min_lon=SP_MIN_LON,
            max_lon=SP_MAX_LON,
        )

        # ==========================================================
        # KML DA MALHA REAL — MESMO PADRÃO VISUAL DA ABA 3
        # ==========================================================
        import geopandas as gpd
        from shapely.geometry import LineString, MultiLineString

        caminho_kml = "malha_mrs.kml"

        if os.path.exists(caminho_kml):
            try:
                gdf_malha = gpd.read_file(caminho_kml, driver="KML")

                for _, row in gdf_malha.iterrows():
                    geom = row.geometry

                    if geom is None or geom.is_empty:
                        continue

                    def adicionar_trecho_kml_operacional(geom_trecho):
                        estilo = {
                            "color": "#2563EB",
                            "weight": 2,
                            "opacity": 0.70
                        }

                        folium.GeoJson(
                            geom_trecho.__geo_interface__,
                            style_function=lambda x, estilo=estilo: estilo,
                            control=False
                        ).add_to(mapa)

                    if isinstance(geom, LineString):
                        adicionar_trecho_kml_operacional(geom)

                    elif isinstance(geom, MultiLineString):
                        for linha in geom.geoms:
                            if linha is not None and not linha.is_empty:
                                adicionar_trecho_kml_operacional(linha)

                    else:
                        try:
                            if hasattr(geom, "geoms"):
                                for parte in geom.geoms:
                                    if isinstance(parte, (LineString, MultiLineString)):
                                        if isinstance(parte, LineString):
                                            adicionar_trecho_kml_operacional(parte)
                                        else:
                                            for linha in parte.geoms:
                                                if linha is not None and not linha.is_empty:
                                                    adicionar_trecho_kml_operacional(linha)
                        except Exception:
                            pass

            except Exception as e:
                st.warning(f"Não foi possível desenhar a malha KML na Aba 2: {e}")

        # ==========================================================
        # ORIGEM DO USUÁRIO
        # ==========================================================
        origem_tipo = st.session_state.get("origem_tipo", "BASE")

        if origem_tipo == "GPS":
            cor_fill_origem = "#F97316"   # laranja
            cor_border_origem = "#C2410C"
            tooltip_origem = f"Minha Localização (GPS): {st.session_state['local_nome']}"
        else:
            cor_fill_origem = "#EF4444"   # vermelho
            cor_border_origem = "#991B1B"
            tooltip_origem = f"Minha Base: {st.session_state['local_nome']}"

        folium.CircleMarker(
            location=[lat_origem, lon_origem],
            radius=7,
            color=cor_border_origem,
            weight=2,
            fill=True,
            fill_color=cor_fill_origem,
            fill_opacity=0.95,
            tooltip=tooltip_origem
        ).add_to(mapa)

        folium.Circle(
            location=[lat_origem, lon_origem],
            radius=raio_busca_km * 1000.0,
            color="#2563EB",
            weight=2,
            fill=True,
            fill_opacity=0.05
        ).add_to(mapa)

        # ==========================================================
        # NÓS RECOMENDADOS NO RAIO (PADRÃO VISUAL DA ABA 3)
        # ==========================================================
        if not df_recomendado.empty:
            df_pts = df_recomendado.copy()

            agg_map = (
                df_pts.groupby("Patio", as_index=False)
                .agg(
                    lat_patio=("lat_patio", "first"),
                    lon_patio=("lon_patio", "first"),
                    qtd_os=("Ordem servico", "count"),
                    menor_dist=("Distancia_km", "min")
                )
                .sort_values(["menor_dist", "Patio"])
            )

            for _, row in agg_map.iterrows():
                patio_nome = str(row["Patio"]).strip().upper()
                tipo_no = TIPO_NO.get(patio_nome, "PATIO")

                if tipo_no == "ESTACAO":
                    cor_fill = "#22C55E"
                    cor_border = "#166534"
                    raio = 5
                else:
                    cor_fill = "#3B82F6"
                    cor_border = "#1D4ED8"
                    raio = 6

                folium.CircleMarker(
                    location=[row["lat_patio"], row["lon_patio"]],
                    radius=raio,
                    color=cor_border,
                    weight=1.5,
                    fill=True,
                    fill_color=cor_fill,
                    fill_opacity=0.95,
                    tooltip=f"{patio_nome} — {int(row['qtd_os'])} OS no raio"
                ).add_to(mapa)

        st_folium(
            mapa,
            height=650,
            use_container_width=True,
            key="mapa_final_limpo"
        )

    st.markdown("---")
    
    # 8.3.4 Cronograma de Execução de Campo
    if not df_recomendado.empty:
        df_tabela_campo = df_recomendado.copy()
        df_tabela_campo = df_tabela_campo.rename(columns={"Ordem servico": "OS", "Patio": "Patio", "Classificacao": "Classificação"})
        df_tabela_campo["Data da Programação"] = df_tabela_campo["dt_prog_filtro"].dt.strftime("%d/%m/%Y")

        colunas_exibir = ["OS", "Data da Programação", "Patio", "Ativo", "Criticidade", "Classificação", "Descrição Longa"]

        col_tit_crono, col_btn_crono = st.columns([7.5, 2.5])
        
        with col_tit_crono:
            st.markdown("#### 📋 Cronograma de Execução de Campo")
            st.caption("OS Pendentes recomendadas no raio de atuação visual por prioridade")
            
        with col_btn_crono:
            st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
            
            def exportar_cronograma_pdf(dataframe, usuario_logado):
                try:
                    from reportlab.lib.pagesizes import letter, landscape
                    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
                    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                    from reportlab.lib import colors
                    import io
                    
                    pdf_buffer = io.BytesIO()
                    doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(letter), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
                    elements = []
                    
                    styles = getSampleStyleSheet()
                    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor('#1A202C'), spaceAfter=6)
                    sub_style = ParagraphStyle('SubStyle', fontName='Helvetica', fontSize=10, textColor=colors.HexColor('#475569'), spaceAfter=15)
                    cell_style = ParagraphStyle('CellStyle', fontName='Helvetica', fontSize=9, leading=11, textColor=colors.HexColor('#1E293B'))
                    header_style = ParagraphStyle('HeaderStyle', fontName='Helvetica-Bold', fontSize=10, leading=12, textColor=colors.white)
                    
                    elements.append(Paragraph("⚡ MRS LOGÍSTICA — CRONOGRAMA OPERACIONAL DE CAMPO", title_style))
                    elements.append(Paragraph(f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')} | Operador responsável: {usuario_logado.upper()}", sub_style))
                    
                    dados_pdf = [[Paragraph(col, header_style) for col in colunas_exibir]]
                    
                    for _, row in dataframe[colunas_exibir].iterrows():
                        linha = []
                        for col in colunas_exibir:
                            texto_limpo = str(row[col]).replace('\n', ' ').replace('\r', '')
                            linha.append(Paragraph(texto_limpo, cell_style))
                        dados_pdf.append(linha)
                    
                    larguras_colunas = [65, 80, 50, 110, 75, 120, 252]
                    tabela_pdf = Table(dados_pdf, colWidths=larguras_colunas, repeatRows=1)
                    
                    tabela_style = TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1A202C')),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                        ('TOPPADDING', (0, 0), (-1, 0), 8),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
                    ])
                    
                    for i in range(1, len(dados_pdf)):
                        if i % 2 == 0:
                            tabela_style.add('BACKGROUND', (0, i), (-1, i), colors.HexColor('#F8FAFC'))
                            
                    tabela_pdf.setStyle(tabela_style)
                    elements.append(tabela_pdf)
                    
                    doc.build(elements)
                    pdf_buffer.seek(0)
                    return pdf_buffer.read()
                except Exception:
                    return None

            pdf_bytes = exportar_cronograma_pdf(df_tabela_campo, st.session_state.get('username', 'técnico'))
            
            if pdf_bytes:
                st.download_button("📄 Gerar PDF para Impressão", data=pdf_bytes, file_name=f"Cronograma_MRS_{datetime.now().strftime('%d%m%Y_%H%M')}.pdf", mime="application/pdf", use_container_width=True)
            else:
                st.button("📄 Erro ao estruturar PDF", disabled=True, use_container_width=True)

        def aplicar_cor_prazo(row):
            dt = row["dt_prog_filtro"]
            if pd.isna(dt): return [""] * len(row)
            d = dt.date(); hoje_ref = datetime.now().date()
            if d < hoje_ref: return ["background-color: #FEE2E2; color: #7F1D1D; font-weight: 500;"] * len(row)
            elif d == hoje_ref: return ["background-color: #FEF3C7; color: #78350F; font-weight: 500;"] * len(row)
            return [""] * len(row)

        df_estilizado = df_tabela_campo.style.apply(aplicar_cor_prazo, axis=1)
        st.dataframe(df_estilizado, use_container_width=True, height=350, hide_index=True, column_order=colunas_exibir)
    else:
        st.markdown("#### 📋 Cronograma de Execução de Campo")
        st.caption("OS Pendentes recomendadas no raio de atuação visual por prioridade")
        st.info("Nenhuma OS pendente localizada dentro do raio de atuação selecionado.")
#endregion
#region 8.4: ABA 3 — Inteligência da Malha
if aba_ativa == "🕸️ Inteligência da Malha":
    st.markdown("### 🕸️ Análise de Resiliência Ferroviária")
    st.caption("Simulador de impacto operacional e gargalos logísticos na malha.")

    # ==========================================================
    # Estado base da simulação
    # ==========================================================
    cent = nx.betweenness_centrality(G, weight="Distancia_KM")
    nx.set_node_attributes(G, cent, "centralidade")

    if "patio_bloqueado_manual" not in st.session_state:
        st.session_state["patio_bloqueado_manual"] = None

    # ==========================================================
    # 8.4.1 — Integração Dinâmica com Ordens de Serviço
    # ==========================================================
    st.markdown("#### 🔄 Integração Dinâmica com Ordens de Serviço")

    ativar_integ = st.session_state.get("sim_ativar_gemeo_digital", False)

    # começa sempre do grafo original
    G_dinamico = G.copy()
    patios_bloqueados_os = []

    # Digital Twin usa criticidade da 1ª simulação:
    # Muito Alta + Alta e ainda pendentes
    if ativar_integ:
        df_pendentes_criticas = df_visao[
            (df_visao["Status_norm"].isin(_status_aberto)) &
            (df_visao["Criticidade"].isin(["Muito Alta", "Alta"]))
        ].copy()

        patios_bloqueados_os = (
            df_pendentes_criticas["Patio"]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
            .unique()
            .tolist()
        )

        for p in patios_bloqueados_os:
            if p in G_dinamico.nodes():
                G_dinamico.remove_node(p)

        if patios_bloqueados_os:
            st.error(
                f"🚨 **GÊMEO DIGITAL:** Pátios bloqueados por OS críticas "
                f"(Muito Alta / Alta) pendentes: **{', '.join(patios_bloqueados_os)}**"
            )
        else:
            st.success("✅ **GÊMEO DIGITAL:** Nenhuma OS crítica pendente (Muito Alta / Alta). Malha liberada.")
    else:
        st.info("💡 Modo padrão. A malha reage apenas à simulação manual configurada na barra lateral.")

    # aplica também o bloqueio manual
    patio_manual = st.session_state.get("patio_bloqueado_manual")
    patio_manual_norm = str(patio_manual).strip().upper() if patio_manual else None

    if patio_manual_norm and patio_manual_norm in G_dinamico.nodes():
        G_dinamico.remove_node(patio_manual_norm)

    # consolida todos os nós bloqueados ativos
    nos_bloqueados_ativos = sorted(
        set(
            patios_bloqueados_os +
            ([patio_manual_norm] if patio_manual_norm else [])
        )
    )

    if nos_bloqueados_ativos:
        st.caption(
            "⛔ **Nós bloqueados ativos na simulação:** "
            + ", ".join(nos_bloqueados_ativos)
        )

    st.markdown("---")

    # ==========================================================
    # 8.4.1.1 — Simulador de Rota Operacional
    # ==========================================================
    st.markdown("#### 🎯 Simulador de Rota Operacional")
    st.caption("Selecione origem e destino para contextualizar o impacto do bloqueio na malha.")

    lista_nos_malha = sorted([str(n).strip().upper() for n in G.nodes()])

    if "origem_operacional_malha" not in st.session_state:
        st.session_state["origem_operacional_malha"] = "IEF" if "IEF" in lista_nos_malha else lista_nos_malha[0]

    if "destino_operacional_malha" not in st.session_state:
        st.session_state["destino_operacional_malha"] = "IPG" if "IPG" in lista_nos_malha else lista_nos_malha[-1]

    col_rota_1, col_rota_2 = st.columns(2)

    with col_rota_1:
        origem_sel = st.selectbox(
            "Origem Operacional",
            lista_nos_malha,
            index=lista_nos_malha.index(st.session_state["origem_operacional_malha"])
            if st.session_state["origem_operacional_malha"] in lista_nos_malha else 0,
            key="origem_operacional_malha"
        )

    with col_rota_2:
        destinos_validos = [n for n in lista_nos_malha if n != origem_sel]
        destino_atual = st.session_state.get("destino_operacional_malha", destinos_validos[0])
        idx_destino = destinos_validos.index(destino_atual) if destino_atual in destinos_validos else 0

        destino_sel = st.selectbox(
            "Destino Operacional",
            destinos_validos,
            index=idx_destino,
            key="destino_operacional_malha"
        )

    def menor_caminho_seguro(grafo, origem, destino):
        try:
            return nx.shortest_path(
                grafo.to_undirected(),
                source=origem,
                target=destino,
                weight="Distancia_KM"
            )
        except Exception:
            return None

    caminho_principal_previsto = menor_caminho_seguro(G, origem_sel, destino_sel)

    if origem_sel in nos_bloqueados_ativos:
        caminho_alternativo_previsto = None
        st.error(f"❌ A origem **{origem_sel}** está bloqueada na malha atual.")
    elif destino_sel in nos_bloqueados_ativos:
        caminho_alternativo_previsto = None
        st.error(f"❌ O destino **{destino_sel}** está bloqueado na malha atual.")
    else:
        caminho_alternativo_previsto = menor_caminho_seguro(G_dinamico, origem_sel, destino_sel)

    st.session_state["caminho_principal_previsto"] = caminho_principal_previsto
    st.session_state["caminho_alternativo_previsto"] = caminho_alternativo_previsto
    st.session_state["nos_bloqueados_ativos_malha"] = nos_bloqueados_ativos

    if caminho_principal_previsto:
        st.caption(
            "➡️ **Caminho principal previsto:** "
            + " → ".join([str(n).strip().upper() for n in caminho_principal_previsto])
        )
    else:
        st.warning("Não foi possível calcular o caminho principal no grafo original.")

    if caminho_alternativo_previsto:
        if caminho_principal_previsto and caminho_alternativo_previsto == caminho_principal_previsto:
            st.info("ℹ️ O caminho alternativo calculado é igual ao principal (não houve desvio efetivo na malha).")
        else:
            st.caption(
                "🔁 **Caminho alternativo disponível na malha atual:** "
                + " → ".join([str(n).strip().upper() for n in caminho_alternativo_previsto])
            )
    else:
        if origem_sel not in nos_bloqueados_ativos and destino_sel not in nos_bloqueados_ativos:
            st.error("❌ Não existe caminho alternativo disponível entre a origem e o destino na malha atual.")

    st.markdown("---")

    # ==========================================================
    # 8.4.2 — Métricas e Mapa
    # ==========================================================
    col_metricas, col_mapa = st.columns([1.0, 1.7], gap="large")

    with col_metricas:
        st.markdown("#### 📊 Centralidade dos Pátios (Gargalos)")
        st.caption("Índice de intermediação recalculado em tempo real. Quanto maior o valor, maior o potencial de impacto do pátio na conectividade da malha.")

        if patio_manual_norm:
            n_comp = nx.number_weakly_connected_components(G_dinamico)
            if n_comp > 1:
                st.warning(f"⚠️ A falha em **{patio_manual_norm}** fragmenta a malha em **{n_comp} partes**.")
            else:
                st.success(f"✅ O bloqueio em **{patio_manual_norm}** não fragmenta a rede por completo.")

        cent_dinamica = (
            nx.betweenness_centrality(G_dinamico, weight="Distancia_KM")
            if len(G_dinamico.nodes()) > 0 else {}
        )

        if cent_dinamica:
            df_cent = pd.DataFrame(
                cent_dinamica.items(),
                columns=["Pátio", "Índice de Gargalo da Rede"]
            ).sort_values("Índice de Gargalo da Rede", ascending=False).reset_index(drop=True)

            df_cent["Posição"] = range(1, len(df_cent) + 1)

            maior_indice = df_cent["Índice de Gargalo da Rede"].max()
            if maior_indice > 0:
                df_cent["Índice Relativo (%)"] = (
                    df_cent["Índice de Gargalo da Rede"] / maior_indice * 100
                ).round(1)
            else:
                df_cent["Índice Relativo (%)"] = 0.0

            def classificar_faixa(indice_relativo):
                if indice_relativo >= 80:
                    return "Muito Alto"
                elif indice_relativo >= 55:
                    return "Alto"
                elif indice_relativo >= 25:
                    return "Médio"
                return "Baixo"

            df_cent["Faixa"] = df_cent["Índice Relativo (%)"].apply(classificar_faixa)

            def leitura_operacional(faixa):
                if faixa == "Muito Alto":
                    return "Gargalo estrutural da malha"
                elif faixa == "Alto":
                    return "Elo estratégico com alto impacto"
                elif faixa == "Médio":
                    return "Impacto regional relevante"
                return "Impacto mais localizado"

            df_cent["Leitura Operacional"] = df_cent["Faixa"].apply(leitura_operacional)

            df_cent = df_cent[
                [
                    "Posição",
                    "Pátio",
                    "Índice de Gargalo da Rede",
                    "Índice Relativo (%)",
                    "Faixa",
                    "Leitura Operacional",
                ]
            ]

            st.dataframe(
                df_cent.style.background_gradient(
                    cmap="Reds",
                    subset=["Índice de Gargalo da Rede", "Índice Relativo (%)"]
                ),
                use_container_width=True,
                height=430,
                hide_index=True
            )
        else:
            st.error("A rede inteira está inoperante.")

    with col_mapa:
        # ==========================================================
        # 8.4.3 — Visualização Geoespacial
        # ==========================================================
        st.markdown("#### 🗺️ Topologia da Rede")

        import geopandas as gpd
        from shapely.geometry import Point, LineString, MultiLineString
        from shapely.ops import unary_union
        from branca.element import Element

        CORREDOR_BOUNDS = [
            [-24.05, -47.20],
            [-23.00, -45.55],
        ]

        CORREDOR_CENTER = [
            (CORREDOR_BOUNDS[0][0] + CORREDOR_BOUNDS[1][0]) / 2,
            (CORREDOR_BOUNDS[0][1] + CORREDOR_BOUNDS[1][1]) / 2,
        ]

        m_rede = folium.Map(
            location=CORREDOR_CENTER,
            zoom_start=9,
            control_scale=True,
            tiles="CartoDB positron",
            prefer_canvas=True
        )

        # ----------------------------
        # Coordenadas fixas
        # ----------------------------
        dict_coords = {}
        for nome, coord in COORDENADAS_FIXAS.items():
            nome_limpo = str(nome).strip().upper()
            dict_coords[nome_limpo] = (float(coord[0]), float(coord[1]))

        # ----------------------------------------------------------
        # 8.4.3.3 — Suporte geométrico para pintar o próprio KMZ
        # CORREÇÃO: usar ARESTAS da rota (entre nós consecutivos),
        # e não uma única linha reta simplificada da rota inteira.
        # Isso evita perder o tracejado vermelho e o verde sobre o KML.
        # ----------------------------------------------------------
        BUFFER_NO_KML = 0.010
        BUFFER_ARESTA_KML = 0.012

        caminho_principal_contexto = st.session_state.get("caminho_principal_previsto")
        caminho_alternativo_contexto = st.session_state.get("caminho_alternativo_previsto")

        def rota_normalizada(rota):
            if not rota:
                return []
            return [str(n).strip().upper() for n in rota]

        rota_principal = rota_normalizada(caminho_principal_contexto)
        rota_alternativa = rota_normalizada(caminho_alternativo_contexto)

        def extrair_trechos_desvio(rota_principal, rota_alternativa):
            if not rota_principal or not rota_alternativa:
                return [], []

            if rota_principal == rota_alternativa:
                return [], []

            i = 0
            limite = min(len(rota_principal), len(rota_alternativa))
            while i < limite and rota_principal[i] == rota_alternativa[i]:
                i += 1

            idx_inicio = max(i - 1, 0)
            principal_restante = rota_principal[idx_inicio:]
            alternativa_restante = rota_alternativa[idx_inicio:]

            no_reencontro = None
            idx_reencontro_principal = None
            idx_reencontro_alternativa = None

            for j_alt, no_alt in enumerate(alternativa_restante):
                if no_alt in principal_restante:
                    no_reencontro = no_alt
                    idx_reencontro_alternativa = idx_inicio + j_alt
                    idx_reencontro_principal = rota_principal.index(no_alt, idx_inicio)
                    break

            if no_reencontro is None:
                trecho_bloqueado_principal = rota_principal[idx_inicio:]
                trecho_alternativo = rota_alternativa[idx_inicio:]
                return trecho_bloqueado_principal, trecho_alternativo

            trecho_bloqueado_principal = rota_principal[idx_inicio:idx_reencontro_principal + 1]
            trecho_alternativo = rota_alternativa[idx_inicio:idx_reencontro_alternativa + 1]
            return trecho_bloqueado_principal, trecho_alternativo

        trecho_bloqueado_principal, trecho_alternativo = extrair_trechos_desvio(
            rota_principal,
            rota_alternativa
        )

        def pares_consecutivos(rota):
            if not rota or len(rota) < 2:
                return []
            return [(rota[i], rota[i + 1]) for i in range(len(rota) - 1)]

        def construir_geom_buffer_aresta(no_a, no_b):
            no_a = str(no_a).strip().upper()
            no_b = str(no_b).strip().upper()

            if no_a not in dict_coords or no_b not in dict_coords:
                return None

            lat_a, lon_a = dict_coords[no_a]
            lat_b, lon_b = dict_coords[no_b]

            ponto_a = Point(lon_a, lat_a).buffer(BUFFER_NO_KML)
            ponto_b = Point(lon_b, lat_b).buffer(BUFFER_NO_KML)
            linha_ab = LineString([(lon_a, lat_a), (lon_b, lat_b)]).buffer(BUFFER_ARESTA_KML)

            return unary_union([ponto_a, ponto_b, linha_ab])

        geom_bloqueadas = []
        for a, b in pares_consecutivos(trecho_bloqueado_principal):
            g = construir_geom_buffer_aresta(a, b)
            if g is not None:
                geom_bloqueadas.append(g)

        geom_alternativas = []
        for a, b in pares_consecutivos(trecho_alternativo):
            g = construir_geom_buffer_aresta(a, b)
            if g is not None:
                geom_alternativas.append(g)

        geom_bloqueadas_union = unary_union(geom_bloqueadas) if geom_bloqueadas else None
        geom_alternativas_union = unary_union(geom_alternativas) if geom_alternativas else None

        if trecho_bloqueado_principal:
            st.caption(
                "🟥 **Trecho bloqueado da rota principal:** "
                + " → ".join(trecho_bloqueado_principal)
            )
        if trecho_alternativo:
            st.caption(
                "🟩 **Trecho efetivo do desvio alternativo:** "
                + " → ".join(trecho_alternativo)
            )

        # ----------------------------------------------------------
        # 8.4.3.4 — Estilo do KML
        # ----------------------------------------------------------
        def estilo_trecho_kml(geom_trecho):
            # prioridade 1: alternativa (verde)
            if geom_alternativas_union is not None:
                try:
                    if geom_trecho.intersects(geom_alternativas_union):
                        return {
                            "color": "#16A34A",
                            "weight": 4,
                            "opacity": 0.95
                        }
                except Exception:
                    pass

            # prioridade 2: bloqueio da principal (vermelho tracejado)
            if geom_bloqueadas_union is not None:
                try:
                    if geom_trecho.intersects(geom_bloqueadas_union):
                        return {
                            "color": "#EF4444",
                            "weight": 4,
                            "opacity": 0.95,
                            "dashArray": "6, 8"
                        }
                except Exception:
                    pass

            return {
                "color": "#2563EB",
                "weight": 2,
                "opacity": 0.70
            }

        def adicionar_trecho_kml_no_mapa(geom_trecho):
            estilo = estilo_trecho_kml(geom_trecho)

            folium.GeoJson(
                geom_trecho.__geo_interface__,
                style_function=lambda x, estilo=estilo: estilo,
                control=False
            ).add_to(m_rede)

        # ----------------------------------------------------------
        # 8.4.3.5 — Desenho do KML
        # ----------------------------------------------------------
        caminho_kml = "malha_mrs.kml"

        if os.path.exists(caminho_kml):
            try:
                gdf_malha = gpd.read_file(caminho_kml, driver="KML")

                for _, row in gdf_malha.iterrows():
                    geom = row.geometry

                    if geom is None or geom.is_empty:
                        continue

                    if isinstance(geom, LineString):
                        adicionar_trecho_kml_no_mapa(geom)

                    elif isinstance(geom, MultiLineString):
                        for linha in geom.geoms:
                            if linha is not None and not linha.is_empty:
                                adicionar_trecho_kml_no_mapa(linha)

                    else:
                        try:
                            if hasattr(geom, "geoms"):
                                for parte in geom.geoms:
                                    if isinstance(parte, (LineString, MultiLineString)):
                                        if isinstance(parte, LineString):
                                            adicionar_trecho_kml_no_mapa(parte)
                                        else:
                                            for linha in parte.geoms:
                                                if linha is not None and not linha.is_empty:
                                                    adicionar_trecho_kml_no_mapa(linha)
                        except Exception:
                            pass

            except Exception as e:
                st.error(f"Erro ao ler o KML: {e}")
        else:
            st.warning("Arquivo 'malha_mrs.kml' não encontrado.")

        # ----------------------------
        # Nós
        # ----------------------------
        for no in G.nodes():
            no_ = str(no).strip().upper()
            if no_ not in dict_coords:
                continue

            coord = dict_coords[no_]
            tipo = TIPO_NO.get(no_, "PATIO")

            bloqueado = (
                no not in G_dinamico.nodes() or
                no in patios_bloqueados_os or
                no == st.session_state.get("patio_bloqueado_manual")
            )

            if bloqueado:
                folium.CircleMarker(
                    location=coord,
                    radius=7,
                    color="#7F1D1D",
                    weight=2,
                    fill=True,
                    fill_color="#111111",
                    fill_opacity=0.9,
                    tooltip=f"{no} BLOQUEADO"
                ).add_to(m_rede)

                folium.Marker(
                    location=coord,
                    icon=folium.DivIcon(
                        html="""
                        <div style="
                            font-size:18px;
                            text-align:center;
                            transform: translate(-3px, -8px);
                        ">🚧</div>
                        """
                    )
                ).add_to(m_rede)
            else:
                if tipo == "ESTACAO":
                    cor_fill = "#22C55E"
                    cor_border = "#166534"
                    raio = 5
                else:
                    cor_fill = "#3B82F6"
                    cor_border = "#1D4ED8"
                    raio = 6

                folium.CircleMarker(
                    location=coord,
                    radius=raio,
                    color=cor_border,
                    weight=1.5,
                    fill=True,
                    fill_color=cor_fill,
                    fill_opacity=0.95,
                    tooltip=f"{no} ({tipo})"
                ).add_to(m_rede)

        # ----------------------------
        # Legenda visual do mapa
        # ----------------------------
        legenda_html = """
        <div style="
            position: fixed;
            bottom: 22px;
            left: 22px;
            z-index: 9999;
            background-color: white;
            border: 2px solid rgba(0,0,0,0.15);
            border-radius: 8px;
            padding: 10px 12px;
            font-size: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.12);
        ">
            <div style="font-weight:700; margin-bottom:6px;">Legenda</div>
            <div style="margin-bottom:4px;">
                <span style="display:inline-block; width:18px; height:3px; background:#2563EB; margin-right:8px;"></span>
                Malha livre
            </div>
            <div style="margin-bottom:4px;">
                <span style="display:inline-block; width:18px; height:3px; border-top:3px dashed #EF4444; margin-right:8px;"></span>
                Trecho bloqueado da rota principal
            </div>
            <div style="margin-bottom:4px;">
                <span style="display:inline-block; width:18px; height:3px; background:#16A34A; margin-right:8px;"></span>
                Rota alternativa operacional
            </div>
            <div>
                <span style="display:inline-block; width:18px; text-align:center; margin-right:8px;">🚧</span>
                Nó bloqueado
            </div>
        </div>
        """
        m_rede.get_root().html.add_child(Element(legenda_html))

        # ----------------------------
        # Enquadramento final
        # ----------------------------
        try:
            m_rede.fit_bounds(CORREDOR_BOUNDS)
        except Exception:
            pass

        # ----------------------------
        # Render final
        # ----------------------------
        st_folium(
            m_rede,
            height=460,
            use_container_width=True,
            key="mapa_grafo_inteligencia"
        )
#endregion
#endregion
#endregion

