'use strict';
const $ = id => document.getElementById(id);
const state = {examples: [], result: null, output: 'preview', revision: 0, resultRevision: -1, loading: false, saved: false, origin: '', guidance: null};
const DRAFT_KEY = 'pribor:editor:v1';
let saveTimer;
const show = (id, visible = true) => $(id).classList.toggle('hidden', !visible);
const text = (id, value) => { $(id).textContent = value; };
function announce(message) { text('live-status', message); }
function revealOutput() { if (window.innerWidth <= 760) document.querySelector('.output-panel').scrollIntoView({block: 'start'}); }
function markChanged() {
  state.revision++;
  state.guidance = null; show('needs-data', false);
  if (state.result) { show('stale-banner'); text('result-badge', 'Устарел'); $('result-badge').classList.remove('ready'); }
  else { text('result-badge', 'Ожидает данных'); if (!state.loading) show('empty-state'); }
  updateSourceStatus(); scheduleSave();
}
function updateSourceStatus() {
  try {
    const product = formValue();
    const count = Object.values(product.specs).filter(value => value !== null && String(value).trim()).length;
    text('source-status', count ? `${count} парам. · ${product.description ? 'с описанием' : 'без описания'}` : 'Один прибор');
  } catch { text('source-status', 'Проверьте поля'); }
}
function saveDraft() {
  clearTimeout(saveTimer);
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify({input: $('json-input').value,
      inputMode: document.querySelector('[data-input].active').dataset.input,
      result: state.result, saved: state.saved, origin: state.origin, output: state.output,
      revision: state.revision, resultRevision: state.resultRevision, guidance: state.guidance}));
    text('draft-status', 'Работа сохранена в этой вкладке.');
  } catch { text('draft-status', 'Автосохранение недоступно. Скопируйте ввод и скачайте результат перед закрытием.'); }
}
function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(saveDraft, 200); }
window.addEventListener('pagehide', saveDraft);
function addSpec(name = '', value = '', mark = true) {
  const row = document.createElement('div'); row.className = 'spec-row';
  const key = document.createElement('input'); key.type = 'text'; key.value = name; key.maxLength = 150; key.placeholder = 'Например, питание'; key.setAttribute('aria-label', 'Название характеристики'); key.className = 'spec-key';
  const input = document.createElement('input'); input.type = 'text'; input.value = value === null ? '' : String(value); input.maxLength = 1000; input.placeholder = 'данных нет'; input.setAttribute('aria-label', 'Значение характеристики'); input.className = 'spec-value'; input.dataset.original = input.value; input.dataset.type = typeof value;
  const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.className = 'remove-spec'; remove.setAttribute('aria-label', 'Удалить характеристику'); remove.addEventListener('click', () => { row.remove(); syncFromForm(); markChanged(); $('add-spec').focus(); });
  row.append(key, input, remove); $('spec-rows').append(row);
  if (mark) { syncFromForm(); markChanged(); key.focus(); }
}
function formValue() {
  const specs = Object.create(null);
  for (const row of $('spec-rows').children) {
    const key = row.querySelector('.spec-key').value;
    const input = row.querySelector('.spec-value');
    if (!key.trim() && !input.value.trim()) continue;
    if (!key.trim()) throw new Error('Укажите название каждой заполненной характеристики.');
    if (Object.hasOwn(specs, key.trim())) throw new Error('Названия характеристик не должны повторяться.');
    specs[key] = input.value === '' ? null : input.dataset.type === 'number' && input.dataset.original === input.value ? Number(input.value) : input.value;
  }
  return {name: $('name').value, manufacturer: $('manufacturer').value || null, specs, description: $('description').value || null, enrich: $('enrich').checked};
}
function inputError(message = '') { text('input-error', message); show('input-error', Boolean(message)); }
function syncFromForm() {
  try { $('json-input').value = JSON.stringify(formValue(), null, 2); inputError(); return true; }
  catch (error) { inputError(error.message); return false; }
}
function fillForm(value) {
  $('name').value = value.name ?? ''; $('manufacturer').value = value.manufacturer ?? ''; $('description').value = value.description ?? '';
  $('enrich').checked = value.enrich ?? true;
  $('spec-rows').replaceChildren(); Object.entries(value.specs || {}).forEach(([key, val]) => addSpec(key, val, false));
  if (!$('spec-rows').children.length) addSpec('', '', false);
}
function parseSource(requireName = true) {
  let value;
  try { value = JSON.parse($('json-input').value); } catch { throw new Error('Некорректный JSON. Проверьте кавычки, запятые и скобки.'); }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('JSON должен быть объектом товара.');
  if (requireName && (typeof value.name !== 'string' || !value.name.trim())) throw new Error('Укажите название прибора.');
  if (value.manufacturer != null && typeof value.manufacturer !== 'string') throw new Error('Производитель должен быть строкой или null.');
  if (value.description != null && typeof value.description !== 'string') throw new Error('Описание должно быть строкой или null.');
  if (value.enrich != null && typeof value.enrich !== 'boolean') throw new Error('Поиск в документации enrich должен быть true или false.');
  if (value.specs != null && (typeof value.specs !== 'object' || Array.isArray(value.specs))) throw new Error('Характеристики specs должны быть объектом.');
  if (value.specs && Object.values(value.specs).some(v => v !== null && !['string', 'number'].includes(typeof v))) throw new Error('Характеристики: только строки, числа или null.');
  if (value.specs && Object.values(value.specs).some(v => typeof v === 'number' &&
      (!Number.isFinite(v) || (Number.isInteger(v) && !Number.isSafeInteger(v))))) {
    throw new Error('Число выходит за безопасный диапазон JSON-редактора. Передайте точное значение строкой в кавычках.');
  }
  return value;
}
function activateTabs(selector, current, attribute) {
  document.querySelectorAll(selector).forEach(button => {
    const active = button.dataset[attribute] === current;
    button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
    show(button.getAttribute('aria-controls'), active);
  });
}
document.querySelectorAll('[data-input]').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.input === 'form') { try { fillForm(parseSource(false)); inputError(); } catch (error) { inputError(error.message); return; } }
  activateTabs('[data-input]', button.dataset.input, 'input');
}));
document.querySelectorAll('[data-output]').forEach(button => button.addEventListener('click', () => {
  state.output = button.dataset.output; activateTabs('[data-output]', state.output, 'output');
}));
document.querySelectorAll('[role=tablist]').forEach(list => list.addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault(); const tabs = [...list.querySelectorAll('[role=tab]')]; const index = tabs.indexOf(document.activeElement);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].click(); tabs[next].focus();
}));
$('form-panel').addEventListener('input', () => { syncFromForm(); markChanged(); });
$('json-input').addEventListener('input', () => {
  markChanged(); try { fillForm(parseSource()); inputError(); } catch (error) { inputError(error.message); }
});
$('add-spec').addEventListener('click', () => { if ($('spec-rows').children.length < 60) addSpec(); else inputError('Можно добавить до 60 характеристик.'); });
async function api(path, options) {
  let response;
  try { response = await fetch(path, options); } catch { throw new Error('Нет соединения с сервером. Ввод и предыдущий результат сохранены.'); }
  let body; try { body = await response.json(); } catch { throw new Error('Сервер вернул некорректный ответ. Ввод сохранён.'); }
  if (!response.ok) throw new Error(body.error?.message || 'Запрос не выполнен. Повторите позже.');
  return body;
}
function node(tag, className, value) {
  const element = document.createElement(tag); if (className) element.className = className; if (value != null) element.textContent = value; return element;
}
function section(label, count, copyValue) {
  const root = node('section', 'result-section'); const title = node('div', 'section-label', label);
  if (count) title.append(node('span', 'counter', count));
  if (copyValue != null) {
    const button = node('button', 'copy-block', 'Копировать'); button.type = 'button';
    button.setAttribute('aria-label', 'Копировать ' + label);
    button.addEventListener('click', () => copyText(copyValue, button)); title.append(button);
  }
  root.append(title); $('preview').append(root); return root;
}
function fieldLabel(value) {
  return ({manufacturer: 'Производитель', description: 'Описание / паспорт', specs: 'Характеристики'})[value] || value.replace(/^specs\./, 'Характеристика: ');
}
function render(result, saved = false, origin = '') {
  state.result = result; state.resultRevision = state.revision; state.saved = saved; state.origin = origin; state.guidance = null;
  const card = result.card; $('preview').replaceChildren(); $('result').open = true; show('previous-summary', false);
  section('SEO-заголовок', result.counts.seo_title + ' / 70', card.seo_title).append(node('h3', 'card-title', card.seo_title));
  const commercial = section('Commercial-блок для менеджера', '', card.commercial_block);
  commercial.classList.add('commercial-section');
  commercial.append(node('p', 'block-help', 'Кому подходит прибор и какие задачи решает'), node('p', 'commercial', card.commercial_block));
  section('SEO-описание', result.counts.seo_description + ' / 800–1200', card.seo_description).append(node('p', 'seo-text', card.seo_description));
  if (card.product_identity) section('Товар').append(node('p', 'product-identity', card.product_identity.name + ' · Производитель: ' + card.product_identity.manufacturer));
  const wrapper = node('div', 'table-wrap'); const table = node('table'); const head = node('thead'); const hr = node('tr');
  ['Параметр', 'Значение / источник'].forEach(value => hr.append(node('th', '', value))); head.append(hr); table.append(head); const body = node('tbody');
  card.characteristics.forEach(fact => {
    const row = node('tr'); const value = node('td', '', fact.value);
    if (fact.value === 'данных нет') row.classList.add('missing-characteristic');
    if (fact.source == null) value.append(node('small', 'missing-source', 'Нет в исходных материалах'));
    else if (fact.source === 'web') {
      const source = node('details', 'fact-source'); source.append(node('summary', '', 'Из документации производителя'));
      const url = safeSourceLink(fact.source_url);
      if (url) source.append(url);
      source.append(node('small', '', '«' + fact.quote + '»')); value.append(source);
    }
    else {
      const source = node('details', 'fact-source'); source.append(node('summary', '', fact.source === 'specs' ? 'Из входной таблицы' : fact.source === 'name' ? 'Из названия' : 'Из описания'));
      source.append(node('small', '', fact.source === 'specs' ? 'Исходные характеристики' : '«' + fact.quote + '»')); value.append(source);
    }
    row.append(node('td', '', fact.name), value); body.append(row);
  }); table.append(body); wrapper.append(table);
  const characteristics = section('Характеристики для карточки', '', card.characteristics.map(fact => fact.name + '\t' + fact.value).join('\n'));
  if (card.enrichment) characteristics.append(node('p', 'block-help', `Из входной таблицы: ${card.enrichment.provided} · Из текста и названия: ${card.enrichment.extracted}${card.enrichment.from_documents != null ? ' · Из документации: ' + card.enrichment.from_documents : ''} · Нет данных: ${card.enrichment.missing}`));
  characteristics.append(wrapper);
  if (card.research) {
    const sources = section('Источники обогащения');
    sources.append(node('p', 'block-help', card.research.status === 'exact_match' ? 'Модель найдена; цитаты новых характеристик сверены с текстом документов. Проверьте исполнение перед публикацией.' : 'Поиск не дал проверяемых данных о точной модели. Использованы исходные материалы.'));
    (card.research.sources || []).forEach(url => { const link = safeSourceLink(url); if (link) sources.append(link); });
  }
  const queries = node('div', 'queries'); card.search_queries.forEach(query => queries.append(node('span', 'query', query))); section('Поисковые запросы', '', card.search_queries.join('\n')).append(queries);
  const warning = node('details', 'warnings'); warning.open = card.missing_fields.length > 0 || card.warnings.some(value => /противоречие/i.test(value));
  warning.append(node('summary', '', card.missing_fields.length ? `Проверка перед публикацией · уточнить ${card.missing_fields.length}` : 'Проверка перед публикацией'));
  const list = node('ul'); card.missing_fields.forEach(value => list.append(node('li', '', fieldLabel(value) + ': требуется уточнение'))); card.warnings.forEach(value => list.append(node('li', '', value))); warning.append(list); $('preview').append(warning);
  text('json-output', result.exports.json); text('markdown-output', result.exports.markdown);
  text('result-meta', `${saved ? 'Сохранённый пример' : 'Живая генерация'} · ${result.metadata.model} · Попыток: ${result.metadata.attempts} · Токенов: ${result.metadata.usage.total_tokens}. Формат и обозначения проверены; смысл проверяет редактор.`);
  text('result-badge', saved ? 'Сохранённый пример' : 'Черновик готов'); $('result-badge').classList.add('ready');
  show('empty-state', false); show('result'); show('stale-banner', false); show('saved-banner', saved); show('needs-data', false);
  text('saved-origin', origin + ' Это сохранённый результат; новая генерация не выполнялась.');
  announce(saved ? 'Открыт сохранённый пример.' : 'Черновик готов. Проверьте текст перед публикацией.');
  scheduleSave();
}
function showNeedsData(body) {
  state.guidance = body;
  if (state.result) { $('result').open = false; show('previous-summary'); }
  text('needs-data-message', body.message);
  const known = (body.known_characteristics || []).filter(fact => fact.value !== 'данных нет').map(fact => fact.name + ': ' + fact.value).join('; ');
  text('known-summary', body.summary || (known ? 'Известно: ' + known : ''));
  show('known-summary', Boolean(body.summary || known));
  $('needs-data-list').replaceChildren();
  (body.missing_fields || []).forEach(value => $('needs-data-list').append(node('li', '', fieldLabel(value) + ': требуется уточнение')));
  (body.next_steps || []).forEach(value => $('needs-data-list').append(node('li', '', value)));
  text('needs-data-meta', body.saved_example ? 'Сохранённая справка. Новая генерация не запускалась.' : body.api_called ? 'Получена краткая справка. SEO-карточка ещё не создана.' : 'Модель не вызывалась. Добавьте источник и попробуйте снова.');
  show('saved-banner', Boolean(body.saved_example));
  if (body.saved_example) text('saved-origin', (body.origin || '') + ' Это сохранённый результат; новая генерация не выполнялась.');
  show('needs-data'); show('empty-state', false); text('result-badge', 'Нужны данные'); $('result-badge').classList.remove('ready');
  announce('Для карточки нужны дополнительные сведения. ' + body.message); scheduleSave();
}
$('complete-source').addEventListener('click', () => {
  try { fillForm(parseSource(false)); activateTabs('[data-input]', 'form', 'input'); $('description').focus(); $('description').scrollIntoView({block: 'center'}); }
  catch (error) { inputError(error.message); $('json-input').focus(); }
});
function safeSourceLink(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return null;
    const link = node('a', 'source-link', value); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link;
  } catch { return null; }
}
$('product-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.loading) return;
  let product; try { if (!$('form-panel').classList.contains('hidden') && !syncFromForm()) return; product = parseSource(); inputError(); } catch (error) { inputError(error.message); return; }
  const revision = state.revision; state.loading = true; updateExampleControls(); $('generate').disabled = true; text('generate-label', 'Создаём карточку…');
  document.querySelector('.output-panel').setAttribute('aria-busy', 'true'); show('error-banner', false); show('needs-data', false); show('loading-state'); show('empty-state', false); announce('Генерация выполняется. Обычно это занимает несколько секунд.');
  try {
    const result = await api('/api/generate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(product)});
    if (result.status === 'needs_data') {
      if (state.revision === revision) showNeedsData(result);
      else { text('error-banner', 'Исходные данные изменились во время проверки. Повторите действие для текущего ввода.'); show('error-banner'); }
    } else {
      render(result); state.resultRevision = revision;
      if (state.revision !== revision) { show('stale-banner'); text('result-badge', 'Устарел'); $('result-badge').classList.remove('ready'); }
    }
  } catch (error) { text('error-banner', error.message); show('error-banner'); announce('Генерация не завершена. ' + error.message); }
  finally { state.loading = false; updateExampleControls(); $('generate').disabled = false; text('generate-label', 'Создать карточку'); document.querySelector('.output-panel').setAttribute('aria-busy', 'false'); show('loading-state', false); if (!state.result && !state.guidance) show('empty-state'); saveDraft(); revealOutput(); }
});
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); if (!state.loading) $('product-form').requestSubmit(); }
});
function selectedExample() { return state.examples.find(example => example.id === $('example-select').value); }
function updateExampleControls() {
  const example = selectedExample();
  $('example-select').disabled = state.loading || !state.examples.length;
  $('load-example').disabled = !example || state.loading;
  $('show-example').disabled = !example || state.loading;
  text('example-origin', (example?.origin || 'Выберите пример из списка.') + ' Просмотр сохранённого результата не вызывает модель.');
}
$('example-select').addEventListener('change', updateExampleControls);
function loadExample(example) { fillForm(example.input); $('json-input').value = JSON.stringify(example.input, null, 2); inputError(); markChanged(); }
$('load-example').addEventListener('click', () => { const example = selectedExample(); if (example) loadExample(example); else inputError('Сначала выберите пример.'); });
$('show-example').addEventListener('click', async () => {
  const example = selectedExample(); if (!example) { inputError('Сначала выберите пример.'); return; }
  if (state.loading) return;
  state.loading = true; updateExampleControls(); $('generate').disabled = true;
  try {
    const body = await api('/api/examples/' + encodeURIComponent(example.id) + '/result'); loadExample(example);
    if (body.result.status === 'needs_data') showNeedsData({...body.result, saved_example: true, origin: body.origin});
    else render(body.result, true, body.origin);
    show('error-banner', false);
  }
  catch (error) { text('error-banner', error.message); show('error-banner'); }
  finally { state.loading = false; updateExampleControls(); $('generate').disabled = false; saveDraft(); revealOutput(); }
});
async function copyText(value, button) {
  const original = button.textContent;
  try { await navigator.clipboard.writeText(value); button.textContent = 'Скопировано'; button.disabled = true; announce('Результат скопирован.'); setTimeout(() => { button.textContent = original; button.disabled = false; }, 1800); }
  catch { text('error-banner', 'Браузер запретил копирование. Выделите текст вручную или скачайте файл.'); show('error-banner'); }
}
$('copy').addEventListener('click', () => {
  if (state.result) copyText(state.result.exports[state.output === 'preview' ? 'markdown' : state.output], $('copy'));
});
function download(format) {
  if (!state.result) return;
  const url = URL.createObjectURL(new Blob([state.result.exports[format]], {type: format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8'}));
  const link = node('a'); link.href = url; link.download = 'product-card.' + (format === 'json' ? 'json' : 'md'); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('download-json').addEventListener('click', () => download('json'));
$('download-markdown').addEventListener('click', () => download('markdown'));
async function init() {
  addSpec('', '', false); syncFromForm();
  try {
    const saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY));
    if (saved && typeof saved.input === 'string') {
      $('json-input').value = saved.input;
      try { fillForm(parseSource(false)); } catch { saved.inputMode = 'json'; }
      activateTabs('[data-input]', saved.inputMode === 'json' ? 'json' : 'form', 'input');
      state.revision = Number.isSafeInteger(saved.revision) ? saved.revision : 0;
      if (saved.result?.card && saved.result?.exports && saved.result?.metadata) {
        render(saved.result, Boolean(saved.saved), saved.origin || ''); state.resultRevision = saved.resultRevision;
        if (state.revision !== state.resultRevision) { show('stale-banner'); text('result-badge', 'Устарел'); $('result-badge').classList.remove('ready'); }
      }
      if (saved.guidance?.status === 'needs_data') showNeedsData(saved.guidance);
      if (['preview', 'json', 'markdown'].includes(saved.output)) { state.output = saved.output; activateTabs('[data-output]', state.output, 'output'); }
      text('draft-status', 'Работа восстановлена в этой вкладке.');
    }
  } catch {
    state.result = null; state.guidance = null; $('preview').replaceChildren(); show('result', false); show('needs-data', false); show('empty-state');
    text('result-badge', 'Ожидает данных'); $('result-badge').classList.remove('ready');
    text('draft-status', 'Начните с исходных данных или примера.');
  }
  updateSourceStatus();
  const results = await Promise.allSettled([api('/api/health'), api('/api/examples')]);
  if (results[0].status === 'fulfilled') { const health = results[0].value; if (!health.ready) { text('config-status', health.message); show('config-status'); } }
  else { text('config-status', 'Нет соединения с сервером. Введённые данные останутся на странице.'); show('config-status'); }
  if (results[1].status === 'fulfilled') {
    state.examples = results[1].value.examples;
    $('example-select').options[0].textContent = 'Выберите набор данных';
    state.examples.forEach(example => { const option = node('option', '', example.label); option.value = example.id; $('example-select').append(option); });
    if (state.examples.length) {
      $('example-select').value = state.examples[0].id;
      $('example-select').disabled = false;
      updateExampleControls();
    } else { text('example-origin', 'Сохранённые примеры пока недоступны. Можно заполнить данные вручную.'); }
  } else { text('example-origin', 'Не удалось загрузить примеры. Проверьте, что сервер запущен, и обновите страницу.'); $('example-notes').open = true; }
}
init();
