import {query, readInput, encodeInput} from './api.js';
import {el, table} from './table.js';

const byId = id => document.getElementById(id);
const money = value => `${value} 元`;
const ok = text => el('span', text, 'state');
const bad = text => el('span', text, 'state problem');

const fields = ['contracts', 'estates', 'balances'];
const uploaded = new Map();
let currentRunId = null;
let preview = null;

function notice(message, error = false) {
  const node = byId('disburse-notice');
  node.value = message;
  node.classList.toggle('problem', error);
}

async function payload() {
  const body = {run_id: currentRunId};
  for (const name of fields) {
    const text = byId(`${name}-csv`).value;
    const original = uploaded.get(name);
    if (original && text === original.text) body[`${name}_base64`] = encodeInput(original.bytes);
    else if (text.length) body[name] = text;
  }
  return body;
}

function kpis(report) {
  const wrap = byId('disburse-kpis');
  wrap.hidden = false;
  wrap.replaceChildren(
    kpi('合同总额', money(report.contract_total)),
    kpi('合格出账', money(report.billed_total)),
    kpi('余额不足楼栋', String(report.shortfall.length), report.shortfall.length > 0),
    kpi('排除议案', String(report.excluded.length), report.excluded.length > 0),
    kpi('总额对平', report.balanced ? '已对平' : '未对平', !report.balanced),
  );
}

function kpi(label, value, problem = false) {
  const node = el('div', undefined, 'kpi');
  node.append(el('small', label), el('strong', value, problem ? 'problem' : ''));
  return node;
}

function section(title, columns, rows) {
  const card = el('section', undefined, 'disburse-section');
  card.append(el('h3', title));
  const body = el('div');
  table(body, columns, rows);
  card.append(body);
  return card;
}

function renderPreview(report) {
  const host = byId('disburse-data');
  host.replaceChildren();
  kpis(report);
  if (report.blockers.length) {
    const banner = el('p', '整批拦截：' + report.blockers.join('；'), 'empty problem');
    host.append(banner);
  }

  const contractCols = [['议案', 'key'], ['合同总价', row => money(row.total)],
    ['性质', row => row.public ? '公共部分' : '专有部分'],
    ['受益楼栋', row => row.paying_buildings.join('、')],
    ['出账合计', row => money(row.billed_total)],
    ['对平', row => row.balanced ? ok('分厘不差') : bad('不平')],
    ['分摊依据', 'reason']];
  host.append(section('合格合同 · 楼栋与户分摊', contractCols, report.contracts));

  const buildingCols = [['楼栋', 'building'], ['建筑面积(㎡)', 'area'],
    ['可用余额', row => money(row.balance)], ['本批分摊', row => money(row.billed)],
    ['资金缺口', row => money(row.gap)],
    ['余额校验', row => row.sufficient ? ok('足额') : bad('不足')], ['原因', 'reason']];
  host.append(section('分楼栋承担与余额', buildingCols, report.buildings));

  const billCols = [['楼栋', 'building'], ['户号', 'unit'], ['业主', 'owner'],
    ['面积(㎡)', 'area'], ['最终承担', row => money(row.amount)],
    ['合同明细', row => row.parts.map(p => `${p.key}：${p.amount} 元`).join('；')]];
  host.append(section('每户最终账单（同业主跨栋另见业主汇总）', billCols, report.bills));

  const ownerCols = [['业主', 'owner'], ['合计承担', row => money(row.total)],
    ['分楼栋', row => row.by_building.map(p => `${p.building}：${p.amount} 元`).join('；')]];
  host.append(section('业主跨楼栋汇总', ownerCols, report.owners));

  if (report.excluded.length) {
    const exclCols = [['议案', 'key'], ['状态', row => row.status === '未通过' ? bad(row.status) : el('span', row.status, 'state problem')],
      ['合同总价', row => money(row.total)], ['不出账原因', 'reason']];
    host.append(section('未通过 / 待核实议案（不出账单）', exclCols, report.excluded));
  }
}

function renderBatches(rows, currentId) {
  const host = byId('disburse-data');
  byId('disburse-kpis').hidden = true;
  if (!rows.length) {
    host.replaceChildren(el('p', '还没有已发布的请款批次', 'empty'));
    return;
  }
  const cols = [['批次号', 'batch_no'], ['来源计票批次', row => row.run_id.slice(0, 9)],
    ['合同总额', row => money(row.contract_total)], ['出账总额', row => money(row.billed_total)],
    ['发布 UTC', 'created'],
    ['操作', row => {
      const button = el('button', '查看整批账单', 'btn');
      button.type = 'button';
      button.addEventListener('click', async () => {
        try {
          const batch = await query(`/api/disburse/${row.batch_no}`);
          renderBatch(batch);
          notice(`批次 ${row.batch_no} 已生效，旧批次账单仍各自保留`);
        } catch (error) { notice(error.message, true); }
      });
      return button;
    }]];
  host.replaceChildren();
  table(host, cols, rows);
}

function renderBatch(batch) {
  const report = batch.report;
  byId('disburse-publish').hidden = true;
  renderPreview(report);
  const stamp = el('p', `批次号 ${batch.batch_no} · 指纹 ${batch.fingerprint.slice(0, 12)} · 已整批生效`, 'empty');
  byId('disburse-data').prepend(stamp);
}

async function loadSamples() {
  const names = fields;
  try {
    const responses = await Promise.all(names.map(name => fetch(`/api/samples/${name}`)));
    if (responses.some(item => !item.ok)) throw Error('样例暂时不可用');
    const buffers = await Promise.all(responses.map(item => item.arrayBuffer()));
    names.forEach((name, index) => {
      const text = new TextDecoder('utf-8').decode(buffers[index]);
      byId(`${name}-csv`).value = text;
      byId(`${name}-file`).value = '';
      uploaded.set(name, {text, bytes: buffers[index]});
    });
    notice('分账样例已载入');
  } catch (error) { notice(error.message, true); }
}

for (const name of fields) {
  byId(`${name}-file`).addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const [text, bytes] = await readInput(file);
      byId(`${name}-csv`).value = text;
      uploaded.set(name, {text, bytes});
      notice(`已读取 ${file.name}`);
    } catch (error) { event.target.value = ''; notice(error.message, true); }
  });
}
byId('disburse-sample').addEventListener('click', loadSamples);

byId('disburse-preview').addEventListener('click', async () => {
  try {
    preview = await query('/api/disburse/preview', await payload());
    renderPreview(preview);
    const button = byId('disburse-publish');
    button.hidden = !preview.ready;
    notice(preview.ready ? '试算通过，可整批发布' : '存在拦截项，按提示处理后才能发布', !preview.ready);
  } catch (error) { notice(error.message, true); }
});

byId('disburse-publish').addEventListener('click', async () => {
  const button = byId('disburse-publish');
  button.disabled = true;
  try {
    const batch = await query('/api/disburse/publish', await payload());
    renderBatch(batch);
    notice(batch.idempotent ? '同批记录重跑：未新增请款，返回已生效批次' : `请款批次 ${batch.batch_no} 整批生效`);
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});

export function showDisburse(runId) {
  currentRunId = runId;
  byId('disburse-run').textContent = runId ? runId.slice(0, 9) : '';
  byId('disburse-run').title = runId || '';
  byId('disburse-preview').disabled = !runId;
  byId('disburse-publish').disabled = !runId;
  if (!runId) {
    byId('disburse-data').replaceChildren(el('p', '请先在「份额计票」运行并选定一个计票批次', 'empty'));
    return;
  }
  query('/api/disburse').then(rows => renderBatches(rows, runId)).catch(error => notice(error.message, true));
}
