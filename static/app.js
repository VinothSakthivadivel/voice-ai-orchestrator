// Voice AI Orchestrator - frontend (vanilla JS, no build step)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { providers: [], langs: [], bots: [], info: {}, editing: null, call: null };
const token = () => localStorage.getItem('vao_token') || '';

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json', 'X-Access-Token': token(), ...(opts.headers || {}) } });
  if (r.status === 401) { showAuth(token() ? 'The saved access token was rejected by the server. Enter the correct APP_ACCESS_TOKEN.' : ''); const e = new Error('Access token required'); e.auth = true; throw e; }
  if (!r.ok) throw new Error((await r.text()).slice(0, 300));
  return r.json();
}
function showAuth(msg = '') { $('#authPanel').classList.remove('hidden'); $('#tokenErr').textContent = msg; $('#token').focus(); }
function callMsg(text, kind = 'err') { const el = $('#callMsg'); if (!text) { el.className = 'callmsg hidden'; return; } el.className = 'callmsg ' + kind; el.textContent = text; }
function appendMsg(text, kind = 'err') {
  const el = $('#callMsg'), cur = el.classList.contains('hidden') || /^(Connecting|Connected|Still connecting)/.test(el.textContent) ? '' : el.textContent;
  callMsg(cur ? cur + '\n' + text : text, cur && el.classList.contains('err') ? 'err' : kind);
}
function micErrorText(err) {
  const n = err && err.name;
  if (n === 'NotAllowedError' || n === 'SecurityError') return 'Microphone permission was denied. Click the 🔒/camera icon in the address bar, allow the microphone for this site, then start the call again. (You can still type messages.)';
  if (n === 'NotFoundError' || n === 'OverconstrainedError') return 'No microphone was found. Plug in / enable a mic, or type messages instead.';
  if (n === 'NotReadableError') return 'The microphone is in use by another app (Zoom/Teams?) or blocked by the OS. Close it and retry.';
  return 'Microphone unavailable: ' + (err && err.message || err) + ' (You can still type messages.)';
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------ keys (browser-only)
function loadKeys() {
  try { return JSON.parse(sessionStorage.getItem('vao_keys') || localStorage.getItem('vao_keys') || '{}'); } catch { return {}; }
}
function saveKeys(k) {
  sessionStorage.setItem('vao_keys', JSON.stringify(k));
  if ($('#rememberKeys').checked) localStorage.setItem('vao_keys', JSON.stringify(k)); else localStorage.removeItem('vao_keys');
}
function credGroups() {
  const g = {};
  for (const p of S.providers) {
    if (!p.credentials.length) continue;
    const x = g[p.cred_group] ||= { name: p.cred_group, labels: [], fields: {}, providers: [] };
    x.labels.push(`${p.layer.toUpperCase()}: ${p.label}`); x.providers.push(p);
    for (const c of p.credentials) x.fields[c.name] ||= c;
  }
  return Object.values(g);
}
function renderKeys() {
  const keys = loadKeys();
  $('#rememberKeys').checked = !!localStorage.getItem('vao_keys');
  $('#keyGroups').innerHTML = credGroups().map(g => {
    const envOk = g.providers.some(p => S.info.env_configured?.[`${p.layer}:${p.name}`]);
    return `<div class="keycard" data-group="${g.name}"><h4>${esc(g.name)} ${envOk ? '<span class="badge env">server env key set</span>' : ''}</h4>
      <div class="small muted">${g.labels.map(esc).join('<br>')}</div>
      ${Object.values(g.fields).map(f => `<label>${esc(f.label)} <span class="muted">(${f.env})</span>
        <input data-field="${f.name}" type="${f.secret ? 'password' : 'text'}" autocomplete="off" placeholder="${esc(f.default || '')}" value="${esc(keys[g.name]?.[f.name] || '')}"></label>`).join('')}
      <div class="row">${g.providers.map(p => `<button type="button" class="small-btn" data-test="${p.layer}:${p.name}">Test ${p.layer.toUpperCase()}</button>`).join('')}</div>
      <div class="test-out"></div></div>`;
  }).join('');
}
function collectKeys() {
  const k = {};
  for (const card of $$('.keycard')) {
    const g = card.dataset.group; k[g] = {};
    for (const i of $$('input[data-field]', card)) if (i.value.trim()) k[g][i.dataset.field] = i.value.trim();
    if (!Object.keys(k[g]).length) delete k[g];
  }
  return k;
}
$('#saveKeys').onclick = () => { saveKeys(collectKeys()); toast('Keys saved in this browser'); };
$('#clearKeys').onclick = () => { sessionStorage.removeItem('vao_keys'); localStorage.removeItem('vao_keys'); renderKeys(); };
$('#keyGroups').addEventListener('click', async e => {
  const b = e.target.closest('[data-test]'); if (!b) return;
  const [layer, provider] = b.dataset.test.split(':');
  const out = $('.test-out', b.closest('.keycard')); out.textContent = 'testing…';
  const keys = collectKeys();
  const lang = S.langs.find(l => l.code === 'hi-IN' && l[layer]?.includes(provider)) ? 'hi-IN' : 'en-IN';
  try {
    const r = await api('/api/test-provider', { method: 'POST', body: JSON.stringify({ layer, provider, keys, language: lang }) });
    if (!r.ok) { out.innerHTML = `<span style="color:var(--bad)">✗ ${esc(r.error)}</span>`; return; }
    out.innerHTML = `<span style="color:var(--ok)">✓ ${layer} OK in ${r.latency_ms} ms</span> ${esc(r.output || '')}`;
    if (r.audio_b64) player.playTest(r);
  } catch (err) { out.textContent = String(err); }
});

// ------------------------------------------------------------ tabs / toast
$('#tabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  $$('#tabs button').forEach(x => x.classList.toggle('active', x === b));
  $$('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
};
function toast(m) { logEv('ℹ ' + m); }
function logEv(m) {
  const el = $('#events'); const d = document.createElement('div');
  d.textContent = new Date().toLocaleTimeString() + '  ' + m; el.prepend(d);
  while (el.children.length > 200) el.lastChild.remove();
}

// ------------------------------------------------------------ language / provider views
function renderLangs() {
  const stt = S.providers.filter(p => p.layer === 'stt').map(p => p.name).filter(n => n !== 'mock');
  const tts = S.providers.filter(p => p.layer === 'tts').map(p => p.name).filter(n => n !== 'mock');
  const b = (list, n) => `<span class="badge ${list.includes(n) ? 'ok' : 'no'}">${n}</span>`;
  $('#langTable').innerHTML = `<tr><th>Language</th><th>Code</th><th>Script</th><th>STT</th><th>TTS</th><th>LLM</th></tr>` +
    S.langs.map(l => `<tr><td><b>${esc(l.name)}</b> <span class="muted">${esc(l.native)}</span></td><td>${l.code}</td><td class="small">${esc(l.script)}</td>
    <td>${stt.map(n => b(l.stt, n)).join('')}</td><td>${tts.map(n => b(l.tts, n)).join('')}</td><td><span class="badge ok">all LLMs</span></td></tr>`).join('');
  const opts = `<option value="auto">Auto-detect (provider dependent)</option>` + S.langs.map(l => `<option value="${l.code}">${esc(l.name)} – ${esc(l.native)} (${l.code})</option>`).join('');
  $('#fLang').innerHTML = opts;
}
function renderProviders() {
  $('#provList').innerHTML = S.providers.map(p => `<div class="prov"><div class="layer">${p.layer}</div><b>${esc(p.label)}</b>
    <div class="small muted">${p.models.length ? 'Models: ' + p.models.map(esc).join(', ') : ''}</div>
    <div>${p.features.map(f => `<span class="badge">${esc(f)}</span>`).join('')}</div>
    <div class="small muted">${esc(p.notes)}</div>
    <div class="small">Env: ${p.credentials.map(c => `<code>${c.env}</code>`).join(' ') || '—'}</div></div>`).join('');
  for (const layer of ['stt', 'llm', 'tts']) {
    $(`select[data-layer=${layer}]`).innerHTML = S.providers.filter(p => p.layer === layer).map(p => `<option value="${p.name}">${esc(p.label)}</option>`).join('');
  }
}

// ------------------------------------------------------------ bot profiles
const getPath = (o, p) => p.split('.').reduce((a, k) => a?.[k], o);
const setPath = (o, p, v) => { const ks = p.split('.'); let a = o; ks.slice(0, -1).forEach(k => a = a[k] ||= {}); a[ks.at(-1)] = v; };
async function loadBots(selectId) {
  S.bots = (await api('/api/bots')).bots;
  $('#botList').innerHTML = S.bots.map(b => `<div class="botitem ${b.id === (selectId || S.editing?.id) ? 'sel' : ''}" data-id="${b.id}">
    <b>${esc(b.name)}</b><small>${b.language} · ${b.stt.provider} → ${b.llm.provider} → ${b.tts.provider}</small><br><small>${esc(b.description)}</small></div>`).join('');
  const cur = $('#cBot').value;
  $('#cBot').innerHTML = S.bots.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  if (selectId || cur) $('#cBot').value = selectId || cur;
  syncControlsFromBot();
}
function editBot(b) {
  S.editing = structuredClone(b);
  $('#botFormTitle').textContent = b.id ? `Edit: ${b.name}` : 'New bot';
  for (const el of $$('#botForm [name]')) {
    const v = getPath(b, el.name);
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v ?? '';
  }
  $$('.botitem').forEach(x => x.classList.toggle('sel', x.dataset.id === b.id));
  updateFormHints();
}
function formBot() {
  const b = structuredClone(S.editing || {});
  for (const el of $$('#botForm [name]')) {
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (el.type === 'number') v = el.value === '' ? null : Number(el.value);
    if (v === '' && /model|voice|style/.test(el.name)) v = null;
    if (el.name === 'id' && !v) v = null;
    setPath(b, el.name, v);
  }
  return b;
}
function updateFormHints(ev) {
  // a voice from another language (e.g. en-IN-NeerjaNeural for Hindi) would not speak the new language
  if (ev?.target?.name === 'language') { const v = $('#botForm [name="tts.voice"]'); if (v.value && /^[a-z]{2,3}-[A-Z]{2}-/.test(v.value) && !v.value.startsWith(ev.target.value)) v.value = ''; }
  const f = formBot(); const l = S.langs.find(x => x.code === f.language);
  const prov = n => S.providers.find(p => p.layer === n.split('.')[0] && p.name === getPath(f, n));
  for (const layer of ['stt', 'llm', 'tts']) {
    const p = prov(layer + '.provider');
    $(`#${layer}Models`).innerHTML = (p?.models || []).map(m => `<option value="${esc(m)}">`).join('');
  }
  const voices = l?.voices?.[f.tts.provider] || [];
  $('#ttsVoices').innerHTML = voices.map(v => `<option value="${esc(v)}">`).join('');
  if (!l) { $('#langSupport').innerHTML = '<span class="muted">Auto-detect: provider decides (Sarvam, Deepgram, OpenAI, AssemblyAI support detection).</span>'; return; }
  const ok = (layer) => l[layer].includes(getPath(f, layer + '.provider'));
  $('#langSupport').innerHTML = `STT ${ok('stt') ? '<span class="badge ok">supported</span>' : '<span class="badge" style="color:var(--bad)">NOT supported</span>'}
    TTS ${ok('tts') ? '<span class="badge ok">supported</span>' : '<span class="badge" style="color:var(--bad)">NOT supported</span>'}
    <br><span class="muted">STT options: ${l.stt.join(', ') || '—'} · TTS options: ${l.tts.join(', ') || '—'}</span>`;
}
$('#botForm').addEventListener('change', updateFormHints);
$('#botList').onclick = e => { const it = e.target.closest('.botitem'); if (it) editBot(S.bots.find(b => b.id === it.dataset.id)); };
$('#newBot').onclick = () => editBot({ ...structuredClone(S.bots.find(b => b.id === 'demo-mock') || S.bots[0]), id: null, name: 'New bot', description: '' });
$('#dupBot').onclick = () => { const b = formBot(); b.id = null; b.name += ' (copy)'; editBot(b); };
$('#botForm').onsubmit = async e => {
  e.preventDefault();
  try { const saved = await api('/api/bots', { method: 'POST', body: JSON.stringify(formBot()) }); await loadBots(saved.id); editBot(saved); toast('Saved ' + saved.name); }
  catch (err) { alert('Save failed: ' + err.message.replace(/^\{"detail":/, '').slice(0, 400)); }
};
$('#delBot').onclick = async () => { const b = formBot(); if (!b.id || !confirm('Delete ' + b.name + '?')) return; await api('/api/bots/' + b.id, { method: 'DELETE' }); await loadBots(); if (S.bots[0]) editBot(S.bots[0]); };
$('#useBot').onclick = () => { const b = formBot(); if (b.id) $('#cBot').value = b.id; syncControlsFromBot(); $('[data-tab=console]').click(); };
$('#cBot').onchange = syncControlsFromBot;
function currentBot() { return S.bots.find(b => b.id === $('#cBot').value) || S.bots[0]; }
function syncControlsFromBot() {
  const b = currentBot(); if (!b) return;
  $('#lTemp').value = b.llm.temperature; $('#lSpeed').value = b.tts.speed; $('#lPitch').value = b.tts.pitch;
  $('#lStyle').value = b.tts.style || ''; $('#lBarge').checked = b.vad.barge_in; $('#lSpk').checked = b.speaker.enabled;
  $('#lThr').value = b.speaker.threshold; updateLabels();
}
function updateLabels() {
  $('#vTemp').textContent = (+$('#lTemp').value).toFixed(2); $('#vSpeed').textContent = (+$('#lSpeed').value).toFixed(2);
  $('#vPitch').textContent = $('#lPitch').value; $('#vThr').textContent = (+$('#lThr').value).toFixed(2);
}
function liveOverrides() {
  return { llm: { temperature: +$('#lTemp').value }, tts: { speed: +$('#lSpeed').value, pitch: +$('#lPitch').value, style: $('#lStyle').value || null },
    vad: { barge_in: $('#lBarge').checked }, speaker: { enabled: $('#lSpk').checked, threshold: +$('#lThr').value } };
}
for (const id of ['lTemp', 'lSpeed', 'lPitch', 'lStyle', 'lBarge', 'lSpk', 'lThr']) {
  $('#' + id).addEventListener('input', updateLabels);
  $('#' + id).addEventListener('change', () => S.call?.sendJSON({ type: 'update', profile: liveOverrides() }));
}

// ------------------------------------------------------------ audio playback with barge-in support
class Player {
  constructor() { this.speaking = 0; this.ctx = null; this.sources = new Set(); this.next = 0; this.chain = Promise.resolve(); this.turn = -1; this.dead = new Set(); this.endTimer = null; this.doneTurns = new Set(); this.onEnded = null; }
  ensure() { if (!this.ctx) this.ctx = new AudioContext(); if (this.ctx.state === 'suspended') this.ctx.resume(); return this.ctx; }
  async decode(meta, buf) {
    const ctx = this.ensure();
    if (meta.format === 'pcm16') {  // server-normalised: PCM16 little-endian mono at meta.sample_rate
      const n = buf.byteLength >> 1, dv = new DataView(buf);
      const ab = ctx.createBuffer(1, Math.max(1, n), meta.sample_rate || 16000);  // Web Audio resamples to ctx rate
      const ch = ab.getChannelData(0); for (let i = 0; i < n; i++) ch[i] = dv.getInt16(i * 2, true) / 32768; return ab;
    }
    return await ctx.decodeAudioData(buf.slice(0));
  }
  enqueue(meta, buf) {
    if (this.dead.has(meta.turn)) return;
    this.chain = this.chain.then(async () => {
      if (this.dead.has(meta.turn)) return;
      let ab; try { ab = await this.decode(meta, buf); } catch (e) { logEv('audio decode error ' + e); return; }
      if (this.dead.has(meta.turn)) return;
      if (window.__vaoTap) try { window.__vaoTap(ab.getChannelData(0), ab.sampleRate, meta); } catch { }
      const ctx = this.ctx; const src = ctx.createBufferSource(); src.buffer = ab; src.connect(ctx.destination);
      const at = Math.max(ctx.currentTime + 0.02, this.next); src.start(at); this.next = at + ab.duration;
      this.turn = meta.turn; this.sources.add(src); setState('speaking');
      src.onended = () => { this.sources.delete(src); this.checkEnd(); };
    });
  }
  speak(meta) {  // browser speechSynthesis fallback / provider
    if (this.dead.has(meta.turn) || !window.speechSynthesis) { if (!window.speechSynthesis) logEv('speechSynthesis not supported in this browser'); return; }
    const u = new SpeechSynthesisUtterance(meta.text);
    u.lang = meta.lang || 'en-IN'; u.rate = meta.rate || 1; u.pitch = Math.max(0, Math.min(2, 1 + (meta.pitch || 0) / 10));
    const vs = speechSynthesis.getVoices(), base = u.lang.split('-')[0];
    u.voice = vs.find(v => v.lang === u.lang) || vs.find(v => v.lang.replace('_', '-').startsWith(base)) || null;
    if (!u.voice && vs.length) logEv(`no ${u.lang} voice on this device - using default voice`);
    this.turn = meta.turn; this.speaking++; setState('speaking');
    u.onend = u.onerror = () => { this.speaking = Math.max(0, this.speaking - 1); this.checkEnd(); };
    if (window.__vaoTap) try { window.__vaoTap(null, 0, meta); } catch { }
    speechSynthesis.speak(u);
  }
  checkEnd() {
    clearTimeout(this.endTimer);
    this.endTimer = setTimeout(() => {
      if (!this.sources.size && !this.speaking && this.doneTurns.has(this.turn)) this.onEnded?.(this.turn);
    }, 120);
  }
  markDone(turn) { this.doneTurns.add(turn); this.checkEnd(); }
  stop(turn) {
    if (turn !== undefined) this.dead.add(turn);
    for (const s of this.sources) { try { s.stop(); } catch { } }
    if (window.speechSynthesis && this.speaking) { speechSynthesis.cancel(); this.speaking = 0; }
    this.sources.clear(); this.next = 0;
  }
  async playTest(r) { this.enqueue({ turn: -99, format: r.format, sample_rate: r.sample_rate }, Uint8Array.from(atob(r.audio_b64), c => c.charCodeAt(0)).buffer); }
}
const player = new Player();

// ------------------------------------------------------------ call client
function setState(s) { const p = $('#statePill'); p.className = 'pill ' + s; p.textContent = s; }
class Call {
  constructor(bot) { this.bot = bot; this.ws = null; this.pendingMeta = null; this.botMsg = {}; this.metrics = []; this.sent = []; this.turnStart = Date.now(); }
  sendJSON(o) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(o)); }
  async start() {
    this.ready = false;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws/call?token=${encodeURIComponent(token())}`);
    this.ws.binaryType = 'arraybuffer';
    const profile = structuredClone(this.bot);
    for (const [k, v] of Object.entries(liveOverrides())) Object.assign(profile[k], v);
    this.ws.onopen = () => { this.sendJSON({ type: 'start', profile, keys: loadKeys() }); logEv('ws connected'); };
    this.ws.onmessage = e => this.onMsg(e);
    this.ws.onerror = () => logEv('ws error');
    this.ws.onclose = e => {
      logEv('ws closed ' + e.code);
      if (e.code === 4401) { callMsg('Call rejected: access token missing or invalid. Enter the APP_ACCESS_TOKEN in the banner at the top.'); showAuth('The server rejected the access token.'); }
      else if (e.code === 4429) callMsg('Server busy: too many active calls. Try again in a moment.');
      else if (!this.ready && !this.gotError) callMsg(`Could not connect to the call server (WebSocket ${proto}://${location.host}/ws/call, close code ${e.code}). If the service was asleep on Render's free plan, wait ~30 s and try again; check that no proxy/firewall blocks WebSockets.`);
      this.cleanup();
    };
    this.readyTimer = setTimeout(() => { if (!this.ready) callMsg('Still connecting to the call server… (a sleeping Render free instance can take ~30-60 s to wake up)', 'warn'); }, 8000);
    player.onEnded = turn => { this.sendJSON({ type: 'playback_ended', turn }); setState('listening'); };
    try { await this.startMic(); if (this.ready && !this.hasNotes && !$('#callMsg').classList.contains('err')) callMsg('Connected - speak now (or type below).', 'ok'); } catch (err) { const t = micErrorText(err); this.micError = t; appendMsg('🎙 ' + t, 'warn'); addMsg('err', t); }
  }
  async startMic() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error('this page is not on HTTPS (or the browser has no mic API), so the browser blocks microphone access'), { name: 'InsecureContext' });
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error('this page is not on HTTPS (or the browser has no mic API), so the browser blocks microphone access'), { name: 'InsecureContext' });
    this.ctx = new AudioContext();
    if (this.ctx.state === 'suspended') await this.ctx.resume().catch(() => { });
    if (!this.ctx.audioWorklet) throw new Error('this browser does not support AudioWorklet - use a recent Chrome, Edge, Firefox or Safari');
    await this.ctx.audioWorklet.addModule('/static/capture-worklet.js');
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, 'capture-processor');
    this.node.port.onmessage = ev => { if (this.ws?.readyState === 1) this.ws.send(ev.data); };
    src.connect(this.node);
    const tr = this.stream.getAudioTracks()[0]; const st = tr.getSettings?.() || {};
    logEv(`mic: AEC=${st.echoCancellation} NS=${st.noiseSuppression} AGC=${st.autoGainControl} ${this.ctx.sampleRate}Hz→16kHz`);
  }
  onMsg(e) {
    if (e.data instanceof ArrayBuffer) { const m = this.pendingMeta; this.pendingMeta = null; if (m) player.enqueue(m, e.data); return; }
    const m = JSON.parse(e.data);
    switch (m.type) {
      case 'ready': this.ready = true; clearTimeout(this.readyTimer);
        if (!(m.missing_keys || []).length && !this.micError) callMsg((this.stream ? 'Connected - speak now (or type below).' : 'Connected - type a message below.'), 'ok');
        setState('listening'); $('#stackInfo').textContent = `${m.profile.stt} → ${m.profile.llm} → ${m.profile.tts} · ${m.profile.language}`;
        kv({ VAD: m.vad_backend, Denoise: m.denoise_backend, 'Speaker ID': m.speaker_embedder, Language: m.profile.language });
        (m.warnings || []).forEach(w => addMsg('err', '⚠ ' + w));
        this.hasNotes = !!(m.notes || []).length || !!(m.missing_keys || []).length; (m.notes || []).forEach(n => { addMsg('sys', 'ℹ ' + n); appendMsg('ℹ ' + n, 'warn'); }); addMsg('sys', 'Call started – speak or type.'); break;
      case 'level': meter(m); break;
      case 'vad': if (m.state === 'speech_start') { setState('user'); this.turnStart = Date.now(); } logEv('vad ' + m.state + (m.speech_ms ? ` ${m.speech_ms}ms` : '')); break;
      case 'transcript': addMsg('user', m.text, m.language ? `${m.language}${m.confidence ? ' · conf ' + (+m.confidence).toFixed(2) : ''}` : ''); break;
      case 'state': if (m.state !== 'listening' || !player.sources.size) setState(m.state); break;
      case 'llm_delta': { let el = this.botMsg[m.turn]; if (!el) el = this.botMsg[m.turn] = addMsg('bot', ''); el.firstChild.textContent += m.text; scrollT(); break; }
      case 'assistant_done': { let el = this.botMsg[m.turn] || (this.botMsg[m.turn] = addMsg('bot', m.text)); if (m.text) el.firstChild.textContent = m.text; if (m.interrupted) { el.classList.add('interrupted'); el.querySelector('.meta').textContent = '✋ interrupted'; } player.markDone(m.turn); break; }
      case 'audio': if (m.format === 'speech' || m.binary === false) player.speak(m); else this.pendingMeta = m; break;
      case 'interrupt': player.stop(m.turn); addMsg('sys', `✋ bot interrupted (${m.reason}${m.similarity != null ? ', voice sim ' + m.similarity.toFixed(2) : ''})`); setState('user'); break;
      case 'metrics': if (!m.empty) addMetrics(m); break;
      case 'sentiment': addSentiment(m); break;
      case 'speaker': logEv(`speaker ${m.event}${m.similarity != null ? ' sim=' + m.similarity : ''}`); if (m.event === 'rejected') addMsg('sys', `🔇 ignored background/other speaker (sim ${m.similarity})`); if (m.event === 'enrolling') addMsg('sys', `🎙 Enrolling your voice – speak normally for ~${m.seconds}s`); break;
      case 'error':
        if (m.code === 'auth') { this.gotError = true; showAuth('The server rejected the access token.'); callMsg('Call rejected: ' + m.message); }
        else if (m.code === 'missing_key') appendMsg('🔑 ' + m.message + ' Or pick the "Demo (no keys, mock providers)" bot.');
        else if (m.code === 'busy') { this.gotError = true; callMsg(m.message); }
        else if (!this.ready) callMsg(`${m.layer?.toUpperCase() || ''} error: ${m.message}`);
        addMsg('err', `${m.layer?.toUpperCase() || ''} error: ${m.message}`); logEv('error ' + m.message); break;
      default: logEv(m.type);
    }
  }
  stop() { this.sendJSON({ type: 'stop' }); try { this.ws?.close(); } catch { } this.cleanup(); }
  cleanup() {
    clearTimeout(this.readyTimer);
    player.stop(); this.stream?.getTracks().forEach(t => t.stop()); this.ctx?.close().catch(() => { }); this.stream = null; this.ctx = null;
    setState('idle'); $('#callBtn').disabled = false; $('#hangBtn').disabled = true; if (S.call === this) S.call = null;
  }
}
$('#callBtn').onclick = async () => {
  callMsg('');
  if (S.info.auth_required && !S.authed) { showAuth('Enter the access token first.'); callMsg('This deployment needs an access token before calls can start - see the banner at the top.'); return; }
  const b = currentBot();
  if (!b) { callMsg('No bot profiles loaded. ' + (S.bootError || 'Reload the page; if it persists check the server logs.')); return; }
  callMsg('Connecting…', 'warn');
  player.ensure(); S.call = new Call(b); $('#callBtn').disabled = true; $('#hangBtn').disabled = false;
  $('#transcript').innerHTML = ''; resetDash(); await S.call.start();
};
$('#hangBtn').onclick = () => S.call?.stop();
$('#interruptBtn').onclick = () => S.call?.sendJSON({ type: 'interrupt' });
$('#enrollBtn').onclick = () => { $('#lSpk').checked = true; S.call?.sendJSON({ type: 'update', profile: liveOverrides() }); S.call?.sendJSON({ type: 'enroll_speaker' }); };
$('#textForm').onsubmit = e => {
  e.preventDefault(); const t = $('#textIn').value.trim(); if (!t) return;
  if (!S.call) { alert('Start a call first'); return; }
  player.ensure(); S.call.sendJSON({ type: 'text', text: t }); $('#textIn').value = '';
};

// ------------------------------------------------------------ dashboard widgets
function addMsg(kind, text, meta = '') {
  const d = document.createElement('div'); d.className = 'msg ' + kind;
  d.innerHTML = `<span></span><span class="meta"></span>`; d.firstChild.textContent = text; d.lastChild.textContent = meta;
  $('#transcript').append(d); scrollT(); return d;
}
const scrollT = () => { const t = $('#transcript'); t.scrollTop = t.scrollHeight; };
function meter(m) {
  const pct = v => Math.max(0, Math.min(100, (v + 70) / 70 * 100));
  $('#meterBar').style.width = pct(m.db) + '%'; $('#meterFloor').style.left = pct(m.floor) + '%';
  $('#meterTxt').textContent = `mic ${m.db} dBFS · noise floor ${m.floor} dB · ${m.speech ? '🟢 speech' : '⚪ silence'}`;
}
function kv(o) { $('#sessKv').innerHTML = Object.entries(o).map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join(''); }
let metricsRows = [], sentPts = [];
function resetDash() { metricsRows = []; sentPts = []; $('#latTable tbody').innerHTML = ''; $('#latBars').innerHTML = ''; drawSent(); $('#emoBadge').textContent = '—'; }
function addMetrics(m) {
  metricsRows.push(m);
  const f = v => v == null ? '–' : v;
  $('#latTable tbody').insertAdjacentHTML('afterbegin', `<tr><td>${m.turn}</td><td>${f(m.stt_ms)}</td><td>${f(m.llm_ttft_ms)}</td><td>${f(m.llm_total_ms)}</td><td>${f(m.tts_first_ms)}</td><td><b>${f(m.e2e_first_audio_ms)}</b></td></tr>`);
  const items = [['STT', m.stt_ms], ['LLM TTFT', m.llm_ttft_ms], ['LLM total', m.llm_total_ms], ['TTS first', m.tts_first_ms], ['E2E audio', m.e2e_first_audio_ms]];
  const max = Math.max(1, ...items.map(i => i[1] || 0));
  $('#latBars').innerHTML = items.map(([k, v]) => `<div class="bar"><span>${k}</span><div style="width:${(v || 0) / max * 220}px"></div><span>${v == null ? '–' : v + ' ms'}</span></div>`).join('');
}
const EMO_COL = { anger: '#ff5d6c', sadness: '#7f8cff', fear: '#c38bff', joy: '#2ecc8f', surprise: '#f5b041', gratitude: '#5be3d0', neutral: '#8b96b8' };
function addSentiment(m) {
  sentPts.push(m); drawSent();
  $('#emoBadge').textContent = `${m.emotion} · ${m.label}`; $('#emoBadge').style.color = EMO_COL[m.emotion] || '';
  $('#sentScore').textContent = `score ${m.score} · intensity ${m.intensity} · ${m.method}`;
}
function drawSent() {
  const c = $('#sentChart'), g = c.getContext('2d'), W = c.width, H = c.height;
  g.clearRect(0, 0, W, H); g.strokeStyle = '#26314f'; g.beginPath(); g.moveTo(0, H / 2); g.lineTo(W, H / 2); g.stroke();
  g.fillStyle = '#8b96b8'; g.font = '10px sans-serif'; g.fillText('+1', 2, 10); g.fillText('-1', 2, H - 3);
  if (!sentPts.length) return;
  const pts = sentPts.slice(-30), dx = W / Math.max(1, pts.length - 1 || 1);
  g.strokeStyle = '#5b8cff'; g.lineWidth = 2; g.beginPath();
  pts.forEach((p, i) => { const x = pts.length === 1 ? W / 2 : i * dx, y = H / 2 - p.score * (H / 2 - 8); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
  pts.forEach((p, i) => { const x = pts.length === 1 ? W / 2 : i * dx, y = H / 2 - p.score * (H / 2 - 8); g.fillStyle = EMO_COL[p.emotion] || '#fff'; g.beginPath(); g.arc(x, y, 4, 0, 7); g.fill(); });
}

// ------------------------------------------------------------ boot
$('#tokenForm').onsubmit = async e => {
  e.preventDefault();
  const t = $('#token').value.trim(); if (!t) { $('#tokenErr').textContent = 'Please paste the token.'; return; }
  const r = await fetch('/api/auth/check', { headers: { 'X-Access-Token': t } }).catch(() => null);
  if (!r) { $('#tokenErr').textContent = 'Network error - could not reach the server.'; return; }
  if (r.status === 401) { $('#tokenErr').textContent = '✗ That token was rejected. It must exactly match APP_ACCESS_TOKEN in Render → Environment.'; return; }
  localStorage.setItem('vao_token', t); $('#authPanel').classList.add('hidden'); await loadAll();
};
$('#tokenChange').onclick = () => showAuth('');
async function loadAll() {
  try {
    if (S.info.auth_required) {
      const r = await fetch('/api/auth/check', { headers: { 'X-Access-Token': token() } });
      if (r.status === 401) { showAuth(token() ? 'The saved access token was rejected. Enter the correct APP_ACCESS_TOKEN.' : ''); callMsg('Locked: enter the access token (banner above) to load bots and start calls.', 'warn'); return; }
      S.authed = true; $('#authbox').classList.remove('hidden');
    }
    S.providers = (await api('/api/providers')).providers;
    S.langs = (await api('/api/languages')).languages;
    renderProviders(); renderLangs(); renderKeys(); await loadBots();
    if (S.bots[0]) editBot(S.bots[0]);
    if (!S.bots.length) { S.bootError = 'The server returned no bot profiles.'; callMsg(S.bootError + ' Create one in the Bot Profiles tab.'); }
    else callMsg('');
    drawSent();
  } catch (e) {
    if (e.auth) return;
    S.bootError = 'Failed to load from server: ' + e.message;
    $('#bootErr').textContent = '⚠ ' + S.bootError; $('#bootErr').classList.remove('hidden');
  }
}
(async function boot() {
  try {
    S.info = await (await fetch('/api/info')).json(); $('#ver').textContent = 'v' + S.info.version;
  } catch (e) { $('#bootErr').textContent = '⚠ Cannot reach the server: ' + e.message; $('#bootErr').classList.remove('hidden'); return; }
  await loadAll();
})();
