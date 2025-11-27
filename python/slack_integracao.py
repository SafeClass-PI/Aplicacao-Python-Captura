import os
import re
import time
from mysql.connector import connect, Error
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()


config = {
    'user':  os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'host': os.getenv("DB_HOST"),
    'database': os.getenv("DB_DATABASE"),
    'port': int(os.getenv("PORT", 3306))
}

# Tirar quando der clone
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN") 
slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

# Buscar apenas UM alerta não enviado
def pegar_um_alerta_nao_enviado():
    try:
        db = connect(**config)
        with db.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT 
                    a.idAlerta,
                    a.fkParametro,
                    a.mensagem, 
                    a.fkCaptura,
                    m.idMaquina,
                    m.ip AS ip_maquina,
                    m.marca,
                    m.sistemaOperacional,
                    s.nome AS sala,
                    comp.nome AS componente,
                    comp.capacidade,
                    comp.formatacao,
                    cap.registro AS valor_atual,
                    pa.minimo AS atencao_min,
                    pa.maximo AS atencao_max,
                    pc.minimo AS critico_min,
                    pc.maximo AS critico_max,
                    p.nivel AS nivel_alerta
                FROM Alerta a
                JOIN Parametro p ON p.idParametro = a.fkParametro
                JOIN Componente comp ON comp.idComponente = p.fkComponente
                JOIN Captura cap ON cap.idCaptura = a.fkCaptura
                JOIN Maquina m ON m.idMaquina = comp.fkMaquina
                JOIN Sala s ON s.idSala = m.fkSala
                LEFT JOIN Parametro pa ON pa.fkComponente = comp.idComponente AND pa.nivel = 'Atenção'
                LEFT JOIN Parametro pc ON pc.fkComponente = comp.idComponente AND pc.nivel = 'Crítico'
                WHERE a.enviado = 0 OR a.enviado IS NULL
                LIMIT 1
            """)
            resultado = cursor.fetchone()
        db.close()
        return resultado
    except Error as e:
        print(f"❌ Erro ao buscar alerta: {e}")
        return None

# Buscar idSlack da escola da máquina
def pegar_idSlack_da_maquina(idMaquina: int):
    try:
        db = connect(**config)
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT e.idSlack
                FROM Escola e
                JOIN Sala s ON s.fkEscola = e.idEscola
                JOIN Maquina m ON m.fkSala = s.idSala
                WHERE m.idMaquina = %s
            """, (idMaquina,))
            resultado = cursor.fetchone()
        db.close()
        return resultado[0] if resultado else None
    except Error as e:
        print(f"❌ Erro ao buscar idSlack da máquina {idMaquina}: {e}")
        return None

# Marcar alerta como enviado
def marcar_alerta_enviado(idAlerta, enviado: int):
    try:
        db = connect(**config)
        with db.cursor() as cursor:
            cursor.execute("UPDATE Alerta SET enviado = %s WHERE idAlerta = %s", (enviado, idAlerta))
            db.commit()
        db.close()
    except Error as e:
        print(f"❌ Erro ao marcar alerta {idAlerta} como enviado: {e}")

# Enviar UM alerta para o Slack
def enviar_alerta_slack():
    alerta = pegar_um_alerta_nao_enviado()
    if not alerta:
        print("ℹ️ Nenhum alerta pendente.")
        return

    idSlack = pegar_idSlack_da_maquina(alerta['idMaquina'])
    if not idSlack or not slack_client:
        print(f"⚠️ Alerta ID {alerta['idAlerta']} - Slack não configurado para máquina {alerta['idMaquina']}")
        marcar_alerta_enviado(alerta['idAlerta'], 0)
        return

    try:
        # Extrair valor numérico
        match_valor = re.search(r'[\d,.]+', str(alerta['valor_atual']))
        valor_float = float(match_valor.group().replace(',', '.')) if match_valor else 0

        match_capacidade = re.search(r'[\d,.]+', str(alerta['capacidade']))
        capacidade_float = float(match_capacidade.group().replace(',', '.')) if match_capacidade else 0

        unidade = alerta['formatacao']
        if unidade.lower() in ['gb', 'mb', 'tb']:
            valor_formatado = f"{valor_float:.1f} {unidade}"
            capacidade_formatada = f"{capacidade_float:.1f} {unidade}"
        else:
            valor_formatado = f"{valor_float:.1f}{unidade}"
            capacidade_formatada = f"{capacidade_float:.1f}{unidade}"

        nivel_lower = alerta['nivel_alerta'].lower()
        if nivel_lower == 'atenção':
            simbolo = "🟡"
        elif nivel_lower == 'crítico':
            simbolo = "🔴"
        else:
            simbolo = "ℹ️"

        motivo = f"{simbolo} *{alerta['nivel_alerta'].capitalize()}* - Componente {alerta['componente']} está com o valor atual de {valor_formatado}"

        mensagem = (
            f"⚠️ *Alerta detectado!*\n"
            f"│ Sala: {alerta['sala']}\n"
            f"│ Máquina: {alerta['idMaquina']} - IP: {alerta['ip_maquina']} ({alerta['marca']})\n"
            f"│ Componente: {alerta['componente']}\n"
            f"│ Valor atual: {valor_formatado}\n"
            f"│ Capacidade: {capacidade_formatada}\n"
            f"│ Nível do alerta: {alerta['nivel_alerta']}\n"
            f"│ Limite Atenção: {alerta['atencao_min']} - {alerta['atencao_max']} {unidade}\n"
            f"│ Limite Crítico: {alerta['critico_min']} - {alerta['critico_max']} {unidade}\n"
            f"│ Sistema Operacional: {alerta['sistemaOperacional']}\n"
            f"│ Motivo: {motivo}"
        )

        slack_client.chat_postMessage(channel=idSlack, text=mensagem)
        marcar_alerta_enviado(alerta['idAlerta'], 1)
        print(f"📨 Alerta ID {alerta['idAlerta']} enviado para Slack! Valor atual: {valor_formatado}")

    except SlackApiError as e:
        print(f"❌ Erro ao enviar alerta ID {alerta['idAlerta']} para Slack: {e.response['error']}")
        marcar_alerta_enviado(alerta['idAlerta'], 0)
    except Exception as e:
        print(f"❌ Erro inesperado ao processar alerta ID {alerta['idAlerta']}: {e}")
        marcar_alerta_enviado(alerta['idAlerta'], 0)


if __name__ == "__main__":
    while True:
        enviar_alerta_slack()
        time.sleep(3)
