const form = document.querySelector('#upload');
const audio = document.querySelector('#audio');
const filename = document.querySelector('#filename');
const drop = document.querySelector('#drop');
const button = document.querySelector('#submit');
const status = document.querySelector('#status');
const results = document.querySelector('#results');
const idleLabel = filename.textContent;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function announce(message, error = false) {
  status.textContent = message;
  status.hidden = !message;
  status.toggleAttribute('data-error', error);
}

audio.addEventListener('change', () => {
  filename.textContent = audio.files[0]?.name || idleLabel;
});

for (const event of ['dragenter', 'dragover', 'dragleave', 'drop']) {
  drop.addEventListener(event, e => {
    e.preventDefault();
    drop.classList.toggle('drag', event === 'dragenter' || event === 'dragover');
    if (event === 'drop' && !button.disabled && e.dataTransfer.files.length) {
      const files = new DataTransfer();
      files.items.add(e.dataTransfer.files[0]);
      audio.files = files.files;
      audio.dispatchEvent(new Event('change'));
    }
  });
}

async function request(url, config) {
  const response = await fetch(url, {cache: 'no-store', ...config});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || 'Серверийн алдаа гарлаа. Дахин оролдоно уу.');
  return body;
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  if (button.disabled) return;
  if (audio.files[0].size > 50 * 1024 * 1024) {
    announce('Файлын хэмжээ 50 МБ-аас хэтэрсэн байна.', true);
    return;
  }
  const data = new FormData(form);
  button.disabled = audio.disabled = true;
  results.hidden = true;
  results.querySelectorAll('audio').forEach(player => player.pause());
  announce('Файл илгээж байна…');
  try {
    const {id} = await request('/api/jobs', {method: 'POST', body: data});
    const url = '/api/jobs/' + id;
    announce('Хоолой, хөгжмийг салгаж байна. Дууны уртаас хамаарч хэдэн минут үргэлжилж болно.');
    let job;
    do {
      await pause(2000);
      job = await request(url);
    } while (job.status === 'processing');
    if (job.status !== 'ready') throw new Error(job.error || 'Боловсруулалт амжилтгүй боллоо.');
    results.querySelectorAll('article').forEach((card, i) => {
      const stem = url + '/' + ['vocals', 'instrumental'][i];
      card.querySelector('audio').src = stem;
      card.querySelector('a').href = stem + '?download=1';
    });
    announce('Бэлэн боллоо.');
    results.hidden = false;
  } catch (error) {
    announce(error instanceof TypeError ? 'Холболт тасарлаа. Дахин оролдоно уу.' : error.message, true);
  } finally {
    button.disabled = audio.disabled = false;
  }
});

document.querySelector('#youtube').addEventListener('submit', e => {
  e.preventDefault();
  const message = document.querySelector('#link-error');
  const url = new URL(document.querySelector('#link').value);
  let id;
  if (url.hostname === 'youtu.be') id = url.pathname.slice(1);
  if (['www.youtube.com', 'youtube.com', 'm.youtube.com'].includes(url.hostname)) {
    id = url.pathname === '/watch' ? url.searchParams.get('v') : url.pathname.match(/^\/(?:shorts|live)\/([^/]+)$/)?.[1];
  }
  if (!['https:', 'http:'].includes(url.protocol) || !/^[\w-]{11}$/.test(id || '')) {
    message.textContent = 'YouTube бичлэгийн зөв холбоос оруулна уу.';
    return;
  }
  message.textContent = '';
  window.open('https://www.youtube.com/watch?v=' + id, '_blank', 'noopener,noreferrer');
});

const install = document.querySelector('#install');
let installPrompt;

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  installPrompt = event;
  install.hidden = false;
});

install.addEventListener('click', async () => {
  if (!installPrompt) return;
  const prompt = installPrompt;
  installPrompt = null;
  install.hidden = true;
  await prompt.prompt();
});

window.addEventListener('appinstalled', () => {
  installPrompt = null;
  install.hidden = true;
  document.querySelector('#ios-install').hidden = true;
});

const appleMobile = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
if (appleMobile && !navigator.standalone && !matchMedia('(display-mode: standalone)').matches) {
  document.querySelector('#ios-install').hidden = false;
}
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(console.error);
}
