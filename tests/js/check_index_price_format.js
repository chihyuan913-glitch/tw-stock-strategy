/**
 * 離線驗收測試腳本：真實讀取 web/index.html 與 lab.html 並提取前端價格格式化函式執行驗證
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const wrkDir = path.resolve(__dirname, '..', '..');
const indexHtmlPath = path.join(wrkDir, 'web', 'index.html');
const labHtmlPath = path.join(wrkDir, 'lab', 'web', 'templates', 'lab.html');

console.log(`[Node Test] WRK: ${wrkDir}`);
console.log(`[Node Test] Reading real index.html: ${indexHtmlPath}`);

const indexHtml = fs.readFileSync(indexHtmlPath, 'utf8');

// 1. 從真實 index.html 提取 formatTwPrice 原始碼
const matchFn = indexHtml.match(/function\s+formatTwPrice\s*\([^)]*\)\s*\{[\s\S]*?\n\s*\}/);
if (!matchFn) {
  console.error('ERROR: Could not find function formatTwPrice in web/index.html');
  process.exit(1);
}

const fnCode = matchFn[0];
console.log('[Node Test] Extracted formatTwPrice function:\n' + fnCode);

// 2. 用 vm 執行
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fnCode, sandbox);

const formatTwPrice = sandbox.formatTwPrice;
if (typeof formatTwPrice !== 'function') {
  console.error('ERROR: formatTwPrice is not a function');
  process.exit(1);
}

// 3. 測試規格對照表
const tableTests = [
  { input: 58.599998474121094, expected: '58.6', name: 'float32 residue' },
  { input: 58.6, expected: '58.6', name: 'normal 58.6' },
  { input: 1440, expected: '1440', name: 'integer 1440' },
  { input: 1440.0, expected: '1440', name: 'float 1440.0' },
  { input: 48.03, expected: '48.03', name: 'cent precision 48.03' },
  { input: 24.23, expected: '24.23', name: 'cent precision 24.23' },
  { input: 0.1 + 0.2, expected: '0.3', name: 'IEEE 0.1+0.2' },
  { input: 30.100000381469727, expected: '30.1', name: 'float32 30.1 residue' },
  { input: 100, expected: '100', name: 'integer 100' },
  { input: 0, expected: '0', name: 'zero' },
  { input: null, expected: '--', name: 'null' },
  { input: undefined, expected: '--', name: 'undefined' },
  { input: '', expected: '--', name: 'empty string' },
  { input: NaN, expected: '--', name: 'NaN' },
  { input: 'abc', expected: '--', name: 'string abc' },
  { input: '58.60', expected: '58.6', name: 'string 58.60' },
];

let failed = 0;
const results = [];

console.log('\n--- web/index.html formatTwPrice Test Results ---');
for (const t of tableTests) {
  const actual = formatTwPrice(t.input);
  const pass = actual === t.expected;
  if (!pass) {
    failed++;
    console.error(`❌ FAIL: ${t.name} (input: ${JSON.stringify(t.input)}) -> Expected: "${t.expected}", Got: "${actual}"`);
  } else {
    console.log(`✓ PASS: ${t.name.padEnd(24)} | input: ${String(t.input).padEnd(20)} -> "${actual}"`);
  }
  results.push({
    name: t.name,
    input: t.input === undefined ? '__UNDEFINED__' : (Number.isNaN(t.input) ? '__NAN__' : t.input),
    expected: t.expected,
    actual,
    pass
  });
}

// 4. 驗證 lab.html 的 formatLabPrice
console.log(`\n[Node Test] Reading real lab.html: ${labHtmlPath}`);
const labHtml = fs.readFileSync(labHtmlPath, 'utf8');
const matchLabFn = labHtml.match(/function\s+formatLabPrice\s*\([^)]*\)\s*\{[\s\S]*?\n\s*\}/);
if (!matchLabFn) {
  console.error('ERROR: Could not find function formatLabPrice in lab.html');
  process.exit(1);
}

vm.runInContext(matchLabFn[0], sandbox);
const formatLabPrice = sandbox.formatLabPrice;
console.log('--- lab.html formatLabPrice Test Results ---');
const labTests = [
  { input: 58.599998474121094, expected: '58.6' },
  { input: 1440, expected: '1440' },
  { input: 48.03, expected: '48.03' },
];

for (const t of labTests) {
  const actual = formatLabPrice(t.input);
  const pass = actual === t.expected;
  if (!pass) {
    failed++;
    console.error(`❌ FAIL lab.html: input ${t.input} -> Expected: "${t.expected}", Got: "${actual}"`);
  } else {
    console.log(`✓ PASS lab.html: input ${String(t.input).padEnd(20)} -> "${actual}"`);
  }
}

// 5. 輸出 JSON 供 Python 測試與比對使用
const outJsonPath = path.join(__dirname, 'node_test_results.json');
fs.writeFileSync(outJsonPath, JSON.stringify({ results, failed, tz: process.env.TZ || 'default' }, null, 2), 'utf8');

if (failed > 0) {
  console.error(`\n[Node Test Completed] Total Failed: ${failed}`);
  process.exit(1);
} else {
  console.log(`\n[Node Test Completed] All ${tableTests.length + labTests.length} tests PASSED!`);
  process.exit(0);
}
