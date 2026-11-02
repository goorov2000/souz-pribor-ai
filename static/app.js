'use strict';
const $ = id => document.getElementById(id);
const state = {examples: [], result: null, output: 'preview', revision: 0, resultRevision: -1, loading: false};
const show = (id, visible = true) => $(id).classList.toggle('hidden', !visible);
const text = (id, value) => { $(id).textContent = value; };
function announce(message) { text('live-status', message); }
function markChanged() {
  state.revision++;
  if (state.result) { show('stale-banner'); text('result-badge', 'Устарел'); $('result-badge').classList.remove('ready'); }
}
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
  return {name: $('name').value, manufacturer: $('manufacturer').value || null, specs, description: $('description').value || null};
}
function inputError(message = '') { text('input-error', message); show('input-error', Boolean(message)); }
function syncFromForm() {
  try { $('json-input').value = JSON.stringify(formValue(), null, 2); inputError(); return true; }
  catch (error) { inputError(error.message); return false; }
}
function fillForm(value) {
  $('name').value = value.name ?? ''; $('manufacturer').value = value.manufacturer ?? ''; $('description').value = value.description ?? '';
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
function section(label, count) {
  const root = node('section', 'result-section'); const title = node('div', 'section-label', label);
  if (count) title.append(node('span', 'counter', count)); root.append(title); $('preview').append(root); return root;
}
function fieldLabel(value) {
  return ({manufacturer: 'Производитель', description: 'Описание / паспорт', specs: 'Характеристики'})[value] || value.replace(/^specs\./, 'Характеристика: ');
}
function render(result, saved = false, origin = '') {
  state.result = result; state.resultRevision = state.revision;
  const card = result.card; $('preview').replaceChildren();
  section('SEO-заголовок', result.counts.seo_title + ' / 70').append(node('h3', 'card-title', card.seo_title));
  section('SEO-описание', result.counts.seo_description + ' / 800–1200').append(node('p', 'seo-text', card.seo_description));
  const wrapper = node('div', 'table-wrap'); const table = node('table'); const head = node('thead'); const hr = node('tr');
  ['Параметр', 'Значение / источник'].forEach(value => hr.append(node('th', '', value))); head.append(hr); table.append(head); const body = node('tbody');
  card.characteristics.forEach(fact => {
    const row = node('tr'); const value = node('td', '', fact.value);
    value.append(node('small', '', fact.source === 'specs' ? 'Исходные характеристики' : 'Из описания: «' + fact.quote + '»'));
    row.append(node('td', '', fact.name), value); body.append(row);
  }); table.append(body); wrapper.append(table); section('Паспортные данные').append(wrapper);
  const queries = node('div', 'queries'); card.search_queries.forEach(query => queries.append(node('span', 'query', query))); section('Поисковые запросы').append(queries);
  section('Для менеджера').append(node('p', 'commercial', card.commercial_block));
  const warning = node('div', 'warnings'); warning.append(node('p', '', 'Требует проверки перед публикацией'));
  const list = node('ul'); card.missing_fields.forEach(value => list.append(node('li', '', fieldLabel(value) + ': данных нет'))); card.warnings.forEach(value => list.append(node('li', '', value))); warning.append(list); $('preview').append(warning);
  text('json-output', result.exports.json); text('markdown-output', result.exports.markdown);
  text('result-meta', `${saved ? 'Сохранённый пример' : 'Живая генерация'} · ${result.metadata.model} · Попыток: ${result.metadata.attempts} · Токенов: ${result.metadata.usage.total_tokens}. Формат и обозначения проверены; смысл проверяет редактор.`);
  text('result-badge', saved ? 'Сохранённый пример' : 'Черновик готов'); $('result-badge').classList.add('ready');
  show('empty-state', false); show('result'); show('stale-banner', false); show('saved-banner', saved);
  text('saved-banner', 'Это сохранённый результат реального API-запроса, а не новая генерация. ' + origin);
  announce(saved ? 'Открыт сохранённый пример.' : 'Черновик готов. Проверьте текст перед публикацией.');
}
$('product-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.loading) return;
  let product; try { if (!$('form-panel').classList.contains('hidden') && !syncFromForm()) return; product = parseSource(); inputError(); } catch (error) { inputError(error.message); return; }
  const revision = state.revision; state.loading = true; $('generate').disabled = true; text('generate-label', 'Создаём карточку…');
  document.querySelector('.output-panel').setAttribute('aria-busy', 'true'); show('error-banner', false); announce('Генерация выполняется. Обычно это занимает несколько секунд.');
  try {
    const result = await api('/api/generate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(product)});
    render(result); if (state.revision !== revision) { show('stale-banner'); text('result-badge', 'Устарел'); $('result-badge').classList.remove('ready'); }
  } catch (error) { text('error-banner', error.message); show('error-banner'); announce('Генерация не завершена. ' + error.message); }
  finally { state.loading = false; $('generate').disabled = false; text('generate-label', 'Создать карточку'); document.querySelector('.output-panel').setAttribute('aria-busy', 'false'); }
});
function selectedExample() { return state.examples.find(example => example.id === $('example-select').value); }
$('example-select').addEventListener('change', () => { const example = selectedExample(); text('example-origin', example?.origin || 'Выберите пример. Просмотр не вызывает модель.'); });
function loadExample(example) { fillForm(example.input); $('json-input').value = JSON.stringify(example.input, null, 2); inputError(); markChanged(); }
$('load-example').addEventListener('click', () => { const example = selectedExample(); if (example) loadExample(example); else inputError('Сначала выберите пример.'); });
$('show-example').addEventListener('click', async () => {
  const example = selectedExample(); if (!example) { inputError('Сначала выберите пример.'); return; }
  if (state.loading) return;
  try { const body = await api('/api/examples/' + encodeURIComponent(example.id) + '/result'); loadExample(example); render(body.result, true, body.origin); show('error-banner', false); }
  catch (error) { text('error-banner', error.message); show('error-banner'); }
});
$('copy').addEventListener('click', async () => {
  if (!state.result) return;
  const value = state.output === 'preview' ? $('preview').innerText : state.result.exports[state.output];
  try { await navigator.clipboard.writeText(value); text('copy', 'Скопировано'); announce('Результат скопирован.'); setTimeout(() => text('copy', 'Копировать'), 1800); }
  catch { text('error-banner', 'Браузер запретил копирование. Выделите текст вручную или скачайте файл.'); show('error-banner'); }
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
  const results = await Promise.allSettled([api('/api/health'), api('/api/examples')]);
  if (results[0].status === 'fulfilled') { const health = results[0].value; if (!health.ready) { text('config-status', health.message); show('config-status'); } }
  else { text('config-status', 'Нет соединения с сервером. Введённые данные останутся на странице.'); show('config-status'); }
  if (results[1].status === 'fulfilled') { state.examples = results[1].value.examples; state.examples.forEach(example => { const option = node('option', '', example.label); option.value = example.id; $('example-select').append(option); }); }
}
init();
