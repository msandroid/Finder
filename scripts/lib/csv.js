// RFC 4180 形式の CSV 1行を分解する（引用符内のカンマ・"" エスケープに対応）
export function parseCsvLine(line, sep = ',') {
  const out = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else quoted = false;
      } else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === sep) {
      out.push(cur);
      cur = '';
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

// 行の配列（または行を返す iterable）から、ヘッダー名をキーにしたオブジェクトを順に返す
export function* csvRecords(lines, { sep = ',', header } = {}) {
  let cols = header;
  for (let line of lines) {
    if (!line) continue;
    if (!cols) {
      cols = parseCsvLine(line.replace(/^﻿/, ''), sep).map((s) => s.trim());
      continue;
    }
    const v = parseCsvLine(line, sep);
    yield Object.fromEntries(cols.map((c, i) => [c, v[i] ?? '']));
  }
}

export async function* csvRecordsAsync(lines, { sep = ',', header } = {}) {
  let cols = header;
  for await (let line of lines) {
    if (!line) continue;
    if (!cols) {
      cols = parseCsvLine(line.replace(/^﻿/, ''), sep).map((s) => s.trim());
      continue;
    }
    const v = parseCsvLine(line, sep);
    yield Object.fromEntries(cols.map((c, i) => [c, v[i] ?? '']));
  }
}
