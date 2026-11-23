const el = id => document.getElementById(id);
const make = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
let state, book = sessionStorage.getItem('morpho-book') || 'shouhuo', view = 'shots', selection = '';
const labels = {queued:'待制作', running:'制作中', completed:'已生成', received:'已存本地', failed:'待处理', blocked:'待处理'};
const chinaTime = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', hour:'2-digit', minute:'2-digit', hour12:false});
const detailArea = el('shot-detail');
let journalKey = '';

function statusOf(row) { return row.received ? 'received' : row.state; }
function describe(field, value) {
  if (!value) return;
  detailArea.append(make('h4',field),make('p',value));
}
function selectView(next) {
  view = next;
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === next)));
  for (const key of ['shots','scripts','notes','methods']) el(key).hidden = key !== next;
  if (next === 'methods') showMethods();
}
function showScripts(films) {
  const list = el('book-scripts');
  if (!films.length) {
    list.replaceChildren(make('p', '这部作品还没有归档的影片台本。'));
    return;
  }
  list.replaceChildren(...films.map(film => {
    const article = make('article', undefined, 'article');
    const open = make('a', film.title);
    open.href = film.url;
    const heading = make('h3');
    heading.append(open);
    article.append(make('small', film.edition), heading);
    return article;
  }));
}

function showBook() {
  const current = state.books.find(p => p.slug === book) || state.books[0];
  book = current.slug;
  el('projects').replaceChildren(...state.books.map(p => {
    const button = make('button',p.title);
    button.setAttribute('aria-pressed', String(p.slug === book));
    button.onclick = () => {
      book = p.slug;
      selection = '';
      sessionStorage.setItem('morpho-book',book);
      showBook();
      showEmptyDetail();
    };
    return button;
  }));
  el('hero-label').textContent = book === 'shouhuo' ? '阎连科小说改编' : '';
  el('hero-label').hidden = book !== 'shouhuo';
  el('book-title').textContent = current.title;
  el('book-intent').textContent = current.intent;
  el('hero-art').hidden = book !== 'shouhuo';
  const takes = current.jobs.filter(shot => shot.kind === 'render');
  el('counters').replaceChildren(...[
    ['待拍',takes.filter(shot => shot.state === 'queued').length],
    ['制作中',takes.filter(shot => shot.state === 'running').length],
    ['已生成',takes.filter(shot => shot.state === 'completed').length],
    ['已存本地',takes.filter(shot => shot.received).length],
  ].map(([name,value]) => {
    const unit = make('div');
    unit.append(make('dd',value),make('dt',name));
    return unit;
  }));
  const watch = el('book-screenings');
  watch.replaceChildren(...current.screenings.map(film => {
    const link = make('a', '观看《'+film.title+'》 · '+film.edition);
    link.href = '/screenings/' + encodeURIComponent(film.slug);
    return link;
  }));
  watch.hidden = !current.screenings.length;
  showShots();
  showScripts(current.scripts || []);
  const timeline = el('journal');
  const currentJournal = JSON.stringify([book,current.records]);
  if (currentJournal === journalKey) return;
  journalKey = currentJournal;
  timeline.replaceChildren();
  for (const row of current.records) {
    const article = make('article',undefined,'article');
    article.append(make('small',row.status),make('h3',row.title),make('p',row.intent));
    if (row.skill) article.append(make('p',row.skill));
    if (row.evidence) article.append(make('p',row.evidence));
    if (row.video) {
      const video = make('video');
      video.controls = true; video.preload = 'metadata'; video.src = row.video;
      article.append(video);
    }
    if (row.image) {
      const image = make('img');
      image.src = row.image; image.alt = row.title;
      article.append(image);
    }
    if (row.parameters) {
      const link = make('a','效果参数');
      link.href = row.parameters;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      article.append(link);
    }
    if (row.source) {
      const link = make('a','来源');
      link.href = row.source;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      article.append(link);
    }
    timeline.append(article);
  }
}
function showShots() {
  if (!state) return;
  const current = state.books.find(p => p.slug === book);
  const query = el('shot-search').value.trim().toLowerCase();
  const filter = el('state-filter').value;
  const list = el('shot-list');
  const filtered = current.jobs.filter(row => {
    const wanted = !filter || statusOf(row) === filter || (filter === 'completed' && row.state === 'completed');
    return wanted && (row.title + ' ' + row.section).toLowerCase().includes(query);
  });
  const order = {running:0, failed:1, completed:2, queued:3, blocked:4};
  filtered.sort((a,b) => order[a.state] - order[b.state] || b.updated_at.localeCompare(a.updated_at));
  list.replaceChildren();
  for (const row of filtered) {
    const button = make('button',undefined,'shot-row');
    button.setAttribute('aria-selected',String(selection === row.id));
    const body = make('span');
    body.append(make('strong',row.title));
    if (row.section) body.append(make('small',row.section));
    else if (row.duration) body.append(make('small',row.duration+' 秒'));
    button.append(body,make('span',labels[statusOf(row)],'status '+statusOf(row)));
    button.onclick = () => openShot(row.id);
    list.append(button);
  }
  if (!filtered.length) list.append(make('p','没有符合条件的镜头。','message'));
}

function showEmptyDetail() {
  detailArea.replaceChildren(
    make('h3','审看镜头'),
    make('p','选择一条记录，查看拍摄意图、提示词和出片证据。')
  );
}

async function openShot(id) {
  selection = id;
  showShots();
  detailArea.replaceChildren(make('p','正在读取镜头…'));
  try {
    const response = await fetch('/api/shots/'+id,{cache:'no-store'});
    if (!response.ok) throw Error();
    const shot = await response.json();
    if (selection !== id) return;
    detailArea.replaceChildren(make('h3',shot.title));
    detailArea.append(make('span',labels[statusOf(shot)],'status '+statusOf(shot)));
    if (shot.has_media) {
      const video = make('video'); video.controls = true; video.preload = 'metadata'; video.src = shot.playback;
      detailArea.append(video);
    }
    describe('作者意图',shot.creative.intent);
    describe('本镜选定的手法',shot.creative.skill);
    for (const method of shot.creative.art || []) {
      describe(method.name, method.execution);
      if (method.capture) describe('底片拍法', method.capture);
    }
    describe('本轮改进',shot.creative.change);
    if (shot.contract) {
      describe('画面与光线',shot.contract.style);
      describe('人物动作',shot.contract.action);
      describe('摄影方法',shot.contract.camera);
      describe('收镜条件',shot.contract.outcome);
      describe('现场声',shot.contract.soundscape);
      if (shot.contract.speech?.length) {
        describe('拍摄台词', shot.contract.speech.map(line => {
          const person = shot.contract.pictures?.[line.picture - 1]?.use;
          return (person ? person + ' · ' : '') + '「' + line.text + '」';
        }).join('\n'));
        describe('台本来源', '当前镜头的 Harness 拍摄单。');
      }
    }
    if (shot.evidence.lines) {
      for (const line of shot.evidence.lines) {
        const wording = {exact:'台词转写吻合', pronoun_homophone:'代词同音，需从画面确认指代', mismatch:'台词待复听'};
        describe(wording[line.word_match], line.expected+'／'+line.transcript);
      }
    }
    if (shot.evidence.cuts) {
      for (const cut of shot.evidence.cuts) describe(cut.at+' 秒', [cut.before,cut.after,
        ...((cut.eligible_methods || []).map(m => m.description))].filter(Boolean).join(' → '));
    }
    if (shot.prompt) {
      const disclosure = make('details');
      disclosure.append(make('summary',shot.prompt_state === '实际提交' ? '完整提示词' : '拍摄方案提示词'),make('pre',shot.prompt));
      const copy = make('button','复制提示词','save');
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(shot.prompt); copy.textContent='已复制'; }
        catch { copy.textContent='请选中文本后复制'; }
      };
      disclosure.append(copy);
      detailArea.append(disclosure);
    }
    if (shot.error) detailArea.append(make('p',shot.error));
    const heading = make('label','审看备注');
    const field = make('textarea');
    field.maxLength = 3000; field.value = shot.note; field.id = 'note-field';
    heading.htmlFor = field.id;
    const save = make('button','保存备注','save');
    save.onclick = async () => {
      const result = await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id,text:field.value})});
      save.textContent = result.ok ? '已保存' : '保存失败';
    };
    if (location.pathname === '/studio') describe('审看备注', shot.note);
    else detailArea.append(heading,field,save);
  } catch {
    if (selection === id) detailArea.replaceChildren(make('p','镜头记录暂时不可读，请刷新。'));
  }
}

function showMethods() {
  if (!state) return;
  const list = el('method-list');
  const keyword = el('method-search').value.trim().toLowerCase();
  const status = el('method-filter').value;
  const filtered = state.catalog.filter(x => (!status || (status === '重点' ? x.priority : x.status === status)) &&
    (x.name+' '+x.group+' '+x.example).toLowerCase().includes(keyword));
  list.replaceChildren(make('p',filtered.length+' 项方法','count-line'));
  for (const x of filtered) {
    const card = make('article',undefined,'method'+(x.priority?' priority':''));
    card.append(make('small',x.group+' · '+x.status),make('h3',x.name),make('p',x.purpose || x.example));
    const details = make('details');
    details.append(make('summary','拍摄与制作'),make('p',x.prompt || x.method));
    if (x.source) {
      const source = make('a','参考来源'); source.href=x.source; source.target='_blank'; source.rel='noopener noreferrer';
      details.append(source);
    }
    card.append(details);
    list.append(card);
  }
}
async function update() {
  try {
    const response = await fetch('/api/state',{cache:'no-store'});
    if (!response.ok) throw Error();
    state = await response.json();
    el('live').textContent = '已同步 '+chinaTime.format(new Date());
    el('live').classList.remove('stale');
    showBook();
    if (view === 'methods' && !el('method-list').childElementCount) showMethods();
  } catch {
    el('live').textContent = '连接中断 · 等待同步';
    el('live').classList.add('stale');
  }
}
document.querySelectorAll('[data-view]').forEach(b => {b.onclick=() => selectView(b.dataset.view);});
el('refresh').onclick=update;
el('shot-search').oninput=showShots; el('state-filter').onchange=showShots;
el('method-search').oninput=showMethods; el('method-filter').onchange=showMethods;
showEmptyDetail(); update(); setInterval(update,10000);
