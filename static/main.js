const form = document.querySelector('#upload');
const youtube = document.querySelector('#youtube');
const jobButtons = [youtube.querySelector('button'), form.querySelector('button')];
const audio = document.querySelector('#audio');
const filename = document.querySelector('#filename');
const drop = document.querySelector('#drop');
let jobBusy = false;
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

const youtubeMode = document.querySelector('#youtube-mode');
const uploadMode = document.querySelector('#upload-mode');
const uploadFallback = document.querySelector('#upload-fallback');

function selectSource(fromFile, focus = false) {
  form.hidden = !fromFile;
  youtube.hidden = fromFile;
  youtubeMode.setAttribute('aria-pressed', String(!fromFile));
  uploadMode.setAttribute('aria-pressed', String(fromFile));
  uploadFallback.hidden = true;
  if (focus) (fromFile ? audio : youtube.querySelector('input')).focus();
}

youtubeMode.addEventListener('click', () => selectSource(false));
uploadMode.addEventListener('click', () => selectSource(true));
uploadFallback.addEventListener('click', () => selectSource(true, true));

const theme = document.querySelector('#theme');
const systemTheme = matchMedia('(prefers-color-scheme: dark)');
const themeKey = 'panamn-theme';

function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.querySelector('meta[name=theme-color]').content = dark ? '#100e17' : '#f7f8fc';
  theme.setAttribute('aria-label', dark ? 'Гэрэлтэй горим' : 'Харанхуй горим');
  theme.setAttribute('aria-pressed', String(dark));
  theme.querySelector('span').textContent = dark ? '☀' : '☾';
}

const savedTheme = localStorage.getItem(themeKey);
applyTheme(savedTheme ? savedTheme === 'dark' : systemTheme.matches);
systemTheme.addEventListener('change', event => {
  if (!localStorage.getItem(themeKey)) applyTheme(event.matches);
});
theme.addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  localStorage.setItem(themeKey, dark ? 'dark' : 'light');
  applyTheme(dark);
});

function announce(message, error = false) {
  status.textContent = message;
  status.hidden = !message;
  status.toggleAttribute('data-error', error);
  uploadFallback.hidden = !error || youtube.hidden;
}

audio.addEventListener('change', () => {
  filename.textContent = audio.files[0]?.name || idleLabel;
});

for (const event of ['dragenter', 'dragover', 'dragleave', 'drop']) {
  drop.addEventListener(event, e => {
    e.preventDefault();
    drop.classList.toggle('drag', event === 'dragenter' || event === 'dragover');
    if (event === 'drop' && !jobBusy && !paymentBusy && e.dataTransfer.files.length) {
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
  let lastStatus;
  let job = await resumeJob(id);
  while (['downloading', 'processing'].includes(job.status)) {
    if (job.status !== lastStatus) {
      announce(job.status === 'downloading' ? 'YouTube-ээс аудио авч байна…' :
        'Хоолой, хөгжмийг салгаж байна. Хэдэн минут үргэлжилж болно.');
      lastStatus = job.status;
    }
    await pause(2000);
    job = await resumeJob(id);
  }
  return job;
}

function updateJobButtons() {
  jobButtons.forEach(button => { button.disabled = jobBusy || paymentBusy; });
}

async function handlePayment(action, attempts = 1) {
  if (!activeJob || paymentBusy) return;
  paymentBusy = true;
  checkout.disabled = verifyPayment.disabled = true;
  updateJobButtons();
  const id = activeJob;
  try {
    let result = await request('/api/jobs/' + id + '/' + action, {method: 'POST'});
    if (result.url) {
      location.assign(result.url);
      return;
    }
    for (let attempt = 1; attempt < attempts && !result.paid; attempt++) {
      paymentNotice.textContent = 'Wire төлбөрийг баталгаажуулж байна…';
      await pause(2000);
      result = await request('/api/jobs/' + id + '/' + action, {method: 'POST'});
    }
    await resumeJob(id);
    if (!result.paid) paymentNotice.textContent = 'Төлбөр хараахан баталгаажаагүй байна. Түр хүлээгээд дахин шалгана уу.';
  } catch (error) {
    paymentNotice.textContent = error.message;
  } finally {
    paymentBusy = false;
    checkout.disabled = verifyPayment.disabled = false;
    updateJobButtons();
  }
}

checkout.addEventListener('click', () => handlePayment('checkout'));
verifyPayment.addEventListener('click', () => handlePayment('verify-payment'));

const wireReturn = new URLSearchParams(location.search).get('wire');
const linkedJob = new URLSearchParams(location.hash.slice(1)).get('job');
const previousJob = /^[a-f0-9]{32}$/.test(linkedJob || '') ? linkedJob : localStorage.getItem(jobKey);
if (previousJob) {
  jobBusy = true;
  updateJobButtons();
  waitForJob(previousJob).then(job => {
    if (wireReturn === 'return' && job.payment_started && !job.paid) return handlePayment('verify-payment', 4);
  }).catch(error => announce(error.message, true)).finally(() => { jobBusy = false; updateJobButtons(); });
}
if (wireReturn) history.replaceState(null, '', '/' + location.hash);

async function submitJob(event) {
  event.preventDefault();
  if (jobBusy || paymentBusy) return;
  const inputForm = event.currentTarget;
  const fromFile = inputForm === form;
  if (fromFile && audio.files[0]?.size > 50 * 1024 * 1024) {
    announce('Файлын хэмжээ 50 МБ-аас хэтэрсэн байна.', true);
    return;
  }
  const data = new FormData(inputForm);
  jobBusy = true;
  updateJobButtons();
  results.hidden = true;
  results.querySelectorAll('audio').forEach(player => player.pause());
  announce(fromFile ? 'Файл илгээж байна…' : 'Холбоос шалгаж байна…');
  try {
    const {id} = await request('/api/jobs', {method: 'POST', body: data});
    localStorage.setItem(jobKey, id);
    await waitForJob(id);
  } catch (error) {
    announce(error instanceof TypeError ? 'Холболт тасарлаа. Дахин оролдоно уу.' : error.message, true);
  } finally {
    jobBusy = false;
    updateJobButtons();
  }
}

form.addEventListener('submit', submitJob);
youtube.addEventListener('submit', submitJob);

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
