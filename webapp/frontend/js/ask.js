renderSidebar('Ask Atlastra');
attachSearchDropdown(document.getElementById('searchBox'));

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

const EXAMPLES = [
  'Who leads the Premier League in assists?',
  'Compare Bellingham and Musiala',
  "What's Arsenal's current form?",
  'Tell me about Lamine Yamal',
];

const log = document.getElementById('askLog');
const empty = document.getElementById('askEmpty');
const form = document.getElementById('askForm');
const input = document.getElementById('askInput');
const send = document.getElementById('askSend');

document.getElementById('askChips').innerHTML = EXAMPLES.map(q =>
  `<span class="ask-chip">${esc(q)}</span>`).join('');
document.getElementById('askChips').addEventListener('click', (e) => {
  const chip = e.target.closest('.ask-chip');
  if (chip) { input.value = chip.textContent; ask(); }
});

// grow the textarea with content, up to the CSS max-height
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = input.scrollHeight + 'px';
});
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
});
form.addEventListener('submit', (e) => { e.preventDefault(); ask(); });

function addMsg(text, cls) {
  empty.style.display = 'none';
  const el = document.createElement('div');
  el.className = 'ask-msg ' + cls;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function ask() {
  const q = input.value.trim();
  if (!q || send.disabled) return;
  addMsg(q, 'q');
  input.value = '';
  input.style.height = 'auto';
  send.disabled = true;
  const pending = addMsg('Thinking…', 'a pending');
  try {
    const r = await apiPost('/api/ask', { question: q });
    pending.textContent = r.answer || "Sorry, I couldn't come up with an answer.";
  } catch {
    pending.textContent = "Something went wrong reaching Ask Atlastra — please try again.";
  } finally {
    pending.classList.remove('pending');
    send.disabled = false;
    input.focus();
  }
}
