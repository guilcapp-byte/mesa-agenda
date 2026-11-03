"""Roda no GitHub Actions: publica no Instagram as peças da fila cujo horário já chegou.

Uso: python publicar.py   publica o que estiver vencido em fila.json
Variáveis: IG_TOKEN (token de usuário do sistema, sem validade), IG_USER_ID e SITE (endereço público dos arquivos).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://graph.facebook.com/v26.0"
FILA = Path(__file__).with_name("fila.json")
MAX_TENTATIVAS = 3
ESPERA_STATUS = 15  # segundos entre as consultas do processamento da mídia
LIMITE_STATUS = 40  # consultas: 10 minutos


class ErroPublicacao(Exception):
    pass


def chamar(caminho, dados=None, token=None):
    pedido = urllib.request.Request(
        f"{API}{caminho}",
        data=urllib.parse.urlencode(dados).encode() if dados else None,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=120) as resposta:
            return json.load(resposta)
    except urllib.error.HTTPError as e:
        raise ErroPublicacao(f"HTTP {e.code} em {caminho}: {e.read().decode(errors='replace')[:400]}")


def criar_conteiner(usuario, token, dados):
    return chamar(f"/{usuario}/media", dados, token)["id"]


def esperar_conteiner(conteiner, token):
    """A Meta baixa e processa a mídia; só depois ela pode ser publicada."""
    for _ in range(LIMITE_STATUS):
        status = chamar(f"/{conteiner}?fields=status_code", token=token)["status_code"]
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise ErroPublicacao(f"a Meta recusou a mídia ({status})")
        time.sleep(ESPERA_STATUS)
    raise ErroPublicacao("a Meta não terminou de processar a mídia em 10 minutos")


def publicar_peca(peca, usuario, token, site):
    urls = [f"{site.rstrip('/')}/{urllib.parse.quote(a)}" for a in peca["arquivos"]]
    legenda = peca["legenda"]
    if peca["formato"] == "video":
        dados = {"media_type": "REELS", "video_url": urls[0], "caption": legenda, "share_to_feed": "true"}
        conteiner = criar_conteiner(usuario, token, dados)
    elif peca["formato"] == "estatico":
        conteiner = criar_conteiner(usuario, token, {"image_url": urls[0], "caption": legenda})
    elif peca["formato"] == "carrossel":
        filhos = []
        for url in urls:
            filho = criar_conteiner(usuario, token, {"image_url": url, "is_carousel_item": "true"})
            esperar_conteiner(filho, token)
            filhos.append(filho)
        dados = {"media_type": "CAROUSEL", "children": ",".join(filhos), "caption": legenda}
        conteiner = criar_conteiner(usuario, token, dados)
    else:
        raise ErroPublicacao(f'formato "{peca["formato"]}" inválido')
    esperar_conteiner(conteiner, token)
    return chamar(f"/{usuario}/media_publish", {"creation_id": conteiner}, token)["id"]


def vencidas(fila, agora):
    pendentes = [p for p in fila if p["estado"] == "agendado" and datetime.fromisoformat(p["quando"]) <= agora]
    return sorted(pendentes, key=lambda p: p["quando"])


def rodar_fila(fila, agora, usuario, token, site):
    """Publica as vencidas e anota o resultado em cada peça. Devolve quantas falharam."""
    falhas = 0
    for peca in vencidas(fila, agora):
        print(f'- {peca["id"]} ({peca["quando"]})', flush=True)
        try:
            peca["media_id"] = publicar_peca(peca, usuario, token, site)
            peca["estado"] = "publicado"
            peca["publicado_em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            peca.pop("erro", None)
            print(f'  publicado: {peca["media_id"]}', flush=True)
        except (ErroPublicacao, urllib.error.URLError, KeyError) as e:
            falhas += 1
            peca["tentativas"] = peca.get("tentativas", 0) + 1
            peca["erro"] = str(e)[:500]
            if peca["tentativas"] >= MAX_TENTATIVAS:
                peca["estado"] = "erro"
            print(f"  ERRO ({peca['tentativas']} de {MAX_TENTATIVAS}): {e}", flush=True)
    return falhas


def main():
    fila = json.loads(FILA.read_text(encoding="utf-8"))
    falhas = rodar_fila(fila, datetime.now(timezone.utc), os.environ["IG_USER_ID"], os.environ["IG_TOKEN"], os.environ["SITE"])
    FILA.write_text(json.dumps(fila, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
