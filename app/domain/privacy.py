"""Aviso de privacidade padrão (RF02, SPEC seção 9). Puro, sem I/O.

Texto-base a validar com advogado (pendência humana registrada em docs/runbook.md).
Cada imobiliária recebe a versão 1 na criação; versões novas são imutáveis.
"""

from __future__ import annotations

import hashlib
from typing import Final

DEFAULT_NOTICE: Final = """\
Aviso de privacidade — atendimento por WhatsApp da {tenant}

Quem cuida dos seus dados: a {tenant} é a controladora. O atendimento automático é \
operado pela plataforma Rental Desk, contratada pela {tenant}.

O que usamos: o número de telefone, o nome do perfil do WhatsApp e as mensagens, áudios \
e imagens que você envia nesta conversa.

Para quê: responder ao seu pedido de hospedagem (datas, pessoas, casas e valores \
estimados) e passar a conversa a um corretor quando preciso. Base legal: procedimentos \
preliminares a um contrato, a seu pedido (LGPD, art. 7º, V). Métricas da imobiliária \
são agregadas e sem identificação (legítimo interesse).

Com quem compartilhamos, só para prestar o serviço: a Meta e o parceiro oficial do \
WhatsApp, o provedor de inteligência artificial (que recebe o texto sem seu nome ou \
telefone e não o usa para treino) e o provedor de transcrição de áudio. O servidor e o \
backup ficam no Brasil; a Meta e os provedores de inteligência artificial e de \
transcrição podem tratar os dados fora do Brasil, com as garantias da LGPD (art. 33), \
como cláusulas contratuais.

Por quanto tempo: o áudio é apagado depois de transcrito; nome e telefone saem das \
mensagens após 180 dias; registros de auditoria, sem conteúdo identificável, ficam pelo \
prazo legal.

Seus direitos: escreva PARAR para não receber mais mensagens. Para ver, corrigir, \
exportar ou apagar seus dados, peça nesta conversa ou à {tenant}; respondemos em até 15 dias.

Não envie dados de saúde, documentos ou dados de cartão por aqui.
"""


def default_notice(tenant_name: str) -> str:
    return DEFAULT_NOTICE.format(tenant=tenant_name)


def notice_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
