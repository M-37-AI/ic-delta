/*
 * treiber-openrouter.mjs – derselbe Agentenkern über OpenRouter (OpenAI-kompatible Chat Completions mit Tools).
 *
 * Für alle, die kein Claude-Code-Abo haben: Schlüssel in ~/.config/marktdaten/ki.env als OPENROUTER_KEY=...
 * (legt der Nutzer selbst an, der Server liest ihn nur). Modell frei wählbar (z. B. "anthropic/claude-sonnet-4.5",
 * "openai/gpt-5", "google/gemini-2.5-pro"); die Werkzeuge aus werkzeuge.mjs werden als JSON-Schema übergeben.
 * Gespräche bleiben im Speicher des Servers (je Sitzung eine Nachrichtenliste, auf 60 Einträge begrenzt).
 */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { z } from 'zod';

const KI_ENV = path.join(os.homedir(), '.config', 'marktdaten', 'ki.env');
const URL_ = 'https://openrouter.ai/api/v1/chat/completions';
const MAX_RUNDEN = 14;

export function openrouterSchluessel() {
  if (process.env.OPENROUTER_KEY) return process.env.OPENROUTER_KEY;
  try {
    for (const z of fs.readFileSync(KI_ENV, 'utf8').split('\n')) {
      const m = /^\s*OPENROUTER_KEY\s*=\s*(.+?)\s*$/.exec(z); if (m) return m[1].replace(/^["']|["']$/g, '');
    }
  } catch (e) { /* keine Datei */ }
  return '';
}

function schemaVon(w) {
  const s = z.toJSONSchema(z.object(w.schema || {}));
  delete s.$schema;
  return { type: 'function', function: { name: w.name, description: w.beschreibung.slice(0, 1024), parameters: s } };
}

// Streamt eine Antwort; liefert {text, toolCalls, usage}
async function anfrage({ key, modell, nachrichten, tools, onEvent, signal }) {
  const r = await fetch(URL_, {
    method: 'POST', signal,
    headers: { Authorization: 'Bearer ' + key, 'Content-Type': 'application/json', 'HTTP-Referer': 'https://github.com/M-37-AI/ic-charts', 'X-Title': 'iC Delta' },
    body: JSON.stringify({ model: modell, messages: nachrichten, tools, tool_choice: 'auto', stream: true, usage: { include: true } }),
  });
  if (!r.ok) throw new Error(`OpenRouter antwortet ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const dec = new TextDecoder(); let rest = '', text = '', usage = null; const calls = new Map();
  for await (const chunk of r.body) {
    rest += dec.decode(chunk, { stream: true });
    const zeilen = rest.split('\n'); rest = zeilen.pop();
    for (const z of zeilen) {
      if (!z.startsWith('data:')) continue;
      const d = z.slice(5).trim(); if (!d || d === '[DONE]') continue;
      let j; try { j = JSON.parse(d); } catch (e) { continue; }
      if (j.usage) usage = j.usage;
      const delta = j.choices?.[0]?.delta; if (!delta) continue;
      if (delta.content) { text += delta.content; onEvent?.({ type: 'text_delta', text: delta.content }); }
      for (const tc of delta.tool_calls || []) {
        const c = calls.get(tc.index) || { id: tc.id, name: '', args: '' };
        if (tc.id) c.id = tc.id; if (tc.function?.name) c.name += tc.function.name; if (tc.function?.arguments) c.args += tc.function.arguments;
        calls.set(tc.index, c);
      }
    }
  }
  if (text) onEvent?.({ type: 'assistant_done' });
  return { text, toolCalls: [...calls.values()], usage };
}

export async function openrouterLauf({ key, modell, system, nachrichten, text, werkzeuge, onEvent, signal }) {
  const tools = werkzeuge.map(schemaVon);
  const byName = Object.fromEntries(werkzeuge.map((w) => [w.name, w]));
  if (!nachrichten.length || nachrichten[0].role !== 'system') nachrichten.unshift({ role: 'system', content: system });
  else nachrichten[0].content = system;
  nachrichten.push({ role: 'user', content: text });
  let antwort = '', kosten = 0, zuege = 0;
  for (let runde = 0; runde < MAX_RUNDEN; runde++) {
    const r = await anfrage({ key, modell, nachrichten, tools, onEvent, signal });
    zuege++;
    if (r.usage?.cost) kosten += r.usage.cost;
    if (!r.toolCalls.length) { antwort = r.text; nachrichten.push({ role: 'assistant', content: r.text }); break; }
    nachrichten.push({ role: 'assistant', content: r.text || null, tool_calls: r.toolCalls.map((c) => ({ id: c.id, type: 'function', function: { name: c.name, arguments: c.args || '{}' } })) });
    for (const c of r.toolCalls) {
      let input = {}; try { input = JSON.parse(c.args || '{}'); } catch (e) { /* leere Argumente */ }
      const w = byName[c.name];
      let inhalt;
      if (!w) inhalt = 'Fehler: unbekanntes Werkzeug ' + c.name;
      else { const res = await w.handler(input); inhalt = res.bild ? res.text + ' (Bild kann über OpenRouter nicht übergeben werden)' : res.text; }
      nachrichten.push({ role: 'tool', tool_call_id: c.id, content: String(inhalt).slice(0, 120000) });
    }
    if (signal?.aborted) throw new Error('Abgebrochen.');
  }
  // Gespräch begrenzen: System + die letzten 60 Nachrichten
  if (nachrichten.length > 61) nachrichten.splice(1, nachrichten.length - 61);
  return { text: antwort.trim(), kosten, zuege };
}
