const form = document.querySelector('#upload');
const audio = document.querySelector('#audio');
const filename = document.querySelector('#filename');
const drop = document.querySelector('#drop');
const button = document.querySelector('#submit');
const status = document.querySelector('#status');
const results = document.querySelector('#results');
const checkout = document.querySelector('#checkout');
const verifyPayment = document.querySelector('#verify-payment');
const paymentNotice = document.querySelector('#payment-notice');
const paymentPanel = document.querySelector('#payment');
const price = document.querySelector('#price');
const jobKey = 'duu-salgagch-current-job';
let activeJob = null;
let paymentBusy = false;
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

function showJob(job, id) {
  activeJob = id;
  history.replaceState(null, '', '/#job=' + id);
  const url = '/api/jobs/' + id;
  results.querySelectorAll('article').forEach((card, i) => {
    const stem = ['vocals', 'instrumental'][i];
    const preview = url + '/preview/' + stem;
    const player = card.querySelector('audio');
    if (player.getAttribute('src') !== preview) player.src = preview;
    const link = card.querySelector('a');
    link.href = url + '/full/' + stem;
    link.hidden = !job.paid;
  });
  price.textContent = new Intl.NumberFormat('mn-MN').format(job.amount);
  paymentPanel.hidden = job.paid;
  checkout.hidden = !job.payment_available;
  verifyPayment.hidden = !job.payment_available || !job.payment_started;
  paymentNotice.textContent = job.payment_available ?
    'Төлбөр баталгаажсаны дараа хоёр MP3-ыг 7 хоногийн дотор татаарай.' :
    'Онлайн төлбөр хараахан идэвхжээгүй байна.';
  announce(job.paid ? 'Төлбөр баталгаажлаа. Бүтэн MP3-аа татаарай.' : 'Эхний 20 секундийг үнэгүй сонсоорой.');
  results.hidden = false;
}

async function resumeJob(id) {
  const job = await request('/api/jobs/' + id);
  if (job.status === 'ready') showJob(job, id);
  if (job.status === 'failed') throw new Error(job.error);
  return job;
}

async function waitForJob(id) {
  let job = await resumeJob(id);
  if (job.status === 'processing') announce('Хоолой, хөгжмийг салгаж байна. Хэдэн минут үргэлжилж болно.');
  while (job.status === 'processing') {
    await pause(2000);
    job = await resumeJob(id);
  }
  return job;
}

async function handlePayment(action) {
  if (!activeJob || paymentBusy) return;
  paymentBusy = true;
  checkout.disabled = verifyPayment.disabled = button.disabled = true;
  const id = activeJob;
  try {
    const result = await request('/api/jobs/' + id + '/' + action, {method: 'POST'});
    if (result.url) {
      location.assign(result.url);
      return;
    }
    await resumeJob(id);
    if (!result.paid) paymentNotice.textContent = 'Төлбөр хараахан баталгаажаагүй байна. Түр хүлээгээд дахин шалгана уу.';
  } catch (error) {
    paymentNotice.textContent = error.message;
  } finally {
    paymentBusy = false;
    checkout.disabled = verifyPayment.disabled = button.disabled = false;
  }
}

checkout.addEventListener('click', () => handlePayment('checkout'));
verifyPayment.addEventListener('click', () => handlePayment('verify-payment'));

const wireReturn = new URLSearchParams(location.search).get('wire');
const linkedJob = new URLSearchParams(location.hash.slice(1)).get('job');
const previousJob = /^[a-f0-9]{32}$/.test(linkedJob || '') ? linkedJob : localStorage.getItem(jobKey);
if (previousJob) {
  button.disabled = true;
  waitForJob(previousJob).then(job => {
    if (wireReturn === 'return' && job.payment_started && !job.paid) return handlePayment('verify-payment');
  }).catch(error => announce(error.message, true)).finally(() => { button.disabled = false; });
}
if (wireReturn) history.replaceState(null, '', '/' + location.hash);

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
    localStorage.setItem(jobKey, id);
    await waitForJob(id);
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
