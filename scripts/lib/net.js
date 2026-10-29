import { createInterface } from 'node:readline';
import { Readable } from 'node:stream';
import { createGunzip } from 'node:zlib';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class HttpError extends Error {
  constructor(status, url) {
    super(`HTTP ${status}: ${url}`);
    this.status = status;
  }
}

/** 429/5xx とネットワークエラーは指数バックオフで再試行。404 は null を返す */
export async function fetchRetry(url, init = {}, { retries = 4 } = {}) {
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(url, { ...init, headers: { 'User-Agent': 'world-realestate-mcp data builder', ...init.headers } });
    } catch (err) {
      if (attempt >= retries) throw err;
      await sleep(2 ** attempt * 1000);
      continue;
    }
    if (res.status === 404) return null;
    if (res.ok) return res;
    if ((res.status === 429 || res.status >= 500) && attempt < retries) {
      await sleep(2 ** (attempt + 1) * 1000);
      continue;
    }
    throw new HttpError(res.status, url);
  }
}

/** レスポンスを1行ずつ読む（gzip: true で展開しながら）。巨大な CSV もメモリに載せずに処理できる */
export function lines(res, { gzip = false } = {}) {
  let stream = Readable.fromWeb(res.body);
  if (gzip) stream = stream.pipe(createGunzip());
  return createInterface({ input: stream, crlfDelay: Infinity });
}
