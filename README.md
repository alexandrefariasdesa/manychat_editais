# ManyChat Editais — Funil de Efetividade

Mede, por **edital**, o funil de 3 etapas dentro do ManyChat:

```
recebeu  →  entrou  →  engajou
```

- **recebeu** — a pessoa recebeu o fluxo do edital
- **entrou** — clicou no botão de entrada (ex: "Verificar")
- **engajou** — clicou no conteúdo (ex: "Ver edital")

Tudo de dentro do ManyChat — sem conversão de venda, sem cruzar com outras planilhas.

## Arquitetura

```
ManyChat (1 automação por edital)
  ├─ External Request → ?edital=<slug>&etapa=recebeu   (início do fluxo)
  ├─ botão "Verificar" → External Request → &etapa=entrou
  └─ botão "Ver edital" → External Request → &etapa=engajou
        └─ Cloudflare Worker (edital-flow-tracker)
              └─ Google Sheets · aba `eventos_manychat`
                    └─ Dashboard Streamlit (app.py)
```

Aba `eventos_manychat` — header: `ts | telefone | subscriber_id | edital | etapa`

## Setup

### 1. Planilha (Google)
1. Crie uma planilha nova na sua conta Google com a aba **`eventos_manychat`** e o header acima.
2. Compartilhe como **Editor** com o service account (reusado do recovery):
   `recovery-dashboard@atomic-quasar-379600.iam.gserviceaccount.com`
3. Pegue o **Spreadsheet ID** (trecho da URL entre `/d/` e `/edit`).

### 2. Worker (Cloudflare)
```bash
cd edital-flow-tracker
cp .dev.vars.example .dev.vars     # preencha SHEET_ID e SHARED_TOKEN
npx wrangler login
bash deploy.sh                      # cria o worker e injeta os secrets
```
Anote a URL: `https://edital-flow-tracker.<subdominio>.workers.dev`

### 3. ManyChat (em cada automação de edital)
Adicione 3 tijolos de **External Request (POST)** apontando pro worker. Em todos:
`?token=<SHARED_TOKEN>&edital=<slug>&etapa=<etapa>` na URL, corpo = **Full Contact Data**.

| Onde | etapa |
|------|-------|
| início do fluxo | `recebeu` |
| no clique do botão "Verificar" | `entrou` |
| no clique do botão "Ver edital" | `engajou` |

Copie o mesmo bloco trocando só `&etapa=` entre as etapas e `&edital=` entre automações.
**Erro comum:** esquecer de trocar o `&edital=` ao duplicar uma automação.

### 4. Dashboard
```bash
cp .env  # já vem com placeholder; cole o SPREADSHEET_ID
pip install -r requirements.txt
streamlit run app.py
```

## Notas
- **Pessoas distintas** por etapa = chave `subscriber_id` (fallback telefone canônico).
  Clicar 2x não dobra a contagem.
- Worker e dashboard reusam o **mesmo service account** do recovery_dashboard — o SA é
  só uma identidade de leitura/escrita; o isolamento entre projetos é a **planilha** (arquivo
  separado) + worker separado + conta ManyChat separada.
- `tipo` de fuso: timestamps gravados em America/Manaus (UTC-4).
