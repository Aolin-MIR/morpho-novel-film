'use strict';

const archive = location.pathname.startsWith('/archive/screenings');
const reviewing = location.pathname.startsWith('/review-films');
const libraryRoot = reviewing ? '/review-films' : (archive ? '/archive/screenings' : '/screenings');
const libraryApi = '/api' + libraryRoot;
const get = id => document.getElementById(id);

function make(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function clock(value) {
  const whole = Math.floor(value);
  return Math.floor(whole / 60) + ':' + String(whole % 60).padStart(2, '0');
}

async function read(url) {
  const response = await fetch(url, {cache: 'no-store'});
  if (!response.ok) throw new Error('无法加载成片。');
  return response.json();
}

function renderFilmCards(id, films) {
  const group = get(id);
  const library = group.querySelector('.screening-library');
  group.hidden = !films.length;
  for (const film of films) {
    const path = libraryRoot + '/' + encodeURIComponent(film.slug);
    const link = make('a', undefined, 'screen-card');
    link.href = path;
    const poster = make('img');
    poster.src = path + '/poster.jpg';
    poster.alt = '';
    poster.loading = 'lazy';
    const body = make('div');
    const facts = make('p', film.book + ' · ' + film.edition + ' · ' + clock(film.duration), 'screen-facts');
    body.append(facts, make('h3', film.title), make('p', film.summary));
    link.append(poster, body);
    library.append(link);
  }
}

function studyIssue(study) {
  let saved = study.annotation?.issue || '';
  const form = make('form', undefined, 'study-issue');
  const fields = make('fieldset');
  const label = make('label', '发现的问题');
  const input = make('textarea');
  input.id = 'study-issue-' + study.id;
  input.rows = 3;
  input.maxLength = 1000;
  input.placeholder = '可以写下问题出现的时刻、动作或画面';
  input.value = saved;
  label.htmlFor = input.id;
  const actions = make('div', undefined, 'study-issue-actions');
  const button = make('button', '保存问题');
  button.type = 'submit';
  button.disabled = true;
  const status = make('p', '', 'study-issue-status');
  status.setAttribute('role', 'status');
  actions.append(button, status);
  fields.append(label, input, actions);
  form.append(fields);
  input.addEventListener('input', () => {
    button.disabled = input.value.trim() === saved;
    status.textContent = button.disabled ? '' : '尚未保存';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (fields.disabled || button.disabled) return;
    fields.disabled = true;
    status.textContent = '正在保存…';
    try {
      const response = await fetch('/api/review-films/study-labels', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: study.id, issue: input.value}),
      });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.error || '暂时无法保存，输入的内容仍保留，请重试。';
        return;
      }
      saved = result.issue;
      input.value = saved;
      status.textContent = saved ? '已保存' : '已清空';
    } catch {
      status.textContent = '暂时无法保存，输入的内容仍保留，请重试。';
    } finally {
      fields.disabled = false;
      button.disabled = input.value.trim() === saved;
    }
  });
  return form;
}

function studyAnnotation(study) {
  const riskLabels = [
    ['easy', '容易'],
    ['unlikely', '不容易'],
    ['', '未判断'],
  ];
  let current = study.annotation || {scenes: [], continuity_risk: ''};

  const details = make('details', undefined, 'study-annotation');
  const summary = make('summary');
  const form = make('form');
  const fields = make('fieldset', undefined, 'study-label-fields');
  const input = make('input');
  const inputLabel = make('label', '适用场景');
  input.id = 'scene-label-' + study.id;
  inputLabel.htmlFor = input.id;
  input.type = 'text';
  input.maxLength = 24;
  input.placeholder = '填写一个场景';
  const add = make('button', '添加');
  add.type = 'submit';
  const entry = make('div', undefined, 'study-label-entry');
  entry.append(input, add);
  const tags = make('ul', undefined, 'study-label-tags');

  const risk = make('fieldset', undefined, 'study-label-risk');
  risk.append(make('legend', '容易看成穿帮吗？'));
  const options = riskLabels.map(([value, label]) => {
    const button = make('button', label);
    button.type = 'button';
    button.dataset.risk = value;
    button.addEventListener('click', () => save({continuity_risk: value}));
    risk.append(button);
    return button;
  });
  const status = make('p', '', 'study-label-status');
  status.setAttribute('role', 'status');
  fields.append(inputLabel, entry, tags, risk);
  form.append(fields, status);
  details.append(summary, form);

  function paint() {
    const indicator = current.continuity_risk === 'easy' ? '容易看成穿帮' :
      (current.continuity_risk === 'unlikely' ? '不易看成穿帮' : '');
    const labels = ['试片标注', current.scenes.length ? current.scenes.length + ' 个场景' : '', indicator];
    summary.textContent = labels.filter(Boolean).join(' · ');
    summary.dataset.risk = current.continuity_risk;
    tags.hidden = current.scenes.length === 0;
    tags.replaceChildren(...current.scenes.map(scene => {
      const tag = make('li');
      const remove = make('button', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', '删除标签 ' + scene);
      remove.addEventListener('click', () => save({remove_scene: scene}));
      tag.append(make('span', scene), remove);
      return tag;
    }));
    for (const option of options) {
      option.setAttribute('aria-pressed', String(option.dataset.risk === current.continuity_risk));
    }
  }

  async function save(change) {
    if (fields.disabled) return;
    const activeRisk = options.includes(document.activeElement) ? document.activeElement : null;
    fields.disabled = true;
    status.textContent = '正在保存…';
    try {
      const response = await fetch('/api/review-films/study-labels', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: study.id, ...change}),
      });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.error || '暂时无法保存，稍后再试。';
        return;
      }
      current = result;
      if ('add_scene' in change) input.value = '';
      paint();
      status.textContent = '已保存';
    } catch {
      status.textContent = '暂时无法保存，稍后再试。';
    } finally {
      fields.disabled = false;
      (activeRisk || input).focus();
    }
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    const scene = input.value.trim();
    if (scene) save({add_scene: scene});
  });
  paint();
  return details;
}

function appendStudyCards(library, studies) {
  for (const study of studies) {
    const card = make('article', undefined, 'screen-card');
    card.id = 'study-' + study.id;
    const video = make('video');
    video.controls = true;
    video.playsInline = true;
    video.preload = 'none';
    video.src = study.playback;
    if (study.poster) video.poster = study.poster;
    const title = study.title.replace(/^[A-Z]+-\d+[A-Z]?\s+/, '');
    const direction = study.classification_status === 'confirmed'
      ? study.directions.join(' · ') : '待确认';
    const methods = study.methods.length ? study.methods.join(' · ') : '待确认';
    video.setAttribute('aria-label', title);
    const body = make('div');
    const details = make('dl', undefined, 'study-specific-methods');
    details.append(make('dt', '具体方法'), make('dd', methods));
    body.append(make('p', '美学风格／表达方向', 'study-direction-label'),
                make('h3', direction, 'study-direction-name'),
                details,
                make('p', title, 'study-film-title'),
                make('p', study.book + (study.edition ? ' · ' + study.edition : ''), 'screen-facts'));
    if (reviewing) body.append(studyIssue(study), studyAnnotation(study));
    card.append(video, body);
    library.append(card);
  }
}

function renderStudies(studies) {
  const group = get('aesthetic');
  const library = group.querySelector('.screening-library');
  const current = reviewing ? studies.filter(study => study.collection === 'contemporary-fiction') : [];
  if (current.length) get('screening-library').prepend(group);
  appendStudyCards(library, current.length ? current : studies);

  if (current.length && current.length < studies.length) {
    const archive = make('details', undefined, 'study-archive');
    const previous = make('div', undefined, 'screening-library');
    appendStudyCards(previous, studies.filter(study => study.collection !== 'contemporary-fiction'));
    archive.append(make('summary', '往期试片'), previous);
    group.append(archive);
  }
  group.hidden = !studies.length;
}

function renderMethods(methods) {
  const kindLabels = {
    'art-style': '艺术风格',
    'expression-direction': '表达方向',
    technique: '具体方法',
    unconfirmed: '待确认',
  };
  const section = get('methods');
  section.hidden = !methods.length;
  const groups = new Map();
  for (const method of methods) {
    if (!groups.has(method.group)) groups.set(method.group, []);
    groups.get(method.group).push(method);
  }
  for (const [name, rows] of groups) {
    const details = make('details');
    details.append(make('summary', name));
    const list = make('ul');
    for (const item of rows) {
      const row = make('li');
      row.append(make('strong', item.name), make('span', kindLabels[item.classification_kind], 'method-kind'),
                 make('p', item.example), make('p', item.method));
      if (item.directions.length) {
        row.append(make('p', '表达方向　' + item.directions.join(' · '), 'method-directions'));
      }
      list.append(row);
    }
    details.append(list);
    section.append(details);
  }
}

function renderScript(film, seek) {
  const script = film.script;
  if (!script) {
    get('script-note').textContent = film.script_state || '这版影片还没有随片归档的台本。';
    return;
  }
  get('script-note').textContent = script.edition + ' · ' + script.note;
  const origins = new Map(script.sources.map(source => [source.id, source]));
  const sourceNodes = script.sources.map(source => {
    const name = source.url ? make('a', source.name) : make('span', source.name);
    if (source.url) {
      name.href = source.url;
      name.rel = 'noopener noreferrer';
      name.target = '_blank';
    }
    const item = make('div');
    const term = make('dt');
    term.append(name, make('small', ' · ' + source.kind + ' · ' + source.use));
    item.append(term, make('dd', source.role));
    return item;
  });
  get('script-sources').replaceChildren(...sourceNodes);
  get('script-origins').hidden = false;

  const sceneNodes = script.scenes.map((scene, index) => {
    const item = make('details', undefined, 'script-scene');
    item.open = index === 0;
    const summary = make('summary');
    summary.append(make('time', clock(scene.start)), make('strong', scene.title));
    const body = make('div', undefined, 'script-scene-body');
    const play = make('button', '定位到此', 'script-seek');
    play.type = 'button';
    play.addEventListener('click', () => seek(scene.start));
    body.append(play, make('p', scene.action));
    if (scene.lines?.length) {
      const lines = make('dl', undefined, 'script-lines');
      for (const line of scene.lines) {
        const entry = make('div');
        const label = [line.kind, line.speaker, line.evidence].filter(Boolean).join(' · ');
        const cue = make('button', clock(line.start), 'script-line-time');
        cue.type = 'button';
        cue.setAttribute('aria-label', '定位到 ' + clock(line.start) + ' 的' + line.kind);
        cue.addEventListener('click', () => seek(line.start));
        const term = make('dt');
        term.append(cue, document.createTextNode(label));
        entry.append(term, make('dd', line.text));
        lines.append(entry);
      }
      body.append(lines);
    }
    const sources = (scene.sources || []).map(id => origins.get(id).name);
    if (sources.length) body.append(make('p', '本段：' + sources.join('、'), 'script-scene-source'));
    item.append(summary, body);
    return item;
  });
  get('script-scenes').replaceChildren(...sceneNodes);
  const expand = get('script-all');
  expand.hidden = sceneNodes.length < 2;
  const labelExpand = () => {
    expand.textContent = sceneNodes.every(scene => scene.open) ? '收起全部场次' : '展开全部场次';
  };
  for (const node of sceneNodes) node.addEventListener('toggle', labelExpand);
  expand.addEventListener('click', () => {
    const next = sceneNodes.some(scene => !scene.open);
    for (const scene of sceneNodes) scene.open = next;
    labelExpand();
  });
}

function renderFilm(film) {
  const root = libraryRoot + '/' + encodeURIComponent(film.slug);
  get('screening').classList.add('watching');
  const video = get('feature-video');
  document.title = film.title + ' · 电影制作台';
  get('heading').textContent = film.title;
  get('edition').textContent = film.book + ' · ' + film.edition + ' · ' + clock(film.duration);
  get('edition').hidden = false;
  get('intro').textContent = film.summary;
  get('intro').hidden = false;
  get('director-file').href = root + '/director.vtt';
  video.poster = root + '/poster.jpg';
  video.src = root + '/film.mp4';
  get('screening-message').textContent = '';
  video.addEventListener('error', () => {
    get('screening-message').textContent = '视频暂时无法播放，可以刷新后再试。';
  });

  const chapters = get('screening-chapters');
  const entries = film.cues.map(cue => {
    const button = make('button');
    button.type = 'button';
    button.append(make('time', clock(cue.start)), make('span', cue.title));
    button.addEventListener('click', () => {
      video.currentTime = cue.start;
      sync();
    });
    chapters.append(button);
    return button;
  });

  let showing = -2;
  function sync() {
    // Media timelines seek in microseconds; compare timestamps at that precision.
    const now = Math.min(Math.round(video.currentTime * 1e6) + 1, Math.round(film.duration * 1e6) - 1);
    const index = film.cues.findIndex(cue =>
      Math.round(cue.start * 1e6) <= now && now < Math.round(cue.end * 1e6));
    if (index === showing) return;
    if (showing >= 0) entries[showing].removeAttribute('aria-current');
    showing = index;
    const data = film.cues[index];
    get('cue-time').textContent = data ? clock(data.start) + '—' + clock(data.end) : '　';
    get('cue-title').textContent = data ? data.title : '　';
    get('cue-line').textContent = data ? data.line : '　';
    const techniques = get('cue-craft');
    techniques.replaceChildren();
    if (data) {
      entries[index].setAttribute('aria-current', 'true');
      for (const [label, explanation] of Object.entries({...data.craft, '创作目标':data.aim})) {
        const item = make('div');
        item.append(make('dt',label),make('dd',explanation));
        techniques.append(item);
      }
    }
  }

  const toggle = get('director-toggle');
  toggle.addEventListener('click', () => {
    const body = get('director-body');
    body.hidden = !body.hidden;
    toggle.setAttribute('aria-expanded',String(!body.hidden));
    toggle.textContent = body.hidden ? '展开' : '收起';
  });
  ['loadedmetadata','timeupdate','seeking','seeked','ended'].forEach(event => video.addEventListener(event,sync));
  sync();
  renderScript(film, time => {
    video.currentTime = time;
    sync();
  });
  get('screening-player').hidden = false;
  if (location.hash === '#film-script') get('film-script').scrollIntoView();
}

(async () => {
  get('screening-message').textContent = '正在读取影片…';
  try {
    const film = location.pathname.slice(libraryRoot.length + 1);
    if (film) renderFilm(await read(libraryApi + '/' + encodeURIComponent(film)));
    else {
      const editions = await read(libraryApi);
      const clips = await read(libraryApi + '/studies');
      if (archive) get('heading').textContent = '制作归档';
      if (reviewing) {
        document.title = '影片审看 · 电影制作台';
        get('heading').textContent = '影片审看';
        get('intro').textContent = '看四本书的当前剪辑，或按美学方向与具体方法看试片。';
        get('intro').hidden = false;
        get('long-films').querySelector('h2').textContent = '影片审看';
        renderFilmCards('long-films', editions);
        renderMethods(await read(libraryApi + '/methods'));
      } else {
        renderFilmCards('long-films', editions.filter(edition => edition.duration >= 300));
        renderFilmCards('short-films', editions.filter(edition => edition.duration < 300));
      }
      renderStudies(clips);
      get('screening-library').hidden = false;
      get('screening-message').textContent = editions.length || clips.length ? '' : '还没有可播放的影片。';
      if (location.hash) {
        const target = get(location.hash.slice(1));
        const collapsed = target?.closest('.study-archive');
        if (collapsed) collapsed.open = true;
        target?.scrollIntoView();
      }
    }
  } catch (error) {
    get('screening-message').textContent = '暂时无法打开这个放映页面。';
  }
})();
