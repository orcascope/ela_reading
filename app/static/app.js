const API = '/api';
const BOOK_ID = 'fwtbt';
const app = document.getElementById('app');

let explainCache = new Map();   // "paraIndex|selectedText" -> {raw, done, listeners, promise}
let explainReqId = 0;           // bumped per panel render so stale streams can't overwrite a newer one
let currentSelection = null;    // {text, paragraphText, key}

// ---------------------------------------------------------------- helpers --

function getStudent() {
  try { return JSON.parse(localStorage.getItem('ela_read_student') || 'null'); }
  catch { return null; }
}
function setStudent(s) {
  if (s) localStorage.setItem('ela_read_student', JSON.stringify(s));
  else localStorage.removeItem('ela_read_student');
}

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(API + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    if (res.status === 400 && text.includes('unknown student')) {
      setStudent(null);
      location.hash = '#/';
    }
    throw new Error(`${method} ${path} failed: ${res.status} ${text}`);
  }
  return res.json();
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function nav() {
  const student = getStudent();
  if (!student) return '<nav class="top"><span></span><span></span></nav>';
  return `<nav class="top">
    <span>Reading as <strong>${esc(student.name)}</strong></span>
    <span><a href="#/book/${BOOK_ID}">Book</a> &middot; <a href="#/vocabulary">My words</a> &middot; <a href="#/switch">Switch reader</a></span>
  </nav>`;
}

// ------------------------------------------------------------------ router --

async function render() {
  const hash = location.hash || '#/';
  const student = getStudent();

  if (hash === '#/switch') { setStudent(null); location.hash = '#/'; return; }

  if (hash === '#/') {
    if (student) { location.hash = `#/book/${BOOK_ID}`; return; }
    return renderWelcome();
  }

  let m;
  if ((m = hash.match(/^#\/book\/([\w-]+)$/))) return renderBook(m[1]);
  if ((m = hash.match(/^#\/lesson\/([\w-]+)$/))) return renderLesson(m[1]);
  if (hash === '#/vocabulary') return renderVocabulary();

  location.hash = '#/';
}

// ----------------------------------------------------------------- screens --

function renderWelcome() {
  app.innerHTML = `
    <div class="card">
      <h1>ela_read</h1>
      <p class="muted">Guided, paced reading of <em>For Whom the Bell Tolls</em>, with comprehension
      questions and built-in word help along the way.</p>
      <form id="welcome-form">
        <label class="muted" for="name">Your name</label>
        <input type="text" id="name" placeholder="e.g. Ana" required autocomplete="off">
        <div style="margin-top:12px;"><button type="submit">Start reading</button></div>
      </form>
    </div>`;
  document.getElementById('welcome-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('name').value.trim();
    if (!name) return;
    const student = await api('/students', { method: 'POST', body: { name } });
    setStudent(student);
    location.hash = `#/book/${BOOK_ID}`;
  });
}

async function renderBook(bookId) {
  const student = getStudent();
  if (!student) { location.hash = '#/'; return; }
  app.innerHTML = `${nav()}<div class="card"><p class="muted">Loading&hellip;</p></div>`;

  let resume;
  try {
    resume = await api(`/books/${bookId}/resume?student=${student.id}`);
  } catch (err) {
    app.innerHTML = `${nav()}<div class="card"><p>Could not load this book yet.</p><p class="muted">${esc(err.message)}</p></div>`;
    return;
  }

  app.innerHTML = `
    ${nav()}
    <div class="card">
      <h1>For Whom the Bell Tolls</h1>
      <p class="muted">Ernest Hemingway &mdash; read in short, timed lessons instead of one long scroll.</p>
      <button id="resume-btn">Continue reading</button>
    </div>`;
  document.getElementById('resume-btn').addEventListener('click', () => {
    location.hash = `#/lesson/${resume.lesson_id}`;
  });
}

async function renderLesson(lessonId) {
  const student = getStudent();
  if (!student) { location.hash = '#/'; return; }
  app.innerHTML = `${nav()}<div class="card"><p class="muted">Loading lesson&hellip;</p></div>`;

  let lesson;
  try {
    lesson = await api(`/lessons/${lessonId}?student=${student.id}`);
  } catch (err) {
    app.innerHTML = `${nav()}<div class="card"><p>Could not load this lesson.</p><p class="muted">${esc(err.message)}</p></div>`;
    return;
  }

  explainCache = new Map();
  currentSelection = null;

  const savedMc = (lesson.progress && lesson.progress.mc_answers) || {};
  const savedOpen = (lesson.progress && lesson.progress.open_responses) || {};
  const alreadyDone = !!(lesson.progress && lesson.progress.completed);

  app.innerHTML = `
    ${nav()}
    <div class="lesson-header">
      <div>
        <h2>${esc(lesson.title)}</h2>
        <p class="muted">~${lesson.estimated_minutes} min &middot; ${lesson.word_count} words</p>
      </div>
    </div>

    <div class="passage" id="passage">
      ${lesson.paragraphs.map((p, i) => `<p data-para="${i}">${esc(p)}</p>`).join('\n')}
    </div>
    <p class="muted">Tip: select any word or sentence you don't understand.</p>

    <div class="select-popup" id="select-popup">
      <button data-action="meaning" type="button">Meaning</button>
      <button data-action="fit" type="button">How it fits</button>
      <button data-action="save" type="button">Save word</button>
    </div>
    <div class="explain-panel" id="explain-panel"></div>

    <div class="card" id="continue-card" style="${alreadyDone ? 'display:none;' : ''}">
      <button id="continue-btn" type="button">Continue to questions &rarr;</button>
    </div>

    <div class="card" id="questions-card" style="${alreadyDone ? '' : 'display:none;'}">
      <h3>Check your understanding</h3>
      <form id="questions-form">
        ${lesson.questions.map((q, qi) => `
          <div class="question">
            <p><strong>${qi + 1}.</strong> ${esc(q.prompt)}</p>
            ${Object.entries(q.choices).map(([letter, text]) => `
              <label class="choice">
                <input type="radio" name="mc-${q.id}" value="${letter}" ${savedMc[q.id] === letter ? 'checked' : ''}>
                ${esc(letter)}. ${esc(text)}
              </label>`).join('')}
            <div class="grading" id="grading-${q.id}" style="display:none;"></div>
          </div>`).join('')}
        ${lesson.open_response_questions.map((q, qi) => `
          <div class="question">
            <p><strong>${lesson.questions.length + qi + 1}.</strong> ${esc(q.prompt)}</p>
            <ul class="muted">${q.guidance.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>
            <textarea name="or-${q.id}" placeholder="Write your answer here...">${esc(savedOpen[q.id] || '')}</textarea>
          </div>`).join('')}
        <div style="margin-top:16px;">
          <button type="submit">Submit answers</button>
        </div>
      </form>
      <div id="next-lesson-area"></div>
    </div>
  `;

  setupSelectionHandling(lessonId, student.id);

  const continueBtn = document.getElementById('continue-btn');
  if (continueBtn) {
    continueBtn.addEventListener('click', () => {
      document.getElementById('questions-card').style.display = 'block';
      document.getElementById('continue-card').style.display = 'none';
      document.getElementById('questions-card').scrollIntoView({ behavior: 'smooth' });
    });
  }

  document.getElementById('questions-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const mcAnswers = {};
    for (const q of lesson.questions) {
      const v = formData.get(`mc-${q.id}`);
      if (v) mcAnswers[q.id] = v;
    }
    const openResponses = {};
    for (const q of lesson.open_response_questions) {
      openResponses[q.id] = (formData.get(`or-${q.id}`) || '').trim();
    }

    const submitBtn = e.target.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    let result;
    try {
      result = await api(`/lessons/${lessonId}/progress`, {
        method: 'POST',
        body: { student: student.id, mc_answers: mcAnswers, open_responses: openResponses, completed: true },
      });
    } catch (err) {
      submitBtn.disabled = false;
      alert('Could not submit: ' + err.message);
      return;
    }

    for (const [qid, g] of Object.entries(result.grading)) {
      const el = document.getElementById(`grading-${qid}`);
      if (!el) continue;
      el.style.display = 'block';
      el.className = 'grading ' + (g.correct ? 'correct' : 'incorrect');
      el.textContent = (g.correct ? 'Correct. ' : `Not quite — correct answer: ${g.correct_answer}. `) + g.rationale;
    }
    submitBtn.textContent = 'Answers saved';

    const nextArea = document.getElementById('next-lesson-area');
    if (result.next_lesson_id) {
      nextArea.innerHTML = '<div style="margin-top:16px;"><button id="next-btn" type="button">Next lesson &rarr;</button></div>';
      document.getElementById('next-btn').addEventListener('click', () => {
        location.hash = `#/lesson/${result.next_lesson_id}`;
      });
    } else {
      nextArea.innerHTML = '<p class="muted" style="margin-top:16px;">More lessons coming soon.</p>';
    }
  });
}

async function renderVocabulary() {
  const student = getStudent();
  if (!student) { location.hash = '#/'; return; }
  app.innerHTML = `${nav()}<div class="card"><p class="muted">Loading&hellip;</p></div>`;

  let items;
  try {
    items = await api(`/vocabulary?student=${student.id}`);
  } catch (err) {
    app.innerHTML = `${nav()}<div class="card"><p>Could not load your words.</p><p class="muted">${esc(err.message)}</p></div>`;
    return;
  }

  app.innerHTML = `
    ${nav()}
    <div class="card">
      <h2>My words</h2>
      ${items.length === 0 ? '<p class="muted">Nothing saved yet. While reading, select a word or sentence and choose "Save word."</p>' : ''}
      ${items.map((v) => `
        <div class="vocab-item">
          <div class="vocab-word">${esc(v.selected_text)}</div>
          <p>${esc(v.meaning)}</p>
          <p class="muted">${esc(v.context_note)}</p>
          <p class="muted">from ${esc(v.lesson_title)}</p>
        </div>`).join('')}
    </div>`;
}

// ---------------------------------------------------- text-selection popup --

function hidePopups() {
  const popup = document.getElementById('select-popup');
  const panel = document.getElementById('explain-panel');
  if (popup) popup.classList.remove('open');
  if (panel) panel.classList.remove('open');
}

function setupSelectionHandling(lessonId, studentId) {
  const passage = document.getElementById('passage');
  const popup = document.getElementById('select-popup');
  const panel = document.getElementById('explain-panel');
  if (!passage || !popup || !panel) return;

  passage.addEventListener('mouseup', () => {
    const sel = window.getSelection();
    const text = sel ? sel.toString().trim() : '';
    if (!text || sel.rangeCount === 0) { hidePopups(); return; }

    const range = sel.getRangeAt(0);
    const anchorNode = range.startContainer.nodeType === 3 ? range.startContainer.parentElement : range.startContainer;
    const paraEl = anchorNode ? anchorNode.closest('[data-para]') : null;
    if (!paraEl || !passage.contains(paraEl)) { hidePopups(); return; }

    currentSelection = { text, paragraphText: paraEl.textContent, key: `${paraEl.dataset.para}|${text}` };

    const rect = range.getBoundingClientRect();
    popup.style.top = `${window.scrollY + rect.bottom + 6}px`;
    popup.style.left = `${window.scrollX + rect.left}px`;
    popup.classList.add('open');
    panel.classList.remove('open');
    popup.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
  });

  document.addEventListener('mousedown', (e) => {
    if (!popup.contains(e.target) && !panel.contains(e.target) && !passage.contains(e.target)) hidePopups();
  });

  popup.querySelector('[data-action="meaning"]').addEventListener('click', () =>
    showExplainField('meaning', lessonId, studentId, popup));
  popup.querySelector('[data-action="fit"]').addEventListener('click', () =>
    showExplainField('context_fit', lessonId, studentId, popup));
  popup.querySelector('[data-action="save"]').addEventListener('click', () =>
    saveVocabulary(lessonId, studentId, popup));
}

// ---- explain stream: two wire formats, one internal shape --------------------------------
// The server may stream the answer in either format. Every message is the WHOLE answer so far
// (built by JsonOutputParser), e.g. {"meaning": "The wo"} then {"meaning": "The word ...", "fit": "In"}.
//
//   ndjson  (Content-Type: application/x-ndjson)   one JSON object per line.
//           A failure arrives as a line  {"error": "..."}.  The end of the stream means "done".
//   sse     (Content-Type: text/event-stream)      frames  "event: <name>\ndata: <json>\n\n"
//           events: partial (the answer so far), done (the final answer), error ({"message": "..."}).
//           A stream that ends WITHOUT a done event is treated as a dropped connection.
//
// STREAM_FORMAT: 'auto' follows the response's Content-Type, so the browser needs no change when the
// server switches format. Set 'ndjson' or 'sse' to force one, or add ?stream=sse to the page URL.
const STREAM_FORMAT = 'auto';   // 'auto' | 'ndjson' | 'sse'

function pickFormat(res) {
  const forced = new URLSearchParams(location.search).get('stream') || STREAM_FORMAT;
  if (forced === 'ndjson' || forced === 'sse') return forced;
  return (res.headers.get('content-type') || '').includes('text/event-stream') ? 'sse' : 'ndjson';
}

// The server's key is "fit"; the UI and the vocabulary save use "context_fit".
function toFields(obj) {
  return {
    meaning: typeof obj.meaning === 'string' ? obj.meaning : '',
    context_fit: typeof obj.fit === 'string' ? obj.fit : '',
  };
}

// Decoded text pieces of the response body. Cancels the request if the consumer stops early.
async function* readChunks(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      yield decoder.decode(value, { stream: true });
    }
    const tail = decoder.decode();
    if (tail) yield tail;
  } finally {
    reader.cancel().catch(() => {});
  }
}

// Both parsers turn text pieces into the same events: {type: 'partial' | 'done' | 'error', data}.
function ndjsonEvent(line) {
  if (!line.trim()) return null;
  const obj = JSON.parse(line);
  return obj.error ? { type: 'error', data: obj } : { type: 'partial', data: obj };
}

async function* parseNdjson(chunks) {
  let buf = '';
  for await (const text of chunks) {
    buf += text;
    const lines = buf.split('\n');
    buf = lines.pop();                       // the last piece may be a half-received line
    for (const line of lines) {
      const ev = ndjsonEvent(line);
      if (ev) yield ev;
    }
  }
  const last = ndjsonEvent(buf);             // a final line without a trailing newline
  if (last) yield last;
}

function sseEvent(block) {
  let name = 'message';
  const data = [];
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue;          // comments, used as keep-alive heartbeats
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') name = value;
    else if (field === 'data') data.push(value);
  }
  if (!data.length) return null;                          // a heartbeat-only block
  const obj = JSON.parse(data.join('\n'));
  if (name === 'error') return { type: 'error', data: obj };
  if (name === 'done') return { type: 'done', data: obj };
  return { type: 'partial', data: obj };                  // "partial", or an unnamed message
}

async function* parseSse(chunks) {
  let buf = '';
  for await (const text of chunks) {
    buf = (buf + text).replace(/\r\n/g, '\n');            // done on the whole buffer: a \r\n can straddle two pieces
    let end;
    while ((end = buf.indexOf('\n\n')) !== -1) {          // a blank line ends one event
      const block = buf.slice(0, end);
      buf = buf.slice(end + 2);
      const ev = sseEvent(block);
      if (ev) yield ev;
    }
  }
  const last = sseEvent(buf);                             // a last event missing its blank line
  if (last) yield last;
}

async function streamExplain(sel, lessonId, studentId, entry) {
  const res = await fetch(API + '/explain', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      student: studentId,
      lesson_id: lessonId,
      selected_text: sel.text,
      context: sel.paragraphText,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    if (res.status === 400 && text.includes('unknown student')) {
      setStudent(null);
      location.hash = '#/';
    }
    throw new Error(`POST /explain failed: ${res.status} ${text}`);
  }

  const format = pickFormat(res);
  const parse = format === 'sse' ? parseSse : parseNdjson;
  let sawDone = false;
  for await (const ev of parse(readChunks(res))) {
    if (ev.type === 'error') throw new Error(ev.data.message || ev.data.error || 'explain failed');
    entry.data = toFields(ev.data);
    if (ev.type === 'done') sawDone = true;
    else entry.listeners.forEach((fn) => fn(entry.data, false));
  }
  // SSE sends an explicit done event, so a missing one means the connection dropped.
  if (format === 'sse' && !sawDone) throw new Error('stream ended before the done event');
  // the server validates too; both parts must be there for the answer to count as complete
  if (!entry.data || !entry.data.meaning || !entry.data.context_fit) throw new Error('incomplete explanation');
  entry.done = true;
  entry.listeners.forEach((fn) => fn(entry.data, true));
  entry.listeners.clear();
}

// Starts (or joins) the streamed request for the current selection. onUpdate({meaning, context_fit}, done)
// fires as the answer grows; the returned promise resolves to {meaning, context_fit} once complete.
function getExplanation(lessonId, studentId, onUpdate) {
  if (!currentSelection) return Promise.resolve(null);
  const sel = currentSelection;
  let entry = explainCache.get(sel.key);
  if (!entry) {
    entry = { data: null, done: false, listeners: new Set() };
    entry.promise = streamExplain(sel, lessonId, studentId, entry);
    explainCache.set(sel.key, entry);
    const failed = entry;
    entry.promise.catch(() => {                       // let the user retry after a failure
      if (explainCache.get(sel.key) === failed) explainCache.delete(sel.key);
    });
  }
  if (onUpdate && !entry.done) entry.listeners.add(onUpdate);
  if (onUpdate && entry.data) onUpdate(entry.data, entry.done);   // catch up on what already arrived
  const done = entry;
  return done.promise.then(() => done.data);
}

async function showExplainField(field, lessonId, studentId, popup) {
  const panel = document.getElementById('explain-panel');
  const btn = popup.querySelector(`[data-action="${field === 'meaning' ? 'meaning' : 'fit'}"]`);
  popup.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  panel.style.top = popup.style.top;
  panel.style.left = popup.style.left;
  const sel = currentSelection;
  const reqId = ++explainReqId;
  const label = field === 'meaning' ? 'Meaning' : 'How it fits the passage';
  panel.innerHTML = `<div class="selected">"${esc(sel.text)}"</div>
    <div class="field-label">${label}</div>
    <p class="answer muted">Thinking&hellip;</p>`;
  panel.classList.add('open');
  const answerEl = panel.querySelector('.answer');

  const show = (data) => {
    if (reqId !== explainReqId) return;               // user moved on to another selection/button
    const value = data[field];
    if (!value) return;                               // this field has not started arriving yet
    answerEl.classList.remove('muted');
    answerEl.textContent = value;
  };

  try {
    const result = await getExplanation(lessonId, studentId, show);
    if (reqId !== explainReqId) return;
    answerEl.classList.remove('muted');
    answerEl.textContent = result[field] || 'No explanation returned.';
  } catch (err) {
    console.error('explain failed:', err);
    if (reqId !== explainReqId) return;
    panel.innerHTML = `<div class="selected">"${esc(sel.text)}"</div><p class="muted">Couldn't get an explanation. Try again.</p>`;
  }
}

async function saveVocabulary(lessonId, studentId, popup) {
  if (!currentSelection) return;
  const saveBtn = popup.querySelector('[data-action="save"]');
  const original = saveBtn.textContent;
  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving…';
  try {
    const result = await getExplanation(lessonId, studentId);
    await api('/vocabulary', {
      method: 'POST',
      body: {
        student: studentId,
        lesson_id: lessonId,
        selected_text: currentSelection.text,
        meaning: result.meaning,
        context_note: result.context_fit,
      },
    });
    saveBtn.textContent = 'Saved';
  } catch (err) {
    saveBtn.textContent = 'Failed';
  } finally {
    setTimeout(() => { saveBtn.disabled = false; saveBtn.textContent = original; }, 1500);
  }
}

window.addEventListener('hashchange', render);
window.addEventListener('DOMContentLoaded', render);
