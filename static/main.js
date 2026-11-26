const form = document.querySelector('#upload');
const youtube = document.querySelector('#youtube');
const jobButtons = [...youtube.querySelectorAll('button[type=submit]'), form.querySelector('button')];
const audio = document.querySelector('#audio');
const searchPanel = document.querySelector('#song-search');
const searchForm = document.querySelector('#search-form');
const searchInput = document.querySelector('#song-query');
const searchStatus = document.querySelector('#search-status');
const searchResults = document.querySelector('#search-results');
const youtubeLink = document.querySelector('#link');
const songPlayer = document.querySelector('#song-player');
const playerFrame = document.querySelector('#player-frame');
const playerTitle = document.querySelector('#player-title');
const watchYouTube = document.querySelector('#watch-youtube');
let searchRequest;
const filename = document.querySelector('#filename');
const drop = document.querySelector('#drop');
let jobBusy = false;
const status = document.querySelector('#status');
const results = document.querySelector('#results');
const converted = document.querySelector('#converted');
const convertedDownload = document.querySelector('#converted-download');
const checkout = document.querySelector('#checkout');
const verifyPayment = document.querySelector('#verify-payment');
const paymentNotice = document.querySelector('#payment-notice');
const paymentPanel = document.querySelector('#payment');
const price = document.querySelector('#price');
const downloadAll = document.querySelector('#download-all');
const newJob = document.querySelector('#new-job');
localStorage.removeItem('duu-salgagch-current-job');
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
  searchPanel.hidden = fromFile;
  if (fromFile) closeSongPlayer();
  youtubeMode.setAttribute('aria-pressed', String(!fromFile));
  uploadMode.setAttribute('aria-pressed', String(fromFile));
  uploadFallback.hidden = true;
  if (focus) (fromFile ? audio : youtube.querySelector('input')).focus();
}

youtubeMode.addEventListener('click', () => selectSource(false));
uploadMode.addEventListener('click', () => selectSource(true));
uploadFallback.addEventListener('click', () => selectSource(true, true));
document.querySelector('#convert-to-upload').addEventListener('click', () => selectSource(true, true));

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

function announceSearch(message) {
  searchStatus.textContent = message;
  searchStatus.hidden = !message;
}

function closeSongPlayer() {
  playerFrame.replaceChildren();
  watchYouTube.removeAttribute('href');
  playerTitle.textContent = '';
  songPlayer.hidden = true;
}

function playSong(result) {
  const id = result.id;
  if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return;
  const watchUrl = 'https://www.youtube.com/watch?v=' + id;
  youtubeLink.value = watchUrl;
  watchYouTube.href = watchUrl;
  playerTitle.textContent = result.title;
  const player = document.createElement('iframe');
  player.title = result.title;
  player.referrerPolicy = 'strict-origin-when-cross-origin';
  player.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
  player.allowFullscreen = true;
  player.src = 'https://www.youtube-nocookie.com/embed/' + id + '?autoplay=1&playsinline=1';
  playerFrame.replaceChildren(player);
  songPlayer.hidden = false;
  searchResults.hidden = true;
  announceSearch('');
  player.focus();
}

searchForm.addEventListener('submit', async event => {
  event.preventDefault();
  searchRequest?.abort();
  const controller = new AbortController();
  searchRequest = controller;
  searchResults.hidden = true;
  searchResults.replaceChildren();
  closeSongPlayer();
  announceSearch('YouTube-ээс хайж байна…');
  try {
    const {results} = await request('/api/youtube/search?' + new URLSearchParams({q: searchInput.value}), {
      signal: controller.signal,
    });
    for (const result of results) {
      const row = document.createElement('li');
      const button = document.createElement('button');
      const art = document.createElement('span');
      const thumbnail = document.createElement('img');
      const play = document.createElement('span');
      const copy = document.createElement('span');
      const title = document.createElement('strong');
      const details = document.createElement('small');
      button.type = 'button';
      button.setAttribute('aria-label', 'Тоглуулах: ' + result.title);
      art.className = 'song-art';
      thumbnail.src = 'https://i.ytimg.com/vi/' + result.id + '/mqdefault.jpg';
      thumbnail.alt = '';
      thumbnail.loading = 'lazy';
      thumbnail.decoding = 'async';
      thumbnail.referrerPolicy = 'no-referrer';
      play.className = 'song-play';
      play.setAttribute('aria-hidden', 'true');
      play.textContent = '▶';
      copy.className = 'song-copy';
      title.textContent = result.title;
      const seconds = Math.floor(result.duration);
      const duration = Number.isFinite(result.duration)
        ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : '';
      details.textContent = [result.channel, duration].filter(Boolean).join(' · ');
      art.append(thumbnail, play);
      copy.append(title, details);
      button.append(art, copy);
      button.addEventListener('click', () => playSong(result));
      row.append(button);
      searchResults.append(row);
    }
    searchResults.hidden = !results.length;
    announceSearch(results.length ? 'Бичлэгээ сонгоно уу.' : '10 минут хүртэлх тохирох бичлэг олдсонгүй.');
  } catch (error) {
    if (error.name !== 'AbortError') announceSearch(error.message);
  }
});

function showJob(job, id) {
  activeJob = id;
  newJob.hidden = false;
  const url = '/api/jobs/' + id;
  if (job.operation === 'convert') {
    results.hidden = true;
    convertedDownload.href = url + '/mp3';
    converted.hidden = false;
    announce('MP3 бэлэн боллоо.');
    return;
  }
  converted.hidden = true;
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
  downloadAll.href = url + "/download";
  downloadAll.hidden = !job.paid;
  paymentPanel.hidden = job.paid;
  checkout.hidden = !job.payment_available;
  verifyPayment.hidden = !job.payment_available || !job.payment_started;
  paymentNotice.textContent = job.payment_available ?
    'Төлбөр баталгаажсаны дараа хоёр MP3-ыг 7 хоногийн дотор татаарай.' :
    'Онлайн төлбөр хараахан идэвхжээгүй байна.';
  announce(job.paid ? 'Төлбөр баталгаажлаа. Бүтэн MP3-аа татаарай.' : 'Эхний 30 секундийг үнэгүй сонсоорой.');
  results.hidden = false;
}

async function resumeJob(id) {
  const job = await request('/api/jobs/' + id);
  if (job.status === 'ready') showJob(job, id);
  if (job.status === 'failed') {
    throw new Error(job.error);
  }
  return job;
}

const processing = document.querySelector('#processing');
const meter = document.querySelector('#job-progress');
const jobTime = document.querySelector('#job-time');
const phaseLabels = {
  downloading: 'YouTube-ээс аудио авч байна…',
  preparing: 'Аудиог салгалтад бэлдэж байна…',
  separating: 'Хоолой, хөгжмийг салгаж байна…',
  encoding: 'MP3 файлуудыг бэлдэж байна…',
};

async function waitForJob(id) {
  let previousPhase;
  try {
    let job = await resumeJob(id);
    while (job.status === 'downloading' || job.status === 'processing') {
      const phase = job.phase || (job.status === 'downloading' ? 'downloading' : 'separating');
      if (phase !== previousPhase) {
        announce(phaseLabels[phase]);
        previousPhase = phase;
      }
      const value = Number.isInteger(job.progress) ? job.progress : null;
      if (value === null) meter.removeAttribute('value');
      else meter.value = value;
      const seconds = job.elapsed;
      const time = Number.isInteger(seconds) ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : '';
      jobTime.textContent = [value === null ? '' : `${value}%`, time].filter(Boolean).join(' · ');
      processing.hidden = false;
      await pause(1800);
      job = await resumeJob(id);
    }
    return job;
  } finally {
    processing.hidden = true;
  }
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
      paymentNotice.textContent = 'QPay төлбөрийг баталгаажуулж байна…';
      await pause(2000);
      result = await request('/api/jobs/' + id + '/' + action, {method: 'POST'});
    }
    await resumeJob(id);
    if (result.paid) downloadAll.click();
    else paymentNotice.textContent = 'Төлбөр хараахан баталгаажаагүй байна. Түр хүлээгээд дахин шалгана уу.';
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

function clearJob() {
  activeJob = null;
  form.reset();
  youtube.reset();
  searchRequest?.abort();
  searchForm.reset();
  closeSongPlayer();
  searchResults.replaceChildren();
  searchResults.hidden = true;
  announceSearch('');
  filename.textContent = idleLabel;
  selectSource(false);
  announce('');
  newJob.hidden = true;
  converted.hidden = true;
  results.hidden = true;
  convertedDownload.removeAttribute('href');
  downloadAll.removeAttribute('href');
  for (const player of results.querySelectorAll('audio')) {
    player.removeAttribute('src');
    player.load();
  }
  for (const link of results.querySelectorAll('article a')) link.removeAttribute('href');
}

newJob.addEventListener('click', () => {
  if (!jobBusy && !paymentBusy) clearJob();
});

for (const link of [convertedDownload, downloadAll]) {
  link.addEventListener('click', event => {
    // Keep a manual retry available after an automatic checkout download.
    if (event.isTrusted && activeJob) {
      const downloadedJob = activeJob;
      setTimeout(() => {
        if (activeJob === downloadedJob && !jobBusy && !paymentBusy) clearJob();
      }, 300);
    }
  });
}

window.addEventListener('pageshow', event => {
  if (event.persisted && !jobBusy && !paymentBusy) clearJob();
});

const wireReturn = new URLSearchParams(location.search).get('wire');
const linkedJob = new URLSearchParams(location.hash.slice(1)).get('job');
if (['return', 'cancel'].includes(wireReturn) && /^[a-f0-9]{32}$/.test(linkedJob || '')) {
  jobBusy = true;
  updateJobButtons();
  waitForJob(linkedJob).then(job => {
    if (wireReturn !== 'return' || !job.payment_started) return;
    if (job.paid) downloadAll.click();
    else return handlePayment('verify-payment', 8);
  }).catch(error => announce(error.message, true)).finally(() => {
    history.replaceState(null, '', '/');
    jobBusy = false;
    updateJobButtons();
  });
} else if (wireReturn || linkedJob) {
  history.replaceState(null, '', '/');
}

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
  if (!fromFile) data.set("operation", event.submitter?.value || "separate");
  jobBusy = true;
  activeJob = null;
  newJob.hidden = true;
  updateJobButtons();
  results.hidden = true;
  converted.hidden = true;
  results.querySelectorAll('audio').forEach(player => player.pause());
  announce(fromFile ? 'Файл илгээж байна…' : 'Холбоос шалгаж байна…');
  try {
    const {id} = await request('/api/jobs', {method: 'POST', body: data});
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
