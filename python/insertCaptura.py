import psutil as p
from mysql.connector import connect, Error
from dotenv import load_dotenv
import os
import datetime
import time
import platform
from tabulate import tabulate
from pythonping import ping

load_dotenv()

MODO_SIMULACAO = False  # ⬅ Altere para False para usar valores reais

# Alterar valores para cada máquina
CPU_SIMULADA = 100.0           # CPU 100%
MEMORIA_SIMULADA_GB = 16.0     # memória usada simulada
DISCO_SIMULADO_GB = 500.0      # disco usado simulado
PING_SIMULADO = 1000
# -------------------------------

config = {
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'host': os.getenv("DB_HOST"),
    'database': os.getenv("DB_DATABASE"),
    'port': int(os.getenv("PORT", 3306)),
    'connection_timeout': 30
}

maquinas_simuladas = [
    {
        'id_maquina': 1,
        'componentes': {
            'CPU': 3,
            'Memoria': 1,
            'Disco': 2,
            'Ping': 6
        }
    },
]

def medir_ping(host="8.8.8.8", tentativas=10):
    resposta = ping(host, count=tentativas)
    return resposta.rtt_avg_ms

def inserir_dados_e_alertas(maquina, cpu, memoria, disco):
    try:
        db = connect(**config)
        with db.cursor(dictionary=True) as cursor:
            dt = datetime.datetime.now()

            registros = {
                'CPU': cpu,
                'Memoria': memoria,
                'Disco': disco
            }

            ids_captura = {}

            # Inserir capturas
            for comp_nome, valor in registros.items():
                fk_componente = maquina['componentes'][comp_nome]
                cursor.execute(
                    "INSERT INTO Captura (fkComponente, registro, dtCaptura) VALUES (%s, %s, %s)",
                    (fk_componente, valor, dt)
                )
                ids_captura[comp_nome] = cursor.lastrowid

            db.commit()

            # Buscar parâmetros
            cursor.execute(
                """SELECT idParametro, fkComponente, nivel, minimo, maximo 
                FROM Parametro 
                WHERE fkComponente IN (%s, %s, %s)""",
                (maquina['componentes']['CPU'],
                maquina['componentes']['Memoria'],
                maquina['componentes']['Disco'])
            )

            parametros = cursor.fetchall()
            
            # Buscar formatação dos componentes
            cursor.execute(
                "SELECT idComponente, formatacao FROM Componente WHERE idComponente IN (%s, %s, %s)",
                (maquina['componentes']['CPU'],
                maquina['componentes']['Memoria'],
                maquina['componentes']['Disco'])
            )

            formatacoes = cursor.fetchall()

            map_format = {f["idComponente"]: f["formatacao"] for f in formatacoes}

            alertas = []
            id_para_nome = {v: k for k, v in maquina['componentes'].items()}

            for param in parametros:
                comp_nome = id_para_nome[param['fkComponente']]
                valor_atual = float(registros[comp_nome])
                minimo = float(param['minimo'])
                maximo = float(param['maximo'])

                if minimo <= valor_atual <= maximo:
                    fkCaptura = ids_captura[comp_nome]
                    alertas.append((param['idParametro'], fkCaptura, comp_nome, param['nivel']))

            mensagens_slack = []

            if alertas:
                for fkParametro, fkCaptura, comp_nome, nivel in alertas:
                    
                    valor_atual = registros[comp_nome]

                    formatacao = map_format[maquina["componentes"][comp_nome]]

                    captura_formatada = f"{round(valor_atual, 1)}{formatacao}"

                    mensagem = f"Uso de {comp_nome} a {captura_formatada}"

                    cursor.execute(
                        "INSERT INTO Alerta (fkParametro, fkCaptura, enviado, mensagem) VALUES (%s, %s, 0, %s)",
                        (fkParametro, fkCaptura, mensagem)
                    )

                    cursor.execute(
                        "UPDATE Maquina SET status = %s WHERE idMaquina = %s",
                        (nivel, maquina['id_maquina'])
                    )

                    mensagens_slack.append(
                        f":{nivel.lower()}: Alerta detectado!\n"
                        f"│ Máquina: {maquina['id_maquina']}\n"
                        f"│ Componente: {comp_nome}\n"
                        f"│ Valor atual: {registros[comp_nome]:.2f}\n"
                        f"│ Nível: {nivel}\n"
                        f"│ Hora: {dt.strftime('%H:%M:%S')}\n"
                        f"│ Motivo: {nivel} atingido"
                    )
                db.commit()
                print(f"⚠ Alertas gerados: {len(alertas)}")
            else:
                cursor.execute(
                    "UPDATE Maquina SET status = %s WHERE idMaquina = %s",
                    ("Estável", maquina['id_maquina'])
                )
                db.commit()
                print("✔ Sem alertas no momento")

            return mensagens_slack

    except Error as e:
        print("❌ Erro:", e)
        return []
    finally:
        try:
            db.close()
        except:
            pass

def inserir_ping(maquina, ping_medio):
    try:
        db = connect(**config)
        with db.cursor() as cursor:
            dt = datetime.datetime.now()
            fk_componente = maquina['componentes']['Ping']
            cursor.execute(
                "INSERT INTO Captura (fkComponente, registro, dtCaptura) VALUES (%s, %s, %s)",
                (fk_componente, ping_medio, dt)
            )
            db.commit()
    except Error as e:
        print("❌ Erro ao inserir ping:", e)
    finally:
        try:
            db.close()
        except:
            pass

# ============================================
# 🚀 LOOP PRINCIPAL
# ============================================
while True:
    hostname = platform.node()

    # -------------------------
    # MODO SIMULAÇÃO ATIVO
    # -------------------------
    if MODO_SIMULACAO:
        cpu_percent = CPU_SIMULADA
        memoria_usada_GB = MEMORIA_SIMULADA_GB
        disco_usado_GB = DISCO_SIMULADO_GB
        ping_medio = PING_SIMULADO

    # -------------------------
    # MODO REAL
    # -------------------------
    else:
        cpu_percent = p.cpu_percent(interval=1)
        memoria = p.virtual_memory()
        memoria_usada_GB = (memoria.total - memoria.available) / (1024 ** 3)
        disco = p.disk_usage("/")
        disco_usado_GB = disco.used / (1024 ** 3)
        ping_medio = medir_ping()

    # Exibição
    captura_display = [
        ["Hostname", hostname],
        ["CPU %", f"{cpu_percent:.1f}%"],
        ["Memória usada (GB)", f"{memoria_usada_GB:.2f} GB"],
        ["Disco usado (GB)", f"{disco_usado_GB:.2f} GB"],
        ["Ping médio (ms)", f"{ping_medio:.1f} ms"]
    ]

    print(tabulate(captura_display, headers=["Componente", "Valor"], tablefmt="fancy_grid"))

    # Inserir dados e gerar alertas
    for maquina in maquinas_simuladas:
        mensagens_slack = inserir_dados_e_alertas(maquina, cpu_percent, memoria_usada_GB, disco_usado_GB)
        inserir_ping(maquina, ping_medio)

        for msg in mensagens_slack:
            print("\n💬 Mensagem para Slack:\n", msg)

    time.sleep(30)
