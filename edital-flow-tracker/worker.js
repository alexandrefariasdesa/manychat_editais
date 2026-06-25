/**
 * edital-flow-tracker — Cloudflare Worker
 * =============================================================================
 * Registra os eventos do funil de cada automação de EDITAL dentro do ManyChat
 * e grava UMA linha na aba `eventos_manychat` da planilha do dashboard.
 *
 * Funil medido (3 etapas), uma automação por edital:
 *   recebeu  → a pessoa recebeu o fluxo
 *   entrou   → clicou no botão de entrada (ex: "Verificar")
 *   engajou  → clicou no conteúdo (ex: "Ver edital")
 *
 * Cada etapa é um tijolo "External Request (POST)" no fluxo do ManyChat:
 *   POST https://<worker>/event?token=SECRET&edital=<slug>&etapa=<recebeu|entrou|engajou>
 *   body: Full Contact Data → traz { phone, id } (1 clique, sem montar JSON)
 *     └─ Worker → Google Sheets API (values:append) → aba eventos_manychat
 *
 * `edital` e `etapa` vão na QUERY (caminho fácil no ManyChat); o corpo fica só
 * com phone/id. Copie o mesmo bloco entre etapas trocando só o &etapa=, e entre
 * automações trocando só o &edital=.
 *
 * Auth Google: service account (JWT RS256 → access token). A planilha precisa
 * estar compartilhada com o e-mail do SA (SA_EMAIL) como Editor.
 *
 * Secrets / vars:
 *   SHEET_ID         — id da planilha nova (dos editais)
 *   SA_EMAIL         — client_email do service account
 *   SA_PRIVATE_KEY   — private_key do service account (PEM, com \n reais)
 *   SHARED_TOKEN     — segredo arbitrário; o ManyChat manda ?token=... igual
 *   TAB_NAME         — opcional, default "eventos_manychat"
 * =============================================================================
 */

const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const SCOPE = 'https://www.googleapis.com/auth/spreadsheets';

// As 3 etapas do funil. `edital` é livre (uma automação por edital), só validamos
// a etapa pra um typo não furar o funil.
const VALID_ETAPAS = new Set(['recebeu', 'entrou', 'engajou']);

export default {
  async fetch(request, env) {
    if (request.method === 'GET') {
      return json({ ok: true, service: 'edital-flow-tracker' });
    }
    if (request.method !== 'POST') {
      return json({ error: 'method not allowed' }, 405);
    }

    const url = new URL(request.url);
    const token = url.searchParams.get('token') || request.headers.get('x-token') || '';
    if (!env.SHARED_TOKEN || token !== env.SHARED_TOKEN) {
      return json({ error: 'unauthorized' }, 401);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: 'invalid json' }, 400);
    }

    // edital e etapa vêm na query; o corpo é só o Full Contact Data (phone/id).
    const edital = String(url.searchParams.get('edital') || body.edital || '')
      .trim()
      .toLowerCase();
    const etapa = String(url.searchParams.get('etapa') || body.etapa || '')
      .trim()
      .toLowerCase();
    const telefone = onlyDigits(body.telefone || body.phone || body.whatsapp_phone || '');
    const subscriberId = String(body.subscriber_id || body.user_id || body.id || '').trim();

    if (!edital) {
      return json({ error: 'edital obrigatório (?edital=...)' }, 400);
    }
    if (!VALID_ETAPAS.has(etapa)) {
      return json({ error: `etapa inválida: '${etapa}'`, validas: [...VALID_ETAPAS] }, 400);
    }
    if (!telefone && !subscriberId) {
      return json({ error: 'telefone ou subscriber_id obrigatório' }, 400);
    }

    const ts = manausIso(new Date());

    try {
      const accessToken = await getAccessToken(env);
      await appendRow(env, accessToken, [ts, telefone, subscriberId, edital, etapa]);
    } catch (err) {
      console.error('append failed:', err && err.stack ? err.stack : String(err));
      return json({ error: 'sheets append failed', detail: String(err) }, 502);
    }

    return json({ ok: true, edital, etapa, ts });
  },
};

// ── Google Sheets append ──────────────────────────────────────────────────────

async function appendRow(env, accessToken, row) {
  const tab = env.TAB_NAME || 'eventos_manychat';
  const range = encodeURIComponent(`${tab}!A:E`);
  const endpoint =
    `https://sheets.googleapis.com/v4/spreadsheets/${env.SHEET_ID}` +
    `/values/${range}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS`;

  const resp = await fetch(endpoint, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${accessToken}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({ values: [row] }),
  });

  if (!resp.ok) {
    throw new Error(`sheets ${resp.status}: ${await resp.text()}`);
  }
}

// ── OAuth: service account JWT (RS256) → access token ────────────────────────

async function getAccessToken(env) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'RS256', typ: 'JWT' };
  const claim = {
    iss: env.SA_EMAIL,
    scope: SCOPE,
    aud: TOKEN_URL,
    iat: now,
    exp: now + 3600,
  };

  const unsigned = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(claim))}`;
  const key = await importPrivateKey(env.SA_PRIVATE_KEY);
  const sigBuf = await crypto.subtle.sign(
    'RSASSA-PKCS1-v1_5',
    key,
    new TextEncoder().encode(unsigned),
  );
  const jwt = `${unsigned}.${b64urlBytes(new Uint8Array(sigBuf))}`;

  const resp = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
      assertion: jwt,
    }),
  });
  if (!resp.ok) {
    throw new Error(`token ${resp.status}: ${await resp.text()}`);
  }
  return (await resp.json()).access_token;
}

async function importPrivateKey(pem) {
  const clean = pem
    .replace(/\\n/g, '\n')
    .replace('-----BEGIN PRIVATE KEY-----', '')
    .replace('-----END PRIVATE KEY-----', '')
    .replace(/\s/g, '');
  const der = Uint8Array.from(atob(clean), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey(
    'pkcs8',
    der.buffer,
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['sign'],
  );
}

// ── helpers ──────────────────────────────────────────────────────────────────

function onlyDigits(v) {
  return String(v || '').replace(/\D/g, '');
}

/** ISO 8601 no fuso America/Manaus (UTC-4 fixo). Ex: 2026-06-22T09:15:03-04:00 */
function manausIso(date) {
  const m = new Date(date.getTime() - 4 * 3600 * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return (
    `${m.getUTCFullYear()}-${p(m.getUTCMonth() + 1)}-${p(m.getUTCDate())}` +
    `T${p(m.getUTCHours())}:${p(m.getUTCMinutes())}:${p(m.getUTCSeconds())}-04:00`
  );
}

function b64url(str) {
  return b64urlBytes(new TextEncoder().encode(str));
}

function b64urlBytes(bytes) {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}
