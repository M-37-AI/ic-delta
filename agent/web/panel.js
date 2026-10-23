/*
 * panel.js – Claude-Panel in der Dashboard-Hülle von iC Delta.
 *
 * Wird vom Agenten-Server (Port 8814) ausgeliefert und mit einer Script-Zeile in dashboard/seite.html geladen.
 * Spricht per WebSocket mit dem Agenten-Server (Raum "dashboard"). Dashboard-Werkzeuge (sehen, navigieren,
 * hervorheben) laufen hier; Chart-Werkzeuge werden an iC Charts weitergereicht (iframe gleicher Herkunft,
 * window.__agent.TOOLS aus agent-panel.js). Der Reiter Analysen (iframe von Port 8813) wird per postMessage
 * gesteuert und meldet seinen Kontext zurück. Fehlt der Agenten-Server, fehlt nur dieses Panel.
 */
(() => {
  'use strict';
  if (window.__kiPanel) return;
  const HOST = location.hostname || 'localhost';
  const BASIS = +location.port || 8812;                                 // Dashboard-Port; Analysen +1, Agent +2 (echt 8812, Demo 8822)
  const AGENT = `${location.protocol}//${HOST}:${BASIS + 2}`;
  const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${HOST}:${BASIS + 2}/agent?raum=dashboard`;
  const ANALYSEN = `${location.protocol}//${HOST}:${BASIS + 1}`;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const SCHNELL = ['Was ist heute auffällig?', 'Erklär mir diese Seite.', 'Welche Formationen gibt es bei SAP auf 1h?', 'Bau mir eine Analyse: Halten Wände bei SAP den Kurs?', 'Wie ist deine Trefferquote?'];

  // ---------------- Gestaltung (nutzt die Marken des Dashboards) ----------------
  const css = document.createElement('style');
  css.textContent = `
  :root { --ki-breite: 440px; }
  #ki-knopf { position: fixed; right: 22px; bottom: 22px; z-index: 900; display: inline-flex; align-items: center; gap: 9px; padding: 11px 18px 11px 14px;
    font: 600 13px/1 -apple-system, BlinkMacSystemFont, system-ui, sans-serif; letter-spacing: .01em; color: var(--ton-1); cursor: pointer;
    border: 1px solid color-mix(in srgb, var(--akzent) 38%, transparent); border-radius: 14px;
    background: linear-gradient(120deg, color-mix(in srgb, var(--akzent) 24%, var(--karte)), var(--karte) 75%); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .07), 0 10px 28px rgb(0 0 0 / .35), 0 0 22px color-mix(in srgb, var(--akzent) 16%, transparent);
    transition: border-color .2s, box-shadow .2s, transform .2s; }
  #ki-knopf .ki-stern { color: var(--akzent); font-size: 15px; text-shadow: 0 0 8px color-mix(in srgb, var(--akzent) 70%, transparent); }
  #ki-knopf:hover { transform: translateY(-1px); border-color: color-mix(in srgb, var(--akzent) 65%, transparent);
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .09), 0 12px 32px rgb(0 0 0 / .4), 0 0 28px color-mix(in srgb, var(--akzent) 26%, transparent); }
  body.ki-offen #ki-knopf { display: none; }
  body.ki-offen .gestell { margin-right: var(--ki-breite); }
  .gestell { transition: margin-right .25s ease; }
  #ki-panel { position: fixed; top: 0; right: 0; height: 100vh; width: var(--ki-breite); z-index: 950; display: flex; flex-direction: column;
    background: var(--karte); border-left: 1px solid var(--linie); color: var(--ton-1); font: 400 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; -webkit-font-smoothing: antialiased; }
  #ki-panel[hidden] { display: none; }
  #ki-kopf { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--linie); }
  #ki-kopf .ki-titel { font-weight: 600; font-size: 13.5px; }
  #ki-kopf .ki-titel small { color: var(--ton-3); font-weight: 400; margin-left: 6px; font-size: 11px; }
  #ki-kopf .ki-status { margin-left: auto; display: flex; align-items: center; gap: 6px; color: var(--ton-3); font-size: 11px; }
  #ki-kopf .ki-status i { width: 7px; height: 7px; border-radius: 50%; background: var(--ton-3); display: inline-block; }
  #ki-kopf .ki-status.ki-live i { background: var(--gut); } #ki-kopf .ki-status.ki-off i { background: var(--kritisch); }
  #ki-kopf button { border: 0; background: none; color: var(--ton-2); font: inherit; font-size: 15px; cursor: pointer; padding: 2px 5px; border-radius: 6px; }
  #ki-kopf button:hover { background: var(--karte-tief); color: var(--ton-1); }
  #ki-lauf { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
  .ki-msg { max-width: 100%; padding: 9px 12px; border-radius: 12px; font-size: 13px; line-height: 1.5; word-wrap: break-word; }
  .ki-msg.user { align-self: flex-end; background: var(--akzent-weich); color: var(--ton-1); border-bottom-right-radius: 4px; max-width: 88%; }
  .ki-msg.assistant { align-self: stretch; background: var(--karte-tief); border-bottom-left-radius: 4px; }
  .ki-msg.error { align-self: stretch; background: var(--kritisch-weich); color: var(--ton-1); font-size: 12px; }
  .ki-msg p { margin: 0 0 6px; } .ki-msg p:last-child { margin: 0; } .ki-msg ul, .ki-msg ol { margin: 2px 0 6px; padding-left: 18px; } .ki-msg li { margin: 1px 0; }
  .ki-msg code { font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; background: var(--karte); border: 1px solid var(--linie); border-radius: 4px; padding: 0 4px; }
  .ki-msg pre { background: var(--karte); border: 1px solid var(--linie); border-radius: 6px; padding: 8px 10px; overflow-x: auto; font-size: 11.5px; margin: 4px 0 6px; } .ki-msg pre code { border: 0; padding: 0; background: none; }
  .ki-msg b { font-weight: 600; }
  .ki-schritt { align-self: flex-start; display: inline-flex; align-items: center; gap: 7px; font-size: 11.5px; color: var(--ton-2); padding: 2px 10px 2px 8px; border: 1px solid var(--linie); border-radius: 999px; background: var(--karte); }
  .ki-schritt i { width: 6px; height: 6px; border-radius: 50%; background: var(--warnung); flex: none; }
  .ki-schritt.done i { background: var(--gut); } .ki-schritt.err i { background: var(--kritisch); } .ki-schritt.run i { animation: ki-puls 1s infinite; }
  .ki-schritt span { color: var(--ton-3); font-family: ui-monospace, Menlo, monospace; font-size: 10.5px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @keyframes ki-puls { 50% { opacity: .3; } }
  #ki-heute { padding: 0 14px; flex: none; max-height: 55vh; overflow-y: auto; overscroll-behavior: contain; }
  #ki-heute .ki-mehr { position: sticky; bottom: 0; background: var(--karte-tief); width: 100%; text-align: left; padding: 4px 0 2px; }
  #ki-lauf { min-height: 0; }
  .ki-chips { flex: none; }
  .ki-karte { background: var(--karte-tief); border: 1px solid var(--linie); border-radius: 10px; padding: 10px 12px; margin-top: 12px; font-size: 12.5px; }
  .ki-karte .ki-ueber { color: var(--ton-3); font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; margin-bottom: 4px; display: flex; gap: 8px; }
  .ki-karte .ki-ueber span:last-child { margin-left: auto; text-transform: none; letter-spacing: 0; }
  .ki-karte.zu .ki-inhalt { max-height: 110px; overflow: hidden; -webkit-mask-image: linear-gradient(#000 60%, transparent); mask-image: linear-gradient(#000 60%, transparent); }
  .ki-karte .ki-mehr { border: 0; background: none; color: var(--akzent); font: inherit; font-size: 11.5px; cursor: pointer; padding: 2px 0 0; }
  .ki-chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 14px 0; }
  .ki-chips button { font: inherit; font-size: 12px; color: var(--ton-2); background: var(--karte); border: 1px solid var(--linie); border-radius: 999px; padding: 5px 11px; cursor: pointer; text-align: left; }
  .ki-chips button:hover { border-color: var(--linie-stark); color: var(--ton-1); }
  #ki-fuss { border-top: 1px solid var(--linie); padding: 8px 14px 12px; }
  #ki-kosten { color: var(--ton-3); font-size: 10.5px; min-height: 14px; margin-bottom: 6px; font-variant-numeric: tabular-nums; }
  .ki-eingabe { display: flex; gap: 8px; align-items: flex-end; }
  .ki-eingabe textarea { flex: 1; resize: none; font: inherit; font-size: 13px; color: var(--ton-1); background: var(--karte-tief); border: 1px solid var(--linie); border-radius: 10px; padding: 8px 10px; max-height: 140px; }
  .ki-eingabe textarea:focus { outline: none; border-color: var(--akzent); }
  .ki-eingabe button { border: 0; border-radius: 10px; padding: 9px 14px; font: 600 12.5px/1 inherit; font-family: inherit; background: var(--akzent); color: var(--karte); cursor: pointer; }
  .ki-eingabe button#ki-stopp { background: var(--kritisch); }
  .ki-eingabe button[disabled] { opacity: .5; cursor: default; }
  #ki-einst { padding: 12px 14px; font-size: 12.5px; border-bottom: 1px solid var(--linie); display: grid; gap: 8px; }
  #ki-einst[hidden] { display: none; }
  #ki-einst label { display: grid; grid-template-columns: 130px 1fr; align-items: center; gap: 8px; color: var(--ton-2); font-size: 12px; }
  #ki-einst input[type=text], #ki-einst select, #ki-einst input[type=time] { font: inherit; font-size: 12.5px; color: var(--ton-1); background: var(--karte-tief); border: 1px solid var(--linie); border-radius: 6px; padding: 5px 8px; }
  #ki-einst .ki-reihe { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  #ki-einst .ki-reihe button { font: inherit; font-size: 12px; color: var(--ton-2); background: var(--karte); border: 1px solid var(--linie); border-radius: 999px; padding: 5px 12px; cursor: pointer; }
  #ki-einst .ki-hinweis { color: var(--ton-3); font-size: 11px; }
  .ki-fragen { order: 3; flex: none; margin-left: 4px; border: 1px solid var(--linie); background: var(--karte); color: var(--ton-3); border-radius: 999px; font: inherit; font-size: 10.5px; padding: 2px 9px; cursor: pointer; }
  .ki-fragen:hover { color: var(--akzent); border-color: var(--akzent); }
  .ki-hervor { outline: 2px solid var(--akzent) !important; outline-offset: 3px; border-radius: 9px; animation: ki-hervor 1.2s ease-in-out 3; }
  @keyframes ki-hervor { 50% { outline-color: transparent; } }
  @media (max-width: 1100px) { body.ki-offen .gestell { margin-right: 0; } #ki-panel { width: min(100vw, 480px); box-shadow: -8px 0 30px rgb(0 0 0 / .25); } }

  /* ===== Gestaltung (21.10.2026): Glas, blaues Licht, klare Kopfzeile, Eingabe als eine Fläche – Grün bleibt den Kursen ===== */
  #ki-panel { background: radial-gradient(460px 280px at 100% -4%, color-mix(in srgb, var(--akzent) 14%, transparent), transparent 70%),
    linear-gradient(180deg, rgb(255 255 255 / .02), transparent 35%), color-mix(in srgb, var(--flaeche) 88%, transparent);
    backdrop-filter: blur(22px) saturate(1.2); -webkit-backdrop-filter: blur(22px) saturate(1.2);
    border-left: 1px solid rgb(255 255 255 / .07); box-shadow: -18px 0 50px rgb(0 0 0 / .35); }
  #ki-kopf { gap: 11px; padding: 14px 14px 14px 16px; border-bottom: 1px solid var(--linie); }
  .ki-avatar { width: 34px; height: 34px; flex: none; border-radius: 11px; display: grid; place-items: center; font-size: 15px; color: #fff;
    background: linear-gradient(135deg, #63bdf7, #2a83d6); box-shadow: inset 0 1px 0 rgb(255 255 255 / .25), 0 4px 14px color-mix(in srgb, var(--akzent) 32%, transparent); }
  .ki-titelblock { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  #ki-kopf .ki-titel { font-size: 14px; font-weight: 650; letter-spacing: -.01em; line-height: 1.25; }
  #ki-kopf #ki-modell { color: var(--ton-3); font-size: 11px; line-height: 1.3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #ki-kopf .ki-status { margin-left: auto; flex: none; gap: 7px; padding: 4px 10px 4px 9px; border-radius: 999px; font-size: 11px; color: var(--ton-2);
    background: rgb(255 255 255 / .04); border: 1px solid var(--linie); white-space: nowrap; }
  #ki-kopf .ki-status i { width: 6px; height: 6px; }
  #ki-kopf .ki-status.ki-live i { background: var(--akzent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--akzent) 18%, transparent), 0 0 8px var(--akzent); }
  .ki-aktionen { display: flex; gap: 2px; flex: none; }
  #ki-kopf .ki-aktionen button { width: 30px; height: 30px; padding: 0; display: grid; place-items: center; border-radius: 9px; font-size: 14px; color: var(--ton-3); }
  #ki-kopf .ki-aktionen button:hover { background: rgb(255 255 255 / .06); color: var(--ton-1); }
  #ki-heute { padding: 0 16px; }
  .ki-karte { background: linear-gradient(180deg, rgb(255 255 255 / .035), rgb(255 255 255 / .012)); border: 1px solid rgb(255 255 255 / .07); border-radius: 14px;
    padding: 12px 14px 10px; margin-top: 14px; box-shadow: inset 0 1px 0 rgb(255 255 255 / .04); }
  .ki-karte .ki-ueber { margin-bottom: 6px; }
  #ki-heute .ki-mehr { background: none; }
  .ki-chips { padding: 16px 16px 0; gap: 7px; }
  .ki-chips-titel { width: 100%; font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--ton-3); margin-bottom: 1px; }
  .ki-chips button { background: rgb(255 255 255 / .03); border: 1px solid rgb(255 255 255 / .08); border-radius: 12px; padding: 7px 12px; color: var(--ton-2);
    transition: border-color .15s, background .15s, color .15s; }
  .ki-chips button:hover { border-color: color-mix(in srgb, var(--akzent) 45%, transparent); background: color-mix(in srgb, var(--akzent) 10%, transparent); color: var(--ton-1); }
  #ki-lauf { padding: 16px; gap: 12px; }
  .ki-msg { font-size: 13.2px; line-height: 1.6; }
  .ki-msg.user { background: linear-gradient(135deg, color-mix(in srgb, var(--akzent) 32%, var(--karte-tief)), color-mix(in srgb, var(--akzent) 16%, var(--karte-tief)));
    border: 1px solid color-mix(in srgb, var(--akzent) 28%, transparent); border-radius: 16px 16px 4px 16px; padding: 9px 13px; }
  .ki-msg.assistant { background: rgb(255 255 255 / .03); border: 1px solid rgb(255 255 255 / .06); border-radius: 16px 16px 16px 4px; padding: 11px 14px;
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .03); }
  .ki-msg.error { border-radius: 12px; }
  .ki-schritt { background: rgb(255 255 255 / .03); border-color: rgb(255 255 255 / .07); }
  .ki-schritt i, .ki-schritt.done i { background: var(--akzent); }
  .ki-schritt.done i { opacity: .55; } .ki-schritt.run i { box-shadow: 0 0 6px var(--akzent); }
  #ki-fuss { border-top: 0; padding: 10px 16px 14px; }
  .ki-eingabe { align-items: flex-end; gap: 4px; background: rgb(255 255 255 / .035); border: 1px solid rgb(255 255 255 / .09); border-radius: 16px;
    padding: 5px 5px 5px 3px; transition: border-color .2s, box-shadow .2s; }
  .ki-eingabe:focus-within { border-color: color-mix(in srgb, var(--akzent) 60%, transparent);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--akzent) 12%, transparent), 0 0 24px color-mix(in srgb, var(--akzent) 10%, transparent); }
  .ki-eingabe textarea, .ki-eingabe textarea:focus { background: transparent; border: 0; padding: 8px 10px; font-size: 13.5px; }
  .ki-eingabe button { width: 36px; height: 36px; padding: 0; display: grid; place-items: center; border-radius: 11px; flex: none; color: #fff;
    background: linear-gradient(135deg, #63bdf7, #2a83d6); box-shadow: inset 0 1px 0 rgb(255 255 255 / .25), 0 4px 12px color-mix(in srgb, var(--akzent) 28%, transparent); }
  .ki-eingabe button svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
  .ki-eingabe button#ki-stopp { background: var(--kritisch); box-shadow: none; } .ki-eingabe button#ki-stopp svg { fill: currentColor; stroke: none; }
  .ki-eingabe button[hidden] { display: none; }
  #ki-kosten { margin: 7px 4px 0; min-height: 13px; }
  /* Einstellungen: Gruppen als Glas-Karten, Felder untereinander, Schalter statt Häkchen */
  #ki-einst { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px 16px; flex: 1 1 auto; min-height: 0; overflow-y: auto; border-bottom: 1px solid var(--linie);
    background: linear-gradient(180deg, rgb(255 255 255 / .015), transparent); }
  #ki-einst[hidden] { display: none; }
  .ke-gruppe { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px 13px; border-radius: 14px; background: rgb(255 255 255 / .03);
    border: 1px solid rgb(255 255 255 / .07); box-shadow: inset 0 1px 0 rgb(255 255 255 / .04); }
  .ke-titel { font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--ton-3); }
  .ke-zwei { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  #ki-einst .ke-feld { display: flex; flex-direction: column; align-items: stretch; gap: 5px; min-width: 0; text-align: left; grid-template-columns: none; }
  /* Offene Einstellungen bekommen das ganze Panel; Morgenlage, Vorschläge und Verlauf kommen beim Schließen zurück */
  #ki-panel:has(#ki-einst:not([hidden])) :is(#ki-heute, .ki-chips, #ki-lauf, #ki-fuss) { display: none; }
  #ki-einst .ke-feld > span { font-size: 11.5px; color: var(--ton-2); }
  #ki-einst .ke-feld select, #ki-einst .ke-feld input[type=time] { width: 100%; min-width: 0; box-sizing: border-box; font: inherit; font-size: 12.5px; color: var(--ton-1);
    background: rgb(255 255 255 / .04); border: 1px solid rgb(255 255 255 / .09); border-radius: 10px; padding: 8px 10px; text-overflow: ellipsis; }
  #ki-einst .ke-feld select:focus, #ki-einst .ke-feld input:focus { outline: none; border-color: color-mix(in srgb, var(--akzent) 60%, transparent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--akzent) 12%, transparent); }
  #ki-einst .ke-feld input[type=time] { font-variant-numeric: tabular-nums; }
  #ki-einst .ke-schalter { display: flex; align-items: center; justify-content: space-between; gap: 14px; cursor: pointer; grid-template-columns: none; }
  .ke-schalter > span { display: flex; flex-direction: column; gap: 1px; text-align: left; }
  .ke-schalter b { font-weight: 600; font-size: 12.5px; color: var(--ton-1); }
  .ke-schalter small { font-size: 11.5px; color: var(--ton-3); }
  .ke-schalter input { appearance: none; -webkit-appearance: none; flex: none; width: 36px; height: 21px; margin: 0; border-radius: 999px; position: relative; cursor: pointer;
    background: rgb(255 255 255 / .1); border: 1px solid rgb(255 255 255 / .08); transition: background .2s, box-shadow .2s; }
  .ke-schalter input::after { content: ""; position: absolute; top: 2px; left: 2px; width: 15px; height: 15px; border-radius: 50%; background: #fff;
    box-shadow: 0 1px 3px rgb(0 0 0 / .35); transition: transform .2s; }
  .ke-schalter input:checked { background: linear-gradient(135deg, #63bdf7, #2a83d6); box-shadow: 0 0 10px color-mix(in srgb, var(--akzent) 35%, transparent); }
  .ke-schalter input:checked::after { transform: translateX(15px); }
  #ki-einst .ki-hinweis { font-size: 11px; line-height: 1.5; color: var(--ton-3); }
  #ki-einst .ki-hinweis code { font-size: 10.5px; background: rgb(255 255 255 / .05); border: 1px solid rgb(255 255 255 / .07); border-radius: 5px; padding: 0 4px; }
  .ke-da { color: var(--akzent); font-weight: 600; } .ke-fehlt { color: var(--warnung); font-weight: 600; }
  .ke-knoepfe { display: flex; flex-wrap: wrap; gap: 8px; }
  .ke-knoepfe button { font: inherit; font-size: 12px; color: var(--ton-2); background: rgb(255 255 255 / .035); border: 1px solid rgb(255 255 255 / .09);
    border-radius: 11px; padding: 8px 13px; cursor: pointer; transition: border-color .15s, color .15s; }
  .ke-knoepfe button:hover { border-color: color-mix(in srgb, var(--akzent) 45%, transparent); color: var(--ton-1); }
  .ke-knoepfe .ke-haupt { color: #fff; font-weight: 600; border: 0; background: linear-gradient(135deg, #63bdf7, #2a83d6);
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .25), 0 4px 12px color-mix(in srgb, var(--akzent) 25%, transparent); }
  .ke-knoepfe button:disabled { opacity: .5; cursor: default; }
  #ke-meldung:empty { display: none; }
  :root[data-thema="hell"] #ki-panel { background: var(--karte); backdrop-filter: none; box-shadow: -12px 0 32px rgb(0 0 0 / .08); }
  @media (prefers-color-scheme: light) { :root:not([data-thema="dunkel"]) #ki-panel { background: var(--karte); backdrop-filter: none; box-shadow: -12px 0 32px rgb(0 0 0 / .08); } }`;
  document.head.appendChild(css);

  // ---------------- Aufbau ----------------
  const knopf = document.createElement('button'); knopf.id = 'ki-knopf'; knopf.innerHTML = '<span class="ki-stern">✦</span>AI-Agent'; knopf.title = 'AI-Agent fragen (⌘K) · Claude';
  const panel = document.createElement('aside'); panel.id = 'ki-panel'; panel.hidden = true; panel.setAttribute('aria-label', 'AI-Agent');
  panel.innerHTML = `
    <div id="ki-kopf"><span class="ki-avatar" aria-hidden="true">✦</span>
      <div class="ki-titelblock"><span class="ki-titel">AI-Agent</span><small id="ki-modell"></small></div>
      <span class="ki-status" id="ki-status"><i></i><span>…</span></span>
      <div class="ki-aktionen"><button id="ki-neu" title="Neues Gespräch">⟳</button><button id="ki-zahnrad" title="Einstellungen">⚙</button><button id="ki-zu" title="Schließen (Esc)">×</button></div></div>
    <div id="ki-einst" hidden></div>
    <div id="ki-heute"></div>
    <div id="ki-lauf"></div>
    <div id="ki-fuss"><div class="ki-eingabe"><textarea id="ki-text" rows="2" placeholder="Frag den AI-Agent zu Kursen, Buch, Formationen, Alarmen …"></textarea>
      <button id="ki-senden" title="Senden (Enter)" aria-label="Senden"><svg viewBox="0 0 24 24"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/></svg></button>
      <button id="ki-stopp" hidden title="Antwort abbrechen" aria-label="Stopp"><svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"/></svg></button></div>
      <div id="ki-kosten"></div></div>`;
  document.body.append(knopf, panel);
  const lauf = $('#ki-lauf'), text = $('#ki-text');

  // ---------------- Kleines Markdown ----------------
  function md(src) {
    const parts = String(src).split(/```/); let out = '';
    parts.forEach((p, i) => {
      if (i % 2) { out += `<pre><code>${esc(p.replace(/^\w*\n/, ''))}</code></pre>`; return; }
      let h = esc(p).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/^#{1,6}\s*(.+)$/gm, '<b>$1</b>');
      h = h.split(/\n{2,}/).map((para) => {
        if (/^(\s*[-*•] .*\n?)+$/.test(para)) return '<ul>' + para.trim().split('\n').map((l) => `<li>${l.replace(/^\s*[-*•] /, '')}</li>`).join('') + '</ul>';
        if (/^(\s*\d+[.)] .*\n?)+$/.test(para)) return '<ol>' + para.trim().split('\n').map((l) => `<li>${l.replace(/^\s*\d+[.)] /, '')}</li>`).join('') + '</ol>';
        return `<p>${para.replace(/\n/g, '<br>')}</p>`;
      }).join('');
      out += h;
    });
    return out;
  }
  function blase(rolle, html) { const d = document.createElement('div'); d.className = 'ki-msg ' + rolle; d.innerHTML = html; lauf.appendChild(d); lauf.scrollTop = lauf.scrollHeight; return d; }
  function schritt(name, label, input, zustand = 'run') {
    const d = document.createElement('div'); d.className = 'ki-schritt ' + zustand;
    const arg = input && Object.keys(input).length ? esc(Object.values(input).filter((v) => typeof v !== 'object').join(' · ')).slice(0, 80) : '';
    d.innerHTML = `<i></i><b>${esc(label || name)}</b><span>${arg}</span>`; lauf.appendChild(d); lauf.scrollTop = lauf.scrollHeight; return d;
  }
  let cur = null, curText = '';
  function anhaengen(t) { if (!cur) { cur = blase('assistant', ''); curText = ''; } curText += t; cur.innerHTML = md(curText); lauf.scrollTop = lauf.scrollHeight; }
  function fertig() { cur = null; curText = ''; }

  // ---------------- Kontext: was Niko sieht ----------------
  let analysenKontext = null, letzteKarte = null, watch = null;
  const reiter = () => ($('#reiter button[aria-selected="true"]') || {}).dataset?.ziel || location.hash.slice(1) || 'kurse';
  const chartAgent = () => { try { return $('#icharts-rahmen')?.contentWindow?.__agent || null; } catch (e) { return null; } };
  const chartState = () => { try { return chartAgent()?.TOOLS.get_state() || null; } catch (e) { return null; } };
  async function watchlist() { if (!watch) { try { watch = (await (await fetch('/api/watchlist')).json()).werte || []; } catch (e) { watch = []; } setTimeout(() => { watch = null; }, 60000); } return watch; }
  async function wertAufloesen(x) {
    const w = await watchlist(), s = String(x || '').trim().toUpperCase();
    return w.find((v) => v.id === s || v.symbol === s || v.name.toUpperCase() === s) || w.find((v) => v.name.toUpperCase().includes(s) && s.length > 2) || null;
  }
  function sichtbarText() {
    const s = $('main > section:not([hidden])'); if (!s || s.querySelector('iframe')) return '';
    return s.innerText.replace(/\s+/g, ' ').trim().slice(0, 1500);
  }
  function kontext() {
    const r = reiter(), k = { reiter: r, thema: document.documentElement.dataset.thema || 'system' };
    if (r === 'analysen' && analysenKontext) k.analysen = analysenKontext;
    if (r === 'icharts') { const st = chartState(); if (st) k.chart = { symbol: st.symbol, timeframe: st.timeframe, last_price: st.last_price, drawings: (st.drawings || []).length }; }
    if (letzteKarte && Date.now() - letzteKarte.t < 120000) k.karte = { titel: letzteKarte.titel, text: letzteKarte.text };
    const sicht = sichtbarText(); if (sicht) k.sichtbar = sicht;
    return k;
  }

  // Analysen-iframe: Kontext und Fragen kommen per postMessage, Steuerung geht per postMessage hin
  const analysenRahmen = () => $('#analysen-rahmen');
  const wartend = new Map();
  addEventListener('message', (e) => {
    if (!e.origin.endsWith(':' + (BASIS + 1))) return;
    const d = e.data || {};
    if (d.typ === 'kontext') analysenKontext = d;
    else if (d.typ === 'fragen') frageZurKarte(d.titel, d.text, d);
    else if (d.typ === 'chart-oeffnen') chartOeffnen(d).catch((x) => console.warn('iC Charts:', x.message));
    else if (d.id && wartend.has(d.id)) { wartend.get(d.id)(d); wartend.delete(d.id); }
  });
  function anAnalysen(nachricht, timeout = 10000) {
    return new Promise((resolve, reject) => {
      const f = analysenRahmen(); if (!f || !f.contentWindow) return reject(new Error('Reiter Analysen ist nicht geladen'));
      const id = Math.random().toString(36).slice(2); wartend.set(id, resolve);
      f.contentWindow.postMessage({ ...nachricht, id }, '*');
      setTimeout(() => { if (wartend.has(id)) { wartend.delete(id); reject(new Error('Analysen antworten nicht')); } }, timeout);
    });
  }
  const warten = (ms) => new Promise((r) => setTimeout(r, ms));
  async function rahmenBereit(sel, pruefen, max = 20000) {
    const t0 = Date.now();
    while (Date.now() - t0 < max) { try { if (pruefen()) return true; } catch (e) { /* noch nicht */ } await warten(250); }
    throw new Error(`${sel} lädt nicht`);
  }
  // „In iC Charts öffnen“ aus der Chart-Vorauswahl: Reiter wechseln, Symbol und Zeiteinheit setzen, Formationen einzeichnen
  async function chartOeffnen({ symbol, tf, formationen }) {
    zeigen('icharts');
    await rahmenBereit('iC Charts', () => chartAgent() && chartState()?.symbol);
    const ag = chartAgent();
    if (chartState().symbol !== symbol) { await ag.TOOLS.set_symbol({ symbol }); await warten(900); }
    if (tf && chartState().timeframe !== tf) { await ag.TOOLS.set_timeframe({ timeframe: tf }); await warten(900); }
    try { ag.TOOLS.clear_drawings(); } catch (e) { /* nichts zu löschen */ }
    if (formationen?.length) await ag.TOOLS.draw_formations({ formationen });
  }
  function zeigen(ziel) { if (typeof window.zeigen === 'function') window.zeigen(ziel); else $(`#reiter button[data-ziel="${ziel}"]`)?.click(); }

  // ---------------- Werkzeuge, die hier im Browser laufen ----------------
  const TOOLS = {
    get_view() { return kontext(); },
    async navigate({ tab, section, wert, tag, tf } = {}) {
      const w = wert ? await wertAufloesen(wert) : null;
      if (wert && !w) throw new Error(`"${wert}" ist nicht auf der Watchlist`);
      const ziel = tab || (section || tag ? 'analysen' : tf && !tab ? reiter() : reiter());
      if (ziel !== reiter()) zeigen(ziel);
      if (ziel === 'analysen') {
        await rahmenBereit('Analysen', () => analysenRahmen()?.contentWindow && analysenKontext);
        const a = await anAnalysen({ typ: 'navigieren', reiter: section, wert: w?.id, tag, tf }, 25000);
        return { ok: true, reiter: 'analysen', ...a, kontext: analysenKontext };
      }
      if (ziel === 'icharts') {
        await rahmenBereit('iC Charts', () => chartAgent() && chartState()?.symbol);
        if (w) await chartAgent().TOOLS.set_symbol({ symbol: w.symbol });
        if (tf) await chartAgent().TOOLS.set_timeframe({ timeframe: tf });
        await warten(600);
        const st = chartState(); return { ok: true, reiter: 'icharts', symbol: st?.symbol, timeframe: st?.timeframe, last_price: st?.last_price };
      }
      if (ziel === 'orderbuch') {   // eigener Reiter Orderbuch: Wert per Knopf in der Werteleiste wählen (nur DE und Krypto haben ein Buch)
        if (w && !['DE', 'KRYPTO'].includes(w.markt)) throw new Error(`${w.symbol}: Im Reiter Orderbuch gibt es nur deutsche und Krypto-Werte`);
        let knopf = null;
        for (let i = 0; i < 60 && !(knopf = w ? document.querySelector(`.ob-wert[data-id="${CSS.escape(w.id)}"]`) : document.querySelector('.ob-wert[aria-pressed="true"]')); i++) await warten(250);
        if (w && knopf && knopf.getAttribute('aria-pressed') !== 'true') knopf.click();
        return { ok: true, reiter: 'orderbuch', wert: w ? w.symbol : knopf?.textContent?.trim() || null, hinweis: 'Signal-Lage und Heatmap brauchen einige Sekunden zum Laden' };
      }
      return { ok: true, reiter: ziel };
    },
    async highlight({ title } = {}) {
      if (reiter() === 'analysen') { const a = await anAnalysen({ typ: 'hervorheben', titel: title }); return { ok: true, gefunden: !!a.gefunden }; }
      const such = String(title || '').toLowerCase().trim();
      const s = $('main > section:not([hidden])'); if (!s || !such) return { ok: false, gefunden: false };
      const el = $$('.rubrik h2, .lage-titel, h3, .detail h3, th, td:first-child, .beschriftung', s).find((x) => x.textContent.toLowerCase().includes(such));
      if (!el) return { ok: true, gefunden: false };
      let ziel = el.closest('.karte, .lage, tr, .detail') || el;
      const rub = el.closest('.rubrik');
      if (rub) { let n = rub.nextElementSibling; while (n && !n.classList.contains('rubrik') && !n.matches('.karte, .kacheln, .zwei, .rahmen, .lage, table')) n = n.nextElementSibling; ziel = n && !n.classList.contains('rubrik') ? n : (rub.nextElementSibling || rub); }
      $$('.ki-hervor').forEach((x) => x.classList.remove('ki-hervor')); ziel.classList.add('ki-hervor'); ziel.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => ziel.classList.remove('ki-hervor'), 5000);
      return { ok: true, gefunden: true };
    },
  };
  async function werkzeug(name, input) {
    if (TOOLS[name]) return TOOLS[name](input || {});
    const ag = chartAgent();
    if (!ag) throw new Error('iC Charts ist nicht geöffnet: erst navigate mit tab "icharts" (und dem Wert) aufrufen');
    if (!ag.TOOLS[name]) throw new Error('Unbekanntes Werkzeug ' + name);
    return ag.TOOLS[name](input || {});
  }

  // ---------------- Verbindung ----------------
  let ws = null, busy = false, retry = 0, verbunden = false;
  function status(art, t) { const s = $('#ki-status'); s.className = 'ki-status' + (art ? ' ki-' + art : ''); s.querySelector('span').textContent = t; }
  function beschaeftigt(b) { busy = b; $('#ki-senden').hidden = b; $('#ki-stopp').hidden = !b; text.disabled = b; }
  function verbinden() {
    try { ws = new WebSocket(WS_URL); } catch (e) { status('off', 'Adresse ungültig'); return; }
    status('', 'verbinde …');
    ws.onopen = () => { retry = 0; verbunden = true; status('live', 'verbunden'); };
    ws.onclose = () => { verbunden = false; status('off', 'Agent aus (Port 8814)'); beschaeftigt(false); setTimeout(verbinden, Math.min(15000, 1000 * 2 ** retry++)); };
    ws.onerror = () => {};
    ws.onmessage = async (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'status') { status(m.ready ? 'live' : 'off', m.text || 'bereit'); if (m.model) $('#ki-modell').textContent = (m.anbieter === 'openrouter' ? 'OpenRouter · ' : '') + m.model; }
      else if (m.type === 'text_delta') anhaengen(m.text);
      else if (m.type === 'assistant_done') fertig();
      else if (m.type === 'step') { fertig(); if (m.state === 'run') { const d = schritt(m.name, m.label, m.input, 'run'); d.dataset.name = m.name; } else { const d = [...lauf.querySelectorAll(`.ki-schritt.run[data-name="${m.name}"]`)].pop(); if (d) { d.className = 'ki-schritt ' + (m.state === 'err' ? 'err' : 'done'); if (m.state === 'err' && m.text) d.querySelector('span').textContent = m.text.slice(0, 80); } } }
      else if (m.type === 'tool_use') { fertig(); if (m.name === 'ToolSearch') return; schritt(m.name, { WebSearch: 'sucht im Netz', WebFetch: 'liest eine Seite', Read: 'liest Quellcode', Grep: 'durchsucht Quellcode', Glob: 'sucht Dateien' }[m.name] || m.name, m.input, 'done'); }
      else if (m.type === 'tool_call') {
        fertig();
        const chip = schritt(m.name, m.label, m.input, 'run');
        try { const result = await werkzeug(m.name, m.input); ws.send(JSON.stringify({ type: 'tool_result', id: m.id, ok: true, result })); chip.className = 'ki-schritt done'; }
        catch (e) { ws.send(JSON.stringify({ type: 'tool_result', id: m.id, ok: false, error: e.message })); chip.className = 'ki-schritt err'; chip.querySelector('span').textContent = e.message.slice(0, 80); }
      }
      else if (m.type === 'result') {
        fertig(); beschaeftigt(false);
        const c = m.cost != null ? `${Number(m.cost).toFixed(3).replace('.', ',')} $` : '';
        $('#ki-kosten').textContent = [m.model, c, m.turns ? `${m.turns} Züge` : '', m.duration ? `${(m.duration / 1000).toFixed(1).replace('.', ',')} s` : ''].filter(Boolean).join(' · ');
        if (m.error) blase('error', esc(m.error));
      }
      else if (m.type === 'error') { fertig(); beschaeftigt(false); blase('error', esc(m.message)); }
    };
  }
  function senden(t) {
    t = (t ?? text.value).trim(); if (!t || busy) return;
    if (!ws || ws.readyState !== 1) { blase('error', 'Keine Verbindung zum Agenten-Server (Port 8814). Läuft der Dienst com.m37.ic-delta-agent?'); return; }
    $('#ki-heute').innerHTML = ''; $('.ki-chips')?.remove();
    blase('user', md(t)); text.value = ''; beschaeftigt(true);
    ws.send(JSON.stringify({ type: 'user', text: t, context: kontext() }));
    letzteKarte = null;
  }
  $('#ki-senden').onclick = () => senden();
  $('#ki-stopp').onclick = () => ws && ws.send(JSON.stringify({ type: 'abort' }));
  $('#ki-neu').onclick = () => { lauf.innerHTML = ''; ws && ws.send(JSON.stringify({ type: 'new_session' })); heute(); };
  text.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); senden(); } });
  text.addEventListener('input', () => { text.style.height = 'auto'; text.style.height = Math.min(140, text.scrollHeight) + 'px'; });

  // ---------------- Fragen zu einer Karte (kommen aus Analysen per postMessage) ----------------
  function frageZurKarte(titel, inhalt, extra) {
    letzteKarte = { titel, text: String(inhalt || '').slice(0, 1500), t: Date.now(), ...extra };
    oeffnen(true);
    senden(`Was sagt mir „${titel}“ gerade? Erklär es kurz mit den wichtigsten Zahlen.`);
  }
  // Fragen zu einzelnen Karten gibt es nicht mehr als ✦-Knopf je Rubrik; frageZurKarte bleibt für Analysen (postMessage).

  // ---------------- „Heute“: letzte Morgenlage oder Nachbesprechung und Schnellfragen ----------------
  async function heute() {
    const h = $('#ki-heute'); h.innerHTML = '';
    try {
      const d = await (await fetch(ANALYSEN + '/api/notizen?n=1')).json();
      const n = d.notizen?.[0];
      if (n && n.art !== 'notiz') {
        const zeit = new Date(n.zeit).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        h.innerHTML = `<div class="ki-karte zu"><div class="ki-ueber"><span>${n.art === 'morgenlage' ? '🌅 Morgenlage' : '🌇 Nachbesprechung'}</span><span>${esc(zeit)}${n.modell ? ' · ' + esc(n.modell) : ''}</span></div><div class="ki-inhalt ki-msg">${md(n.text)}</div><button class="ki-mehr">mehr</button></div>`;
        h.querySelector('.ki-mehr').onclick = (e) => { const k = e.target.closest('.ki-karte'); k.classList.toggle('zu'); e.target.textContent = k.classList.contains('zu') ? 'mehr' : 'weniger'; };
      }
    } catch (e) { /* kein Analysen-Dienst */ }
    if (!lauf.children.length && !$('.ki-chips')) {
      const c = document.createElement('div'); c.className = 'ki-chips';
      c.innerHTML = `<div class="ki-chips-titel">Vorschläge</div>` + SCHNELL.map((s) => `<button type="button">${esc(s)}</button>`).join('');
      c.querySelectorAll('button').forEach((b) => { b.onclick = () => senden(b.textContent); });
      h.after(c);
    }
  }

  // ---------------- Einstellungen ----------------
  async function einstellungen() {
    const box = $('#ki-einst');
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false; box.innerHTML = '<div class="ki-hinweis">lädt …</div>';
    let ki = {}, st = {};
    try { ki = await (await fetch(ANALYSEN + '/api/ki')).json(); } catch (e) { box.innerHTML = '<div class="ki-hinweis">Analysen-Dienst nicht erreichbar.</div>'; return; }
    try { st = await (await fetch(AGENT + '/status')).json(); } catch (e) { /* Agent aus */ }
    box.innerHTML = `
      <div class="ke-gruppe"><div class="ke-titel">Modell</div>
        <label class="ke-feld"><span>Anbieter</span><select id="ke-anbieter"><option value="claude-code" ${ki.anbieter === 'claude-code' ? 'selected' : ''}>Claude Code · Login dieses Rechners</option><option value="openrouter" ${ki.anbieter === 'openrouter' ? 'selected' : ''}>OpenRouter · eigener Schlüssel</option></select></label>
        <label class="ke-feld"><span>Modell im Chat</span><select id="ke-modell"></select></label>
        <label class="ke-feld"><span>Modell für Routinen</span><select id="ke-routine"></select></label>
        <div class="ki-hinweis" id="ke-modellhinweis"></div>
        <div class="ki-hinweis">OpenRouter-Schlüssel <b class="${st.openrouter_schluessel ? 'ke-da' : 'ke-fehlt'}">${st.openrouter_schluessel ? 'vorhanden' : 'fehlt'}</b>${st.openrouter_schluessel ? '' : ' · Datei <code>~/.config/marktdaten/ki.env</code> mit <code>OPENROUTER_KEY=…</code> legst du selbst an'}</div></div>
      <div class="ke-gruppe"><div class="ke-titel">Automatik</div>
        <label class="ke-schalter"><span><b>Zweitmeinung</b><small>zu jedem ausgelösten Alarm per Telegram</small></span><input type="checkbox" id="ke-zweit" ${ki.zweitmeinung ? 'checked' : ''}></label>
        <label class="ke-schalter"><span><b>Routinen</b><small>Morgenlage und Nachbesprechung an Handelstagen</small></span><input type="checkbox" id="ke-routinen" ${ki.routinen !== false ? 'checked' : ''}></label>
        <div class="ke-zwei"><label class="ke-feld"><span>Morgenlage</span><input type="time" id="ke-morgen" value="${esc(ki.morgenlage || '08:45')}"></label>
          <label class="ke-feld"><span>Nachbesprechung</span><input type="time" id="ke-abend" value="${esc(ki.nachbesprechung || '17:50')}"></label></div>
        <div class="ki-hinweis">Heute gelaufen: ${esc(Object.entries(st.routinen || {}).map(([k, v]) => `${k} ${v}`).join(', ') || 'noch keine')}</div></div>
      <div class="ke-knoepfe"><button id="ke-speichern">← Zurück</button><button id="ke-morgenlage">Morgenlage jetzt</button><button id="ke-nachbesprechung">Nachbesprechung jetzt</button></div>
      <div class="ki-hinweis" id="ke-meldung"></div>`;
    // Modell-Auswahl je Anbieter: Claude Code fest, OpenRouter aus der Live-Liste (nach Hersteller gruppiert, mit Preis je 1 Mio. Tokens)
    let liste = { claude: [], openrouter: [] };
    try { liste = await (await fetch(AGENT + '/modelle')).json(); } catch (e) { /* Agent aus */ }
    const preis = (x) => (isFinite(x) ? (x < 1 ? x.toFixed(2) : x.toFixed(1)).replace('.', ',') : '?');
    function fuellen(sel, aktuell) {
      const or = $('#ke-anbieter').value === 'openrouter';
      let html = '';
      if (!or) html = liste.claude.map((m) => `<option value="${esc(m.id)}">${esc(m.name)} · ${esc(m.hinweis)}</option>`).join('');
      else {
        const gruppen = {};
        for (const m of liste.openrouter) (gruppen[m.anbieter] = gruppen[m.anbieter] || []).push(m);
        const vorne = ['anthropic', 'openai', 'google', 'x-ai', 'deepseek', 'mistralai', 'meta-llama', 'qwen'];
        const reihe = [...vorne.filter((g) => gruppen[g]), ...Object.keys(gruppen).filter((g) => !vorne.includes(g))];
        html = reihe.map((g) => `<optgroup label="${esc(g)}">${gruppen[g].map((m) => `<option value="${esc(m.id)}">${esc(m.name)} · ${preis(m.ein)} / ${preis(m.aus)} $</option>`).join('')}</optgroup>`).join('');
      }
      sel.innerHTML = html;
      const ids = [...sel.options].map((o) => o.value);
      if (aktuell && !ids.includes(aktuell)) sel.insertAdjacentHTML('afterbegin', `<option value="${esc(aktuell)}">${esc(aktuell)} (gespeichert)</option>`);
      sel.value = ids.includes(aktuell) || aktuell ? aktuell : ids[0];
      if (!sel.value && sel.options.length) sel.selectedIndex = 0;
    }
    function alleFuellen(behalten) {
      const or = $('#ke-anbieter').value === 'openrouter';
      const passt = (id) => id && (or ? id.includes('/') : !id.includes('/'));
      const neuestes = (muster) => [...liste.openrouter].reverse().find((m) => muster.test(m.id))?.id;   // aus der Live-Liste, nicht geraten
      fuellen($('#ke-modell'), behalten && passt(ki.modell) ? ki.modell : (or ? neuestes(/^anthropic\/claude.*opus/) : 'claude-opus-5-5'));
      fuellen($('#ke-routine'), behalten && passt(ki.modell_routine) ? ki.modell_routine : (or ? neuestes(/^anthropic\/claude.*sonnet/) : 'claude-sonnet-5-5'));
      $('#ke-modellhinweis').textContent = or ? (liste.fehler || `${liste.openrouter.length} Modelle mit Werkzeugen, Preis je 1 Mio. Tokens Eingabe / Ausgabe`) : 'über das Claude-Login dieses Rechners, Kosten laufen über dein Abo';
    }
    alleFuellen(true);
    // Ein Knopf für beides: ohne Änderung „Zurück“ (schließt die Einstellungen), mit Änderung „Speichern“
    const werte = () => JSON.stringify([...['anbieter', 'modell', 'routine', 'morgen', 'abend'].map((k) => $('#ke-' + k).value), $('#ke-zweit').checked, $('#ke-routinen').checked]);
    let gespeichert = werte();
    const knopfStand = () => { const neu = werte() !== gespeichert, k = $('#ke-speichern'); k.textContent = neu ? 'Speichern' : '← Zurück'; k.classList.toggle('ke-haupt', neu); };
    box.oninput = knopfStand; box.onchange = knopfStand;
    $('#ke-anbieter').onchange = () => alleFuellen(false);
    $('#ke-speichern').onclick = async () => {
      if (werte() === gespeichert) { box.hidden = true; return; }
      const d = { anbieter: $('#ke-anbieter').value, modell: $('#ke-modell').value.trim(), modell_routine: $('#ke-routine').value.trim(), zweitmeinung: $('#ke-zweit').checked, routinen: $('#ke-routinen').checked, morgenlage: $('#ke-morgen').value, nachbesprechung: $('#ke-abend').value };
      try { await fetch(ANALYSEN + '/api/ki', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) }); $('#ke-meldung').textContent = 'gespeichert, gilt ab der nächsten Frage'; $('#ki-modell').textContent = (d.anbieter === 'openrouter' ? 'OpenRouter · ' : '') + d.modell; gespeichert = werte(); knopfStand(); }
      catch (e) { $('#ke-meldung').textContent = 'Fehler: ' + e.message; }
    };
    for (const art of ['morgenlage', 'nachbesprechung']) $(`#ke-${art}`).onclick = async (e) => {
      e.target.disabled = true; $('#ke-meldung').textContent = `${art} läuft (dauert ein bis drei Minuten) …`;
      try { const r = await (await fetch(AGENT + '/routine', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ art, senden: true }) })).json(); if (r.fehler) throw new Error(r.fehler); $('#ke-meldung').textContent = 'fertig, steht unter Analysen → Claude und ggf. in Telegram'; box.hidden = true; lauf.innerHTML = ''; heute(); }
      catch (x) { $('#ke-meldung').textContent = 'Fehler: ' + x.message; }
      e.target.disabled = false;
    };
  }
  $('#ki-zahnrad').onclick = einstellungen;

  // ---------------- Öffnen und Schließen ----------------
  function oeffnen(an) {
    const offen = an ?? panel.hidden;
    panel.hidden = !offen; document.body.classList.toggle('ki-offen', offen);
    if (offen) { if (!ws) verbinden(); if (!lauf.children.length) heute(); setTimeout(() => text.focus(), 50); }
  }
  knopf.onclick = () => oeffnen(true); $('#ki-zu').onclick = () => oeffnen(false);
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); oeffnen(); }
    else if (e.key === 'Escape' && !panel.hidden && panel.contains(document.activeElement)) oeffnen(false);
  });
  if (new URLSearchParams(location.search).get('claude') === '1') oeffnen(true);
  window.__kiPanel = { TOOLS, senden, oeffnen, kontext };
})();
